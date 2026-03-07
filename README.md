# 📧 Stain Temp Mail Service

A Telegram bot that gives users real disposable email addresses — powered by 3 free mail providers, no API key needed.

## Features

- 📮 Choose from 3 email services: 1secmail, Guerrilla Mail, mail.tm
- 📬 Check inbox for incoming messages
- 📖 Read full email body inside Telegram
- 🔄 Generate a new address anytime
- 🗑 Delete your inbox on demand
- ⌨️ Inline buttons — no typing needed

## Email Services

| Service | TTL | Notes |
|---|---|---|
| 1secmail.com | Varies | Instant · 7 domains · No signup |
| Guerrilla Mail | ~1 hour | Popular · Session-based · No signup |
| mail.tm | Long | Auto account · Most reliable |

## Commands

| Command | Description |
|---|---|
| `/start` | Welcome message |
| `/new` | Pick a service and generate an address |
| `/check` | Check inbox for new emails |
| `/read N` | Read message number N in full |
| `/delete` | Discard current address |
| `/support` | Contact the owner |
| `/help` | Show all commands |

---

## Deploy: GitHub → Render (Web Service)

### 1. Create your Telegram bot
1. Open Telegram → message **@BotFather**
2. Send `/newbot`, follow the prompts
3. Copy your **BOT_TOKEN**

### 2. Push to GitHub
```bash
cd tempmail_bot
git init
git add .
git commit -m "Initial commit: Stain Temp Mail Bot"

# Create a new repo on github.com, then:
git remote add origin https://github.com/YOUR_USERNAME/stain-tempmail-bot.git
git branch -M main
git push -u origin main
```

### 3. Deploy on Render
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
   - Key: `BOT_TOKEN`
   - Value: *(your token from BotFather)*

> `RENDER_EXTERNAL_URL` and `PORT` are set **automatically** by Render — do not add them yourself.

5. Click **Create Web Service** → wait ~60 seconds for the first deploy

### 4. Verify
- Check Render logs for: `Webhook set →` and `Bot started in webhook mode.`
- Open Telegram → message your bot `/start`

### 5. Future updates
```bash
git add .
git commit -m "describe your change"
git push
# Render auto-redeploys on every push
```

---

## How It Works

```
User sends /new
    → Bot shows 3 service buttons
    → User taps a service
    → Bot creates inbox via that service's API
    → Returns address + inbox buttons

User sends /check
    → Bot fetches messages from the active service
    → Lists subject + sender for each email

User sends /read 1
    → Bot fetches full message body
    → Returns plain-text content in chat
```

## Notes

- Inboxes are stored **in memory** — they reset if the bot restarts. Fine for throwaway addresses.
- None of these addresses should be used for anything sensitive.
- Render's free tier may spin down after inactivity; the webhook re-registers automatically on the next deploy.

## File Structure

```
tempmail_bot/
├── bot.py              # All bot logic (3 services + webhook)
├── requirements.txt
├── Procfile            # Tells Render: python bot.py
├── .python-version     # Python 3.11
├── .gitignore
└── README.md
```

---

## Owner

Built and maintained by **Stain**.

🔗 https://linktr.ee/iamevanss
