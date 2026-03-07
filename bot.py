"""
Telegram Temporary Email Bot - Stain Temp Mail Service
Supports: 1secmail / Guerrilla Mail / mail.tm
Render Web Service (webhook mode)

Env vars:
  BOT_TOKEN           - from @BotFather
  RENDER_EXTERNAL_URL - auto set by Render
  PORT                - auto set by Render
"""

import asyncio
import logging
import os
import random
import re
import string
import threading
from datetime import datetime

import aiohttp
from flask import Flask, jsonify, request
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN        = os.environ["BOT_TOKEN"]
WEBHOOK_URL      = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
PORT             = int(os.environ.get("PORT", 10000))
CHANNEL_USERNAME = "stainprojectss"
CHANNEL_LINK     = "https://t.me/stainprojectss"

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── In-memory store ───────────────────────────────────────────────────────────
inboxes: dict[int, dict] = {}

# ── Flask ─────────────────────────────────────────────────────────────────────
flask_app = Flask(__name__)
ptb_app: Application = None


@flask_app.get("/")
def health():
    return jsonify({"status": "ok", "bot": "stain-tempmail"}), 200


@flask_app.post("/webhook")
def webhook():
    data = request.get_json(force=True)
    asyncio.run_coroutine_threadsafe(
        ptb_app.update_queue.put(Update.de_json(data, ptb_app.bot)),
        ptb_app.bot_data["event_loop"],
    )
    return "ok", 200


# =============================================================================
#  HELPERS
# =============================================================================

def _rand_str(k=10):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=k))


# =============================================================================
#  SERVICE 1 - 1secmail
# =============================================================================

SECMAIL_DOMAINS = [
    "1secmail.com", "1secmail.org", "1secmail.net",
    "wwjmp.com", "esiix.com", "xojxe.com", "yoggm.com",
]


async def secmail_new():
    login = _rand_str(10)
    domain = random.choice(SECMAIL_DOMAINS)
    return {"service": "1secmail", "login": login, "domain": domain, "token": None}


async def secmail_list(box):
    url = (
        "https://www.1secmail.com/api/v1/"
        "?action=getMessages"
        "&login=" + box["login"] +
        "&domain=" + box["domain"]
    )
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                logger.info("1secmail list status: %s", r.status)
                if r.status == 200:
                    data = await r.json()
                    logger.info("1secmail list data: %s", data)
                    return data
    except Exception as e:
        logger.warning("secmail_list error: %s", e)
    return []


async def secmail_read(box, msg_id):
    url = (
        "https://www.1secmail.com/api/v1/"
        "?action=readMessage"
        "&login=" + box["login"] +
        "&domain=" + box["domain"] +
        "&id=" + str(msg_id)
    )
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    return await r.json()
    except Exception as e:
        logger.warning("secmail_read error: %s", e)
    return None


# =============================================================================
#  SERVICE 2 - Guerrilla Mail
# =============================================================================

GUERRILLA_API = "https://api.guerrillamail.com/ajax.php"


async def guerrilla_new():
    params = {"f": "get_email_address", "lang": "en", "sid_token": ""}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                GUERRILLA_API, params=params,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                data = await r.json(content_type=None)
                email = data.get("email_addr", "")
                parts = email.split("@") if "@" in email else [_rand_str(), "guerrillamailblock.com"]
                return {
                    "service": "guerrilla",
                    "login": parts[0],
                    "domain": parts[1],
                    "token": data.get("sid_token", ""),
                }
    except Exception as e:
        logger.warning("guerrilla_new error: %s", e)
        raise


async def guerrilla_list(box):
    params = {"f": "get_email_list", "offset": 0, "seq": 0, "sid_token": box["token"]}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                GUERRILLA_API, params=params,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                logger.info("Guerrilla list status: %s", r.status)
                data = await r.json(content_type=None)
                logger.info("Guerrilla list data: %s", data)
                raw = data.get("list", [])
                if not isinstance(raw, list):
                    return []
                return [
                    {
                        "id": m.get("mail_id"),
                        "from": m.get("mail_from"),
                        "subject": m.get("mail_subject"),
                        "date": m.get("mail_date"),
                    }
                    for m in raw if isinstance(m, dict)
                ]
    except Exception as e:
        logger.warning("guerrilla_list error: %s", e)
    return []


