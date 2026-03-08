"""
Telegram Temporary Email Bot - Stain Temp Mail Service
Provider: mail.tm (only reliable one on Render free tier)
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
MAILTM_API       = "https://api.mail.tm"

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── In-memory store ───────────────────────────────────────────────────────────
# { user_id: { "login": str, "domain": str, "token": str, "created": datetime } }
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
#  mail.tm API
# =============================================================================

def _rand_str(k=12):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=k))


async def mailtm_create():
    """Create a new mail.tm account and return inbox dict."""
    async with aiohttp.ClientSession() as s:
        # 1. Get an available domain
        async with s.get(
            MAILTM_API + "/domains",
            timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            data = await r.json(content_type=None)
            domain = data["hydra:member"][0]["domain"]

        login    = _rand_str(12)
        password = _rand_str(16)
        address  = login + "@" + domain

        # 2. Register account
        async with s.post(
            MAILTM_API + "/accounts",
            json={"address": address, "password": password},
            timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            if r.status not in (200, 201):
                body = await r.text()
                raise Exception("Account creation failed: " + str(r.status) + " " + body)

        # 3. Get JWT token
        async with s.post(
            MAILTM_API + "/token",
            json={"address": address, "password": password},
            timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            token_data = await r.json(content_type=None)
            token = token_data.get("token", "")
            if not token:
                raise Exception("Token not returned by mail.tm")

    return {
        "login":    login,
        "domain":   domain,
        "password": password,
        "token":    token,
        "created":  datetime.now(),
    }


async def mailtm_list(box):
    """Return list of messages in the inbox. Auto-refreshes token on 401."""
    for attempt in range(2):
        headers = {"Authorization": "Bearer " + box["token"]}
        async with aiohttp.ClientSession() as s:
            async with s.get(
                MAILTM_API + "/messages",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                logger.info("mail.tm list status: %s", r.status)
                if r.status == 401:
                    if attempt == 0:
                        logger.info("Token expired, refreshing...")
                        await mailtm_refresh_token(box)
                        continue  # retry with new token
                    raise Exception("Session expired. Use /new to get a fresh address.")
                data = await r.json(content_type=None)
                raw  = data.get("hydra:member", [])
                return [
                    {
                        "id":      m.get("id"),
                        "from":    m.get("from", {}).get("address", "unknown"),
                        "subject": m.get("subject") or "(no subject)",
                        "date":    m.get("createdAt", ""),
                    }
                    for m in raw
                ]
    return []


async def mailtm_read(box, msg_id):
    """Return full message content. Auto-refreshes token on 401."""
    for attempt in range(2):
        headers = {"Authorization": "Bearer " + box["token"]}
        async with aiohttp.ClientSession() as s:
            async with s.get(
                MAILTM_API + "/messages/" + str(msg_id),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status == 401:
                    if attempt == 0:
                        logger.info("Token expired on read, refreshing...")
                        await mailtm_refresh_token(box)
                        continue
                    raise Exception("Session expired. Use /new to get a fresh address.")
                if r.status != 200:
                    return None
                data = await r.json(content_type=None)
                html_body = data.get("html", [""])
                html_text = html_body[0] if isinstance(html_body, list) else html_body
                return {
                    "subject":  data.get("subject") or "(no subject)",
                    "from":     data.get("from", {}).get("address", "unknown"),
                    "date":     data.get("createdAt", ""),
                    "textBody": data.get("text") or re.sub(r"<[^>]+>", "", html_text),
                }
    return None


async def mailtm_refresh_token(box):
    """Re-authenticate and update the token in the box dict in place."""
    async with aiohttp.ClientSession() as s:
        async with s.post(
            MAILTM_API + "/token",
            json={"address": box["login"] + "@" + box["domain"], "password": box["password"]},
            timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            if r.status != 200:
                raise Exception("Could not refresh token: " + str(r.status))
            data = await r.json(content_type=None)
            token = data.get("token", "")
            if not token:
                raise Exception("No token returned during refresh.")
            box["token"] = token
            logger.info("Token refreshed for %s@%s", box["login"], box["domain"])
            return token


# =============================================================================
#  UI HELPERS
# =============================================================================

def _inbox_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📬 Check inbox", callback_data="check"),
            InlineKeyboardButton("🔄 New address",  callback_data="new"),
        ],
        [
            InlineKeyboardButton("🗑 Delete inbox", callback_data="delete"),
        ],
    ])


def _fmt_address(user_id):
    box = inboxes.get(user_id)
    if not box:
        return "_no active inbox_"
    return "`" + box["login"] + "@" + box["domain"] + "`"


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
        logger.info("Membership %s: %s", user_id, member.status)
        return member.status not in ("left", "kicked")
    except Exception as e:
        logger.warning("Membership check error for %s: %s", user_id, e)
        return True  # fail open


async def _gate(update, context):
    user = update.effective_user
    msg  = update.message or (update.callback_query and update.callback_query.message)
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


async def new_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _gate(update, context):
        return
    msg = update.message or (update.callback_query and update.callback_query.message)
    await msg.reply_text("⏳ Creating your inbox, please wait...")
    try:
        box = await mailtm_create()
        inboxes[update.effective_user.id] = box
        context.user_data.pop("last_messages", None)
        text = (
            "✅ *Your inbox is ready!*\n\n"
            "📧 *Address:* `" + box["login"] + "@" + box["domain"] + "`\n\n"
            "Use this address anywhere.\n"
            "Tap *Check inbox* below when you expect an email."
        )
        await msg.reply_text(text, parse_mode="Markdown", reply_markup=_inbox_keyboard())
    except Exception as e:
        logger.error("mailtm_create failed: %s", e)
        await msg.reply_text(
            "❌ Could not create inbox right now. Please try again in a moment."
        )


async def check_inbox(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _gate(update, context):
        return
    user = update.effective_user
    msg  = update.message or update.callback_query.message
    box  = inboxes.get(user.id)

    if not box:
        await msg.reply_text(
            "❌ You don't have an active inbox yet. Use /new to create one."
        )
        return

    await msg.reply_text("⏳ Checking inbox...")

    try:
        messages = await mailtm_list(box)
    except Exception as e:
        await msg.reply_text("❌ " + str(e))
        return

    if not messages:
        text = (
            "📭 *Inbox empty*\n\n"
            "📧 " + _fmt_address(user.id) + "\n\n"
            "No messages yet. Emails usually arrive within 30 seconds.\n"
            "Make sure you sent the email to the exact address above."
        )
    else:
        lines = ["📬 *" + str(len(messages)) + " message(s)* in your inbox:\n"]
        for i, m in enumerate(messages, 1):
            lines.append(
                "*" + str(i) + ".* 📩 " + m["subject"] +
                "\n    From: `" + str(m["from"]) + "`" +
                "\n    " + str(m["date"])
            )
        lines.append("\n👉 Use /read N to read (e.g. /read 1)")
        text = "\n\n".join(lines)

    context.user_data["last_messages"] = messages
    await msg.reply_text(text, parse_mode="Markdown", reply_markup=_inbox_keyboard())


async def read_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _gate(update, context):
        return
    user = update.effective_user
    box  = inboxes.get(user.id)

    if not box:
        await update.message.reply_text("❌ No active inbox. Use /new first.")
        return

    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /read N  e.g. /read 1")
        return

    messages = context.user_data.get("last_messages")
    if not messages:
        await update.message.reply_text("📭 Please run /check first.")
        return

    idx = int(args[0]) - 1
    if idx < 0 or idx >= len(messages):
        await update.message.reply_text(
            "❌ Invalid number. You have " + str(len(messages)) + " message(s)."
        )
        return

    await update.message.reply_text("⏳ Loading message...")

    try:
        full = await mailtm_read(box, messages[idx]["id"])
    except Exception as e:
        await update.message.reply_text("❌ " + str(e))
        return

    if not full:
        await update.message.reply_text("❌ Message not found.")
        return

    body = full.get("textBody") or "(empty body)"
    body = re.sub(r"<[^>]+>", "", body).strip()
    if len(body) > 3500:
        body = body[:3500] + "\n\n... (message truncated)"

    text = (
        "📩 *" + full["subject"] + "*\n"
        "From: `" + str(full["from"]) + "`\n"
        "Date: " + str(full["date"]) + "\n"
        "──────────────────────────────\n\n" +
        body
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=_inbox_keyboard())


async def delete_inbox(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _gate(update, context):
        return
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


# =============================================================================
#  CALLBACK HANDLER
# =============================================================================

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    action = query.data

    if action == "verify_join":
        user = update.effective_user
        if await _is_member(user.id, context.bot):
            text = (
                "✅ *Verified! Welcome, " + user.first_name + "*.\n\n"
                "Use the available commands:\n\n"
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
