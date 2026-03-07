# 📧 Stain Temp Mail Service

A Telegram bot that gives users real disposable email addresses powered by [mail.tm](https://mail.tm) — free, no API key needed, runs 24/7 on Render's free tier.

## Features

- 📧 Instant disposable email address via /new
- 📬 Check inbox for incoming messages
- 📖 Read full email body inside Telegram
- 🔄 Generate a new address anytime
- 🗑 Delete your inbox on demand
- 🔒 Join gate — users must join your channel to use the bot
- ⌨️ Inline buttons — no typing needed

## Commands

| Command | Description |
|---|---|
| `/start` | Welcome message |
| `/new` | Generate a fresh email address |
| `/check` | Check inbox for new emails |
| `/read N` | Read message number N in full |
| `/delete` | Discard current address |
| `/support` | Contact the owner |
| `/help` | Show all commands |

---

## Deploy: GitHub → Render (Web Service)

### 1. Create your Telegram bot
1. Open Telegram → message **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy your **BOT_TOKEN**

### 2. Add your bot as admin of your channel
1. Open `@stainprojectss` in Telegram
2. Channel Settings → Administrators → Add Administrator
3. Search your bot username → Add it
4. Read-only permissions are enough

### 3. Push to GitHub
```bash
cd tempmail_bot
git init
git add .
git commit -m "Initial commit: Stain Temp Mail Bot"

# Create a new repo on github.com then:
git remote add origin https://github.com/YOUR_USERNAME/stain-tempmail-bot.git
git branch -M main
git push -u origin main
```

### 4. Deploy on Render
1. Go to [render.com](https://render.com) → **New** → **Web Service**
2. Connect GitHub → select your repo
3. Fill in settings:

| Field | Value |
|---|---|
| **Name** | `stain-tempmail-bot` |
| **Environment** | `Python 3` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `python bot.py` |
| **Instance Type** | `Free` |

4. Add **Environment Variable**:

| Key | Value |
|---|---|
| `BOT_TOKEN` | your token from @BotFather |

> `PORT` and `RENDER_EXTERNAL_URL` are set **automatically** by Render — do not add them.

5. Click **Create Web Service** → wait ~60 seconds for the first deploy

### 5. Verify it's live
Check Render logs for:
```
Webhook set -> https://your-app.onrender.com/webhook
Bot started in webhook mode.
```
Then message your bot `/start` on Telegram.

### 6. Future updates
```bash
git add .
git commit -m "describe your change"
git push
```
Render auto-redeploys on every push.

---

## How It Works

```
User sends /start
    → Bot checks if user has joined @stainprojectss
    → If not, shows join button + verify button
    → Once verified, shows welcome message

User sends /new
    → Bot registers a new account on mail.tm
    → Returns a real working email address

User sends /check
    → Bot fetches messages from mail.tm API
    → Lists subject + sender for each email

User sends /read 1
    → Bot fetches full message body from mail.tm
    → Returns plain-text content in chat
```

## Why only mail.tm?

Three services were tested on Render's free tier:

| Service | Result |
|---|---|
| 1secmail | ❌ HTTP 403 — Render's IP is blocked |
| Guerrilla Mail | ❌ Times out on active sessions |
| mail.tm | ✅ Works reliably |

## Notes

- Inboxes are stored **in memory** — they reset if the bot restarts. This is fine for throwaway addresses; just use `/new` to get a fresh one.
- Do not use these addresses for anything sensitive or important.
- mail.tm tokens can expire over time — if `/check` returns a token error, just run `/new` again.

## File Structure

```
tempmail_bot/
├── bot.py              # All bot logic (mail.tm + webhook + join gate)
├── requirements.txt    # python-telegram-bot, aiohttp, flask
├── Procfile            # Tells Render: python bot.py
├── .python-version     # Python 3.11
├── .gitignore
└── README.md
```

---

## Owner

Built and maintained by **Stain**.

🔗 https://linktr.ee/iamevanss