async def guerrilla_read(box, msg_id):
    params = {"f": "fetch_email", "email_id": msg_id, "sid_token": box["token"]}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                GUERRILLA_API, params=params,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                data = await r.json(content_type=None)
                if not data:
                    return None
                return {
                    "subject": data.get("mail_subject"),
                    "from": data.get("mail_from"),
                    "date": data.get("mail_date"),
                    "textBody": re.sub(r"<[^>]+>", "", data.get("mail_body", "")),
                }
    except Exception as e:
        logger.warning("guerrilla_read error: %s", e)
    return None


# =============================================================================
#  SERVICE 3 - mail.tm
# =============================================================================

MAILTM_API = "https://api.mail.tm"


async def mailtm_new():
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                MAILTM_API + "/domains",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                domains_data = await r.json(content_type=None)
                domain = domains_data["hydra:member"][0]["domain"]

            login = _rand_str(12)
            password = _rand_str(16)
            address = login + "@" + domain

            async with s.post(
                MAILTM_API + "/accounts",
                json={"address": address, "password": password},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status not in (200, 201):
                    raise Exception("mail.tm account creation failed: " + str(r.status))

            async with s.post(
                MAILTM_API + "/token",
                json={"address": address, "password": password},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                token_data = await r.json(content_type=None)
                token = token_data.get("token", "")

        return {"service": "mailtm", "login": login, "domain": domain, "token": token}
    except Exception as e:
        logger.warning("mailtm_new error: %s", e)
        raise


async def mailtm_list(box):
    headers = {"Authorization": "Bearer " + box["token"]}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                MAILTM_API + "/messages", headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                logger.info("mail.tm list status: %s", r.status)
                data = await r.json(content_type=None)
                logger.info("mail.tm list data: %s", data)
                raw = data.get("hydra:member", [])
                return [
                    {
                        "id": m.get("id"),
                        "from": m.get("from", {}).get("address", "unknown"),
                        "subject": m.get("subject"),
                        "date": m.get("createdAt", ""),
                    }
                    for m in raw
                ]
    except Exception as e:
        logger.warning("mailtm_list error: %s", e)
    return []


async def mailtm_read(box, msg_id):
    headers = {"Authorization": "Bearer " + box["token"]}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                MAILTM_API + "/messages/" + str(msg_id),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status != 200:
                    return None
                data = await r.json(content_type=None)
                html_body = data.get("html", [""])
                html_text = html_body[0] if isinstance(html_body, list) else html_body
                return {
                    "subject": data.get("subject"),
                    "from": data.get("from", {}).get("address", "unknown"),
                    "date": data.get("createdAt", ""),
                    "textBody": data.get("text") or re.sub(r"<[^>]+>", "", html_text),
                }
    except Exception as e:
        logger.warning("mailtm_read error: %s", e)
    return None


# =============================================================================
#  DISPATCHER
# =============================================================================

async def create_inbox(service):
    if service == "1secmail":
        return await secmail_new()
    elif service == "guerrilla":
        return await guerrilla_new()
    elif service == "mailtm":
        return await mailtm_new()
    raise ValueError("Unknown service: " + service)


async def list_messages(box):
    if box["service"] == "1secmail":
        return await secmail_list(box)
    elif box["service"] == "guerrilla":
        return await guerrilla_list(box)
    elif box["service"] == "mailtm":
        return await mailtm_list(box)
    return []


async def read_message_full(box, msg_id):
    if box["service"] == "1secmail":
        return await secmail_read(box, msg_id)
    elif box["service"] == "guerrilla":
        return await guerrilla_read(box, msg_id)
    elif box["service"] == "mailtm":
        return await mailtm_read(box, msg_id)
    return None


# =============================================================================
#  UI HELPERS
# =============================================================================

SERVICE_LABELS = {
    "1secmail":  "1secmail.com",
    "guerrilla": "Guerrilla Mail",
    "mailtm":    "mail.tm",
}

SERVICE_INFO = {
    "1secmail":  "Instant · No signup · 7 domains",
    "guerrilla": "Popular · No signup · 1 hour TTL",
    "mailtm":    "Reliable · Auto account · Longer TTL",
}


def _service_picker():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "📮 " + SERVICE_LABELS[s] + "  —  " + SERVICE_INFO[s],
            callback_data="svc_" + s
        )]
        for s in ["1secmail", "guerrilla", "mailtm"]
    ])


