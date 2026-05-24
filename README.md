# Caldy Bot

A Telegram bot that sends your Google Calendar agenda every morning. Integrates with Todoist for task management and OpenAI Whisper for voice transcription.

**Features:**
- Daily agenda at scheduled time
- Natural language calendar queries (via AI)
- Todoist task integration
- Voice message transcription
- Google Calendar event management

## Requirements

- Python 3.10+
- Google Cloud project
- Telegram bot token

## Installation

### 1. Clone and set up environment

```bash
cd /opt/caldy-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

Create `.env` file:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_ALLOWED_CHAT_ID=your_chat_id
ANTHROPIC_API_KEY=your_anthropic_key  # or OPENAI_API_KEY
TODOIST_API_TOKEN=your_todoist_token  # optional
```

Create `config.toml`:

```toml
model = "anthropic:claude-haiku-4-5"  # or openai:gpt-4o
send_time = "08:00"
timezone = "Europe/Berlin"
system_prompt = "You are a helpful assistant..."
```

### 3. Google Calendar Setup (Service Account — Recommended)

This method provides permanent access without token refresh.

**Create Service Account:**
1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Select your project → **IAM & Admin** → **Service Accounts**
3. Create new service account (e.g., `caldy-bot`)
4. Download JSON key file

**Grant calendar access:**
1. Open Google Calendar → settings → your calendar
2. **Share with specific people**
3. Add service account email (from JSON: `client_email`)
4. Role: **Make changes to events**

**Install credentials:**

```bash
mkdir -p .google_secrets
mv /path/to/downloaded-key.json .google_secrets/credentials.json
chmod 600 .google_secrets/credentials.json
```

The bot will automatically use Service Account auth. No `token.json` needed.

### 4. Install Systemd Service (Optional)

```bash
sudo nano /etc/systemd/system/caldy-bot.service
```

```ini
[Unit]
Description=Caldy Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/caldy-bot
Environment="PATH=/opt/caldy-bot/.venv/bin"
ExecStart=/opt/caldy-bot/.venv/bin/python3 telegram_bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable caldy-bot
sudo systemctl start caldy-bot
```

## Todoist Integration (Optional)

For task management alongside calendar:

1. Get your Todoist API token from [todoist.com/settings/integrations/developer](https://todoist.com/settings/integrations/developer)
2. Add to `.env`:

```env
TODOIST_API_TOKEN=your_todoist_token
```

3. In `integrations/todoist.py`, ensure `FEATURE_FLAG = True`

The bot will pull tasks from Todoist and include them in the daily agenda.

## OpenAI Whisper (Optional)

For voice message transcription:

1. The bot uses OpenAI Whisper for speech-to-text
2. Ensure `OPENAI_API_KEY` is set in `.env` (can be combined with Anthropic for AI):

```env
OPENAI_API_KEY=your_openai_key
```

Note: OpenAI is used only for Whisper transcription, not for the main AI model.

## Troubleshooting

**Bot not responding:**
```bash
journalctl -u caldy-bot -n 50
```

**Check Google Calendar access:**
```bash
cd /opt/caldy-bot
.venv/bin/python3 -c "
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
creds = Credentials.from_service_account_file('.google_secrets/credentials.json', scopes=['https://www.googleapis.com/auth/calendar'])
print('OK' if creds else 'FAIL')
"
```