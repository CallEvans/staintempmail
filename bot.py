"""
Telegram Temporary Email Bot — Stain Temp Mail Service
=======================================================
Supports 3 email providers:
  1. 1secmail.com  — instant, no auth
  2. Guerrilla Mail — well-known, no auth
  3. mail.tm       — requires account creation (handled automatically)

After /new, user picks provider via inline buttons.
Runs as a Render Web Service (webhook mode).

Environment variables (set in Render):
  BOT_TOKEN           — from @BotFather
  RENDER_EXTERNAL_URL — set automatically by Render
  PORT                — set automatically by Render (default 10000)
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
BOT_TOKEN   = os.environ["BOT_TOKEN"]
WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
PORT        = int(os.environ.get("PORT", 10000))
CHANNEL_USERNAME = "stainprojectss"          # without @
CHANNEL_LINK     = "https://t.me/stainprojectss"

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── In-memory store ───────────────────────────────────────────────────────────
# { user_id: { "service": str, "login": str, "domain": str,
#              "token": str|None, "created": datetime } }
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


# ═════════════════════════════════════════════════════════════════════════════
#  SERVICE LAYER — 1secmail / Guerrilla Mail / mail.tm
# ═════════════════════════════════════════════════════════════════════════════

def _rand_str(k=10) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=k))


# ── 1. 1secmail ───────────────────────────────────────────────────────────────
SECMAIL_DOMAINS = ["1secmail.com", "1secmail.org", "1secmail.net",
                   "wwjmp.com", "esiix.com", "xojxe.com", "yoggm.com"]

async def secmail_new() -> dict:
    login  = _rand_str(10)
    domain = random.choice(SECMAIL_DOMAINS)
    return {"service": "1secmail", "login": login, "domain": domain, "token": None}

async def secmail_list(box: dict) -> list[dict]:
    url = (f"https://www.1secmail.com/api/v1/"
           f"?action=getMessages&login={box['login']}&domain={box['domain']}")
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            return await r.json() if r.status == 200 else []

async def secmail_read(box: dict, msg_id: int) -> dict | None:
    url = (f"https://www.1secmail.com/api/v1/"
           f"?action=readMessage&login={box['login']}&domain={box['domain']}&id={msg_id}")
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            return await r.json() if r.status == 200 else None


# ── 2. Guerrilla Mail ─────────────────────────────────────────────────────────
GUERRILLA_API = "https://api.guerrillamail.com/ajax.php"

async def guerrilla_new() -> dict:
    """Create a new Guerrilla Mail inbox and return box dict with session token."""
    params = {"f": "get_email_address", "lang": "en", "sid_token": ""}
    async with aiohttp.ClientSession() as s:
        async with s.get(GUERRILLA_API, params=params,
                         timeout=aiohttp.ClientTimeout(total=10)) as r:
            data  = await r.json(content_type=None)
            email = data.get("email_addr", "")
            parts = email.split("@") if "@" in email else [_rand_str(), "guerrillamailblock.com"]
            return {
                "service": "guerrilla",
                "login":   parts[0],
                "domain":  parts[1],
                "token":   data.get("sid_token", ""),
            }

async def guerrilla_list(box: dict) -> list[dict]:
    params = {"f": "get_email_list", "offset": 0, "sid_token": box["token"]}
    async with aiohttp.ClientSession() as s:
        async with s.get(GUERRILLA_API, params=params,
                         timeout=aiohttp.ClientTimeout(total=8)) as r:
            data = await r.json(content_type=None)
            raw  = data.get("list", [])
            # Normalise to our format
            return [
                {
                    "id":      m.get("mail_id"),
                    "from":    m.get("mail_from"),
                    "subject": m.get("mail_subject"),
                    "date":    m.get("mail_date"),
                }
                for m in raw
            ]

async def guerrilla_read(box: dict, msg_id) -> dict | None:
    params = {"f": "fetch_email", "email_id": msg_id, "sid_token": box["token"]}
    async with aiohttp.ClientSession() as s:
        async with s.get(GUERRILLA_API, params=params,
                         timeout=aiohttp.ClientTimeout(total=8)) as r:
            data = await r.json(content_type=None)
            if not data:
                return None
            return {
                "subject":  data.get("mail_subject"),
                "from":     data.get("mail_from"),
                "date":     data.get("mail_date"),
                "textBody": re.sub(r"<[^>]+>", "", data.get("mail_body", "")),
            }


# ── 3. mail.tm ────────────────────────────────────────────────────────────────
MAILTM_API = "https://api.mail.tm"

async def mailtm_new() -> dict:
    """Register a new mail.tm account and return box dict with JWT token."""
    async with aiohttp.ClientSession() as s:
        # 1. Get available domains
        async with s.get(f"{MAILTM_API}/domains",
                         timeout=aiohttp.ClientTimeout(total=10)) as r:
            domains_data = await r.json(content_type=None)
            domain = domains_data["hydra:member"][0]["domain"]

        login    = _rand_str(12)
        password = _rand_str(16)
        address  = f"{login}@{domain}"

        # 2. Create account
        async with s.post(f"{MAILTM_API}/accounts",
                          json={"address": address, "password": password},
                          timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status not in (200, 201):
                raise Exception(f"mail.tm account creation failed: {r.status}")

        # 3. Get JWT token
        async with s.post(f"{MAILTM_API}/token",
                          json={"address": address, "password": password},
                          timeout=aiohttp.ClientTimeout(total=10)) as r:
            token_data = await r.json(content_type=None)
            token = token_data.get("token", "")

        return {"service": "mailtm", "login": login, "domain": domain, "token": token}

async def mailtm_list(box: dict) -> list[dict]:
    headers = {"Authorization": f"Bearer {box['token']}"}
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{MAILTM_API}/messages", headers=headers,
                         timeout=aiohttp.ClientTimeout(total=8)) as r:
            data = await r.json(content_type=None)
            raw  = data.get("hydra:member", [])
            return [
                {
                    "id":      m.get("id"),
                    "from":    m.get("from", {}).get("address", "unknown"),
                    "subject": m.get("subject"),
                    "date":    m.get("createdAt", ""),
                }
                for m in raw
            ]

async def mailtm_read(box: dict, msg_id: str) -> dict | None:
    headers = {"Authorization": f"Bearer {box['token']}"}
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{MAILTM_API}/messages/{msg_id}", headers=headers,
                         timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status != 200:
                return None
            data = await r.json(content_type=None)
            return {
                "subject":  data.get("subject"),
                "from":     data.get("from", {}).get("address", "unknown"),
                "date":     data.get("createdAt", ""),
                "textBody": data.get("text") or re.sub(r"<[^>]+>", "", data.get("html", [""])[0]),
            }


# ── Dispatcher ────────────────────────────────────────────────────────────────

async def create_inbox(service: str) -> dict:
    if service == "1secmail":
        return await secmail_new()
    elif service == "guerrilla":
        return await guerrilla_new()
    elif service == "mailtm":
        return await mailtm_new()
    raise ValueError(f"Unknown service: {service}")

async def list_messages(box: dict) -> list[dict]:
    if box["service"] == "1secmail":
        return await secmail_list(box)
    elif box["service"] == "guerrilla":
        return await guerrilla_list(box)
    elif box["service"] == "mailtm":
        return await mailtm_list(box)
    return []

async def read_message_full(box: dict, msg_id) -> dict | None:
    if box["service"] == "1secmail":
        return await secmail_read(box, msg_id)
    elif box["service"] == "guerrilla":
        return await guerrilla_read(box, msg_id)
    elif box["service"] == "mailtm":
        return await mailtm_read(box, msg_id)
    return None


# ═════════════════════════════════════════════════════════════════════════════
#  UI HELPERS
# ═════════════════════════════════════════════════════════════════════════════

SERVICE_LABELS = {
    "1secmail":  "1secmail.com",
    "guerrilla": "Guerrilla Mail",
    "mailtm":    "mail.tm",
}

SERVICE_INFO = {
    "1secmail":  "⚡ Instant · No signup · 7 domains",
    "guerrilla": "🦍 Popular · No signup · 1 hour TTL",
    "mailtm":    "🔒 Reliable · Auto account · Longer TTL",
}

def _service_picker() -> InlineKeyboardMarkup:
    """Inline keyboard to pick a service after /new."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"📮 {SERVICE_LABELS[s]}  —  {SERVICE_INFO[s]}",
            callback_data=f"svc_{s}"
        )]
        for s in ["1secmail", "guerrilla", "mailtm"]
    ])