def _inbox_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📬 Check inbox", callback_data="check"),
            InlineKeyboardButton("🔄 New address", callback_data="new"),
        ],
        [
            InlineKeyboardButton("🗑 Delete inbox", callback_data="delete"),
        ],
    ])


def _fmt_address(user_id):
    box = inboxes.get(user_id)
    if not box:
        return "_no active inbox_"
    svc = SERVICE_LABELS.get(box["service"], box["service"])
    return "`" + box["login"] + "@" + box["domain"] + "`  _(" + svc + ")_"


# =============================================================================
#  JOIN GATE
# =============================================================================

def _join_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Stain Projects", url=CHANNEL_LINK)],
        [InlineKeyboardButton("✅ I have joined — verify me", callback_data="verify_join")],
    ])


async def _is_member(user_id, bot):
    try:
        member = await bot.get_chat_member("@" + CHANNEL_USERNAME, user_id)
        logger.info("Membership check for %s: %s", user_id, member.status)
        return member.status not in ("left", "kicked")
    except Exception as e:
        logger.warning("Membership check failed for %s: %s", user_id, e)
        return True  # fail open so users are not permanently locked out


async def _gate(update, context):
    user = update.effective_user
    msg = update.message or (update.callback_query and update.callback_query.message)
    if await _is_member(user.id, context.bot):
        return True
    text = (
        "👋 Hello *" + user.first_name + "*!\n\n"
        "To use *Stain Temp Mail Service* you must join our channel first.\n\n"
        "1️⃣ Tap the button below to join\n"
        "2️⃣ Come back and tap *I have joined*"
    )
    await msg.reply_text(text, parse_mode="Markdown", reply_markup=_join_keyboard())
    return False


# =============================================================================
#  COMMAND HANDLERS
# =============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await _is_member(user.id, context.bot):
        text = (
            "👋 Hello *" + user.first_name + "*!\n\n"
            "To use *Stain Temp Mail Service* you must join our channel first.\n\n"
            "1️⃣ Tap the button below to join\n"
            "2️⃣ Come back and tap *I have joined*"
        )
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=_join_keyboard())
        return
    text = (
        "👋 Hello *" + user.first_name + "*, welcome to *Stain Temp Mail Service*.\n\n"
        "Use the available commands below to get started:\n\n"
        "📧 /new — Generate a temporary email address\n"
        "📬 /check — Check inbox for new emails\n"
        "📖 /read N — Read message number N in full\n"
        "🗑 /delete — Discard your current address\n"
        "🆘 /support — Get help\n"
        "ℹ️ /help — Show this message"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🆘 *Support*\n\n"
        "Need help? Reach out:\n\n"
        "💬 Telegram: https://t.me/heisevanss\n"
        "🔗 Links: https://linktr.ee/iamevanss"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test all 3 APIs live and show what is reachable."""
    await update.message.reply_text("🔍 Testing all 3 mail APIs, please wait...")
    results = []

    # Test 1secmail
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "https://www.1secmail.com/api/v1/?action=getMessages&login=test&domain=1secmail.com",
                timeout=aiohttp.ClientTimeout(total=8)
            ) as r:
                results.append("✅ *1secmail* — HTTP " + str(r.status))
    except Exception as e:
        results.append("❌ *1secmail* — " + type(e).__name__ + ": " + str(e))

    # Test Guerrilla Mail
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "https://api.guerrillamail.com/ajax.php",
                params={"f": "get_email_address", "lang": "en", "sid_token": ""},
                timeout=aiohttp.ClientTimeout(total=8)
            ) as r:
                data = await r.json(content_type=None)
                email = data.get("email_addr", "none")
                results.append("✅ *Guerrilla Mail* — HTTP " + str(r.status) + " · " + email)
    except Exception as e:
        results.append("❌ *Guerrilla Mail* — " + type(e).__name__ + ": " + str(e))

    # Test mail.tm
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "https://api.mail.tm/domains",
                timeout=aiohttp.ClientTimeout(total=8)
            ) as r:
                data = await r.json(content_type=None)
                domains = [d["domain"] for d in data.get("hydra:member", [])]
                results.append("✅ *mail.tm* — HTTP " + str(r.status) + " · " + str(domains))
    except Exception as e:
        results.append("❌ *mail.tm* — " + type(e).__name__ + ": " + str(e))

    # Show active inbox
    box = inboxes.get(update.effective_user.id)
    if box:
        results.append(
            "\n📬 *Active inbox:*\n"
            "`" + box["login"] + "@" + box["domain"] + "`\n"
            "Service: " + box["service"] + "\n"
            "Token set: " + ("yes" if box.get("token") else "no")
        )
    else:
        results.append("\n📭 No active inbox")

    await update.message.reply_text("\n".join(results), parse_mode="Markdown")


async def new_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _gate(update, context):
        return
    text = (
        "📮 *Choose your email service:*\n\n"
        "Each service creates a real, working inbox.\n"
        "Tap one to generate your address instantly."
    )
    msg = update.message or (update.callback_query and update.callback_query.message)
    await msg.reply_text(text, parse_mode="Markdown", reply_markup=_service_picker())


async def check_inbox(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _gate(update, context):
        return
    user = update.effective_user
    msg = update.message or update.callback_query.message
    box = inboxes.get(user.id)

    if not box:
        await msg.reply_text(
            "❌ You don't have an active inbox yet.\nUse /new to create one.",
            reply_markup=_inbox_keyboard()
        )
        return

    await msg.reply_text("⏳ Checking inbox...")

    try:
        messages = await list_messages(box)
    except Exception as e:
        logger.warning("list_messages error: %s", e)
        await msg.reply_text("❌ Could not reach the mail service. Try again in a moment.")
        return

    if not messages:
        text = (
            "📭 *Inbox empty*\n\n"
            "📧 " + _fmt_address(user.id) + "\n\n"
            "No messages yet. Emails can take up to 30 seconds to arrive."
        )
    else:
        lines = ["📬 *" + str(len(messages)) + " message(s)* in your inbox:\n"]
        for i, m in enumerate(messages, 1):
            subject = m.get("subject") or "(no subject)"
            sender = m.get("from", "unknown")
            date = m.get("date", "")
            lines.append(
                "*" + str(i) + ".* 📩 " + subject +
                "\n    From: `" + str(sender) + "`" +
                "\n    " + str(date)
            )
        lines.append("\n👉 Use /read N to read a message (e.g. /read 1)")
        text = "\n\n".join(lines)

    context.user_data["last_messages"] = messages
    await msg.reply_text(text, parse_mode="Markdown", reply_markup=_inbox_keyboard())


async def read_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _gate(update, context):
        return
    user = update.effective_user
    box = inboxes.get(user.id)

    if not box:
        await update.message.reply_text("❌ No active inbox. Use /new first.")
        return

    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /read N  e.g. /read 1")
        return

    idx = int(args[0]) - 1
    messages = context.user_data.get("last_messages")

    if not messages:
        await update.message.reply_text("📭 Please run /check first to load your inbox.")
        return

    if idx < 0 or idx >= len(messages):
        await update.message.reply_text("❌ Invalid number. You have " + str(len(messages)) + " message(s).")
        return

    msg_id = messages[idx]["id"]
    await update.message.reply_text("⏳ Loading message...")

    try:
        full = await read_message_full(box, msg_id)
    except Exception as e:
        logger.warning("read_message_full error: %s", e)
        await update.message.reply_text("❌ Could not load message. Try again.")
        return

    if not full:
        await update.message.reply_text("❌ Message not found.")
        return

    subject = full.get("subject") or "(no subject)"
    sender = full.get("from", "unknown")
    date = full.get("date", "")
    body = full.get("textBody") or "(empty body)"
    body = re.sub(r"<[^>]+>", "", body).strip()

    if len(body) > 3500:
        body = body[:3500] + "\n\n... (message truncated)"

    text = (
        "📩 *" + subject + "*\n"
        "From: `" + str(sender) + "`\n"
        "Date: " + str(date) + "\n"
        "──────────────────────────────\n\n" +
        body
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=_inbox_keyboard())


async def delete_inbox(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _gate(update, context):
        return
    user = update.effective_user
    msg = update.message or update.callback_query.message

    if user.id in inboxes:
        del inboxes[user.id]
        context.user_data.pop("last_messages", None)
        await msg.reply_text(
            "🗑 *Inbox deleted.*\n\nUse /new to generate a fresh address.",
            parse_mode="Markdown"
        )
    else:
        await msg.reply_text("❌ No active inbox to delete.")


# =============================================================================
#  CALLBACK HANDLER
# =============================================================================

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    action = query.data

    # Verify join
    if action == "verify_join":
        if await _is_member(user.id, context.bot):
            text = (
                "✅ *Verified! Welcome, " + user.first_name + "*.\n\n"
                "You can now use all commands:\n\n"
                "📧 /new — Generate a temporary email address\n"
                "📬 /check — Check inbox for new emails\n"
                "📖 /read N — Read message number N in full\n"
                "🗑 /delete — Discard your current address\n"
                "🆘 /support — Get help"
            )
            await query.edit_message_text(text, parse_mode="Markdown")
        else:
            await query.answer(
                "You have not joined yet! Tap the join button first.",
                show_alert=True
            )

    # Service selection
    elif action.startswith("svc_"):
        service = action.replace("svc_", "")
        label = SERVICE_LABELS.get(service, service)

        await query.edit_message_text(
            "⏳ Creating your *" + label + "* inbox...",
            parse_mode="Markdown"
        )

        try:
            box = await create_inbox(service)
            box["created"] = datetime.now()
            inboxes[user.id] = box
            context.user_data.pop("last_messages", None)

            text = (
                "✅ *Your new inbox is ready!*\n\n"
                "📧 *Address:* `" + box["login"] + "@" + box["domain"] + "`\n"
                "🏷 *Service:* " + label + "\n"
                "ℹ️ " + SERVICE_INFO[service] + "\n\n"
                "Use it anywhere — emails arrive within seconds."
            )
            await query.edit_message_text(
                text, parse_mode="Markdown", reply_markup=_inbox_keyboard()
            )
        except Exception as e:
            logger.error("create_inbox(%s) failed: %s", service, e)
            await query.edit_message_text(
                "❌ *" + label + "* is currently unavailable. Please try another service.",
                parse_mode="Markdown",
                reply_markup=_service_picker()
            )

    elif action == "check":
        await check_inbox(update, context)

    elif action == "new":
        await new_address(update, context)

    elif action == "delete":
        await delete_inbox(update, context)


# =============================================================================
#  MAIN
# =============================================================================

async def main_async():
    global ptb_app

    ptb_app = Application.builder().token(BOT_TOKEN).build()
    ptb_app.bot_data["event_loop"] = asyncio.get_event_loop()

    ptb_app.add_handler(CommandHandler("start",   start))
    ptb_app.add_handler(CommandHandler("help",    help_command))
    ptb_app.add_handler(CommandHandler("support", support))
    ptb_app.add_handler(CommandHandler("new",     new_address))
    ptb_app.add_handler(CommandHandler("check",   check_inbox))
    ptb_app.add_handler(CommandHandler("read",    read_message))
    ptb_app.add_handler(CommandHandler("delete",  delete_inbox))
    ptb_app.add_handler(CommandHandler("debug",   debug))
    ptb_app.add_handler(CallbackQueryHandler(button_callback))

    await ptb_app.bot.set_webhook(WEBHOOK_URL + "/webhook")
    logger.info("Webhook set -> %s/webhook", WEBHOOK_URL)

    await ptb_app.initialize()
    await ptb_app.start()
    logger.info("Bot started in webhook mode.")

    t = threading.Thread(
        target=lambda: flask_app.run(host="0.0.0.0", port=PORT, use_reloader=False),
        daemon=True,
    )
    t.start()
    logger.info("Flask listening on port %s", PORT)

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main_async())