def _inbox_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📬 Check inbox", callback_data="check"),
            InlineKeyboardButton("🔄 New address",  callback_data="new"),
        ],
        [
            InlineKeyboardButton("🗑 Delete inbox", callback_data="delete"),
        ],
    ])

def _fmt_address(user_id: int) -> str:
    box = inboxes.get(user_id)
    if not box:
        return "_no active inbox_"
    svc = SERVICE_LABELS.get(box["service"], box["service"])
    return f"`{box['login']}@{box['domain']}`  _(via {svc})_"


# ═════════════════════════════════════════════════════════════════════════════
#  JOIN GATE
# ═════════════════════════════════════════════════════════════════════════════

def _join_keyboard() -> InlineKeyboardMarkup:
    """Button to open the channel + a verify button."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Stain Projects", url=CHANNEL_LINK)],
        [InlineKeyboardButton("✅ I've joined — verify me", callback_data="verify_join")],
    ])


async def _is_member(user_id: int, bot) -> bool:
    """Return True if user is a member/admin of the channel."""
    try:
        member = await bot.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning(f"Membership check failed: {e}")
        return False


async def _gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Call at the top of every handler.
    Returns True if user is allowed through, False if they were shown the join prompt.
    """
    user = update.effective_user
    msg  = update.message or (update.callback_query and update.callback_query.message)

    if await _is_member(user.id, context.bot):
        return True

    text = (
        f"👋 Hello *{user.first_name}*!\n\n"
        f"To use *Stain Temp Mail Service* you must join our channel first.\n\n"
        f"1️⃣ Tap the button below to join\n"
        f"2️⃣ Come back and tap *I\'ve joined*"
    )
    await msg.reply_text(text, parse_mode="Markdown", reply_markup=_join_keyboard())
    return False


# ═════════════════════════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ═════════════════════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # Show join gate if not a member; gate() sends the prompt itself
    if not await _is_member(user.id, context.bot):
        text = (
            f"👋 Hello *{user.first_name}*!\n\n"
            f"To use *Stain Temp Mail Service* you must join our channel first.\n\n"
            f"1️⃣ Tap the button below to join\n"
            f"2️⃣ Come back and tap *I\'ve joined*"
        )
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=_join_keyboard())
        return
    text = (
        f"👋 Hello *{user.first_name}*, welcome to *Stain Temp Mail Service*.\n\n"
        f"Use the available commands below to get started:\n\n"
        f"📧 /new — Generate a temporary email address\n"
        f"📬 /check — Check inbox for new emails\n"
        f"📖 /read `N` — Read message number N in full\n"
        f"🗑 /delete — Discard your current address\n"
        f"🆘 /support — Get help\n"
        f"ℹ️ /help — Show this message"
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


async def new_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show service picker."""
    if not await _gate(update, context): return
    text = (
        "📮 *Choose your email service:*\n\n"
        "Each service creates a real, working inbox.\n"
        "Tap one to generate your address instantly."
    )
    msg = update.message or (update.callback_query and update.callback_query.message)
    await msg.reply_text(text, parse_mode="Markdown", reply_markup=_service_picker())


async def check_inbox(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg  = update.message or update.callback_query.message
    box  = inboxes.get(user.id)

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
        logger.warning(f"list_messages error: {e}")
        await msg.reply_text("❌ Couldn't reach the mail service. Try again in a moment.")
        return

    if not messages:
        text = (
            f"📭 *Inbox empty*\n\n"
            f"📧 {_fmt_address(user.id)}\n\n"
            f"No messages yet. Emails can take up to 30 seconds to arrive."
        )
    else:
        lines = [f"📬 *{len(messages)} message(s)* in your inbox:\n"]
        for i, m in enumerate(messages, 1):
            subject = m.get("subject") or "(no subject)"
            sender  = m.get("from", "unknown")
            date    = m.get("date", "")
            lines.append(f"*{i}.* 📩 {subject}\n    From: `{sender}`\n    {date}")
        lines.append("\n👉 Use /read `N` to read a message (e.g. `/read 1`)")
        text = "\n\n".join(lines)

    context.user_data["last_messages"] = messages
    await msg.reply_text(text, parse_mode="Markdown", reply_markup=_inbox_keyboard())


async def read_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _gate(update, context): return
    user = update.effective_user
    box  = inboxes.get(user.id)

    if not box:
        await update.message.reply_text("❌ No active inbox. Use /new first.")
        return

    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text(
            "Usage: /read `N` — e.g. `/read 1`", parse_mode="Markdown"
        )
        return

    idx      = int(args[0]) - 1
    messages = context.user_data.get("last_messages")

    if not messages:
        await update.message.reply_text("📭 Please run /check first to load your inbox.")
        return

    if idx < 0 or idx >= len(messages):
        await update.message.reply_text(f"❌ Invalid number. You have {len(messages)} message(s).")
        return

    msg_id = messages[idx]["id"]
    await update.message.reply_text("⏳ Loading message...")

    try:
        full = await read_message_full(box, msg_id)
    except Exception as e:
        logger.warning(f"read_message_full error: {e}")
        await update.message.reply_text("❌ Couldn't load message. Try again.")
        return

    if not full:
        await update.message.reply_text("❌ Message not found.")
        return

    subject = full.get("subject") or "(no subject)"
    sender  = full.get("from", "unknown")
    date    = full.get("date", "")
    body    = full.get("textBody") or "(empty body)"
    body    = re.sub(r"<[^>]+>", "", body).strip()

    if len(body) > 3500:
        body = body[:3500] + "\n\n... _(message truncated)_"

    text = (
        f"📩 *{subject}*\n"
        f"From: `{sender}`\n"
        f"Date: {date}\n"
        f"{'─' * 30}\n\n"
        f"{body}"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=_inbox_keyboard())


async def delete_inbox(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _gate(update, context): return
    user = update.effective_user
    msg  = update.message or update.callback_query.message

    if user.id in inboxes:
        del inboxes[user.id]
        context.user_data.pop("last_messages", None)
        await msg.reply_text(
            "🗑 *Inbox deleted.*\n\nUse /new to generate a fresh address.",
            parse_mode="Markdown"
        )
    else:
        await msg.reply_text("❌ No active inbox to delete.")


# ═════════════════════════════════════════════════════════════════════════════
#  CALLBACK HANDLER
# ═════════════════════════════════════════════════════════════════════════════

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user   = update.effective_user
    action = query.data

    # ── Service selection ────────────────────────────────────────────────────
    if action.startswith("svc_"):
        service = action.replace("svc_", "")
        label   = SERVICE_LABELS.get(service, service)

        await query.edit_message_text(
            f"⏳ Creating your *{label}* inbox...",
            parse_mode="Markdown"
        )

        try:
            box = await create_inbox(service)
            box["created"] = datetime.now()
            inboxes[user.id] = box
            context.user_data.pop("last_messages", None)

            text = (
                f"✅ *Your new inbox is ready!*\n\n"
                f"📧 *Address:* `{box['login']}@{box['domain']}`\n"
                f"🏷 *Service:* {label}\n"
                f"ℹ️ {SERVICE_INFO[service]}\n\n"
                f"Use it anywhere — emails arrive within seconds."
            )
            await query.edit_message_text(
                text, parse_mode="Markdown", reply_markup=_inbox_keyboard()
            )

        except Exception as e:
            logger.error(f"create_inbox({service}) failed: {e}")
            await query.edit_message_text(
                f"❌ *{label}* is currently unavailable. Please try another service.",
                parse_mode="Markdown",
                reply_markup=_service_picker()
            )

    # ── Verify join ──────────────────────────────────────────────────────────
    elif action == "verify_join":
        if await _is_member(user.id, context.bot):
            await query.edit_message_text(
                f"✅ *Verified!* Welcome, {user.first_name}.\n\n"
                f"You can now use all commands:\n\n"
                f"📧 /new — Generate a temporary email address\n"
                f"📬 /check — Check inbox for new emails\n"
                f"📖 /read `N` — Read message number N in full\n"
                f"🗑 /delete — Discard your current address\n"
                f"🆘 /support — Get help",
                parse_mode="Markdown"
            )
        else:
            await query.answer(
                "❌ You haven't joined yet! Tap the join button first.", show_alert=True
            )

    # ── Inbox actions ────────────────────────────────────────────────────────
    elif action == "check":
        await check_inbox(update, context)

    elif action == "new":
        await new_address(update, context)

    elif action == "delete":
        await delete_inbox(update, context)


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═════════════════════════════════════════════════════════════════════════════

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
    ptb_app.add_handler(CallbackQueryHandler(button_callback))

    await ptb_app.bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    logger.info(f"Webhook set → {WEBHOOK_URL}/webhook")

    await ptb_app.initialize()
    await ptb_app.start()
    logger.info("Bot started in webhook mode.")

    t = threading.Thread(
        target=lambda: flask_app.run(host="0.0.0.0", port=PORT, use_reloader=False),
        daemon=True,
    )
    t.start()
    logger.info(f"Flask listening on port {PORT}")

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main_async())
