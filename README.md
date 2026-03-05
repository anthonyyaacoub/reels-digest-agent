# Reels Digest Agent

An automated personal knowledge pipeline for Instagram Reels. Save any reel you like via a Telegram bot, and every morning the agent downloads it, transcribes the audio locally with OpenAI Whisper, summarises the key points with Claude AI, and publishes a clean digest page to Notion — complete with timestamped transcripts, key takeaways, and action items.

**The full loop:**
1. You see a reel worth saving → copy the link → paste it into your Telegram bot
2. Every day at 8am (or whenever you want) the agent processes everything
3. A new page appears in your Notion database with summaries for every reel

---

## How it works

```
Instagram Reel URL
       │
       ▼
  Telegram Bot  ──────────────────┐
  (or Web UI)                     │
                                  ▼
                            SQLite Queue
                                  │
                    ┌─────────────┘
                    ▼
              yt-dlp download
                    │
                    ▼
          Whisper transcription
          (runs locally, no API)
                    │
                    ▼
          Claude summarization
          (title + takeaways + actions)
                    │
                    ▼
           Notion page published
```

---

## Prerequisites

- **Python 3.11+**
- **ffmpeg** — required by yt-dlp and Whisper for audio processing
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt install ffmpeg`
  - Windows: `winget install --id Gyan.FFmpeg`
- A free **Telegram account** (to create the bot)
- An **Anthropic API key** — [console.anthropic.com](https://console.anthropic.com)
- A **Notion account** with API access

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/reels-digest-agent.git
cd reels-digest-agent

python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

pip install -r requirements.txt
pip install python-telegram-bot>=20.0
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in the required values (see sections below for how to get each one).

---

## Getting your API keys

### Anthropic (Claude)
1. Go to [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys)
2. Create a new key → copy it → paste as `ANTHROPIC_API_KEY`

### Notion
1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations) → **New integration**
2. Name it (e.g. "Reels Digest"), select your workspace → **Save**
3. Copy the **Internal Integration Secret** → paste as `NOTION_API_KEY`
4. Create a new **full-page database** in Notion. Add a `Date` property (type: Date).
5. Click **Share** on the database → **Connect to** → select your integration
6. Copy the **Database ID** from the URL:
   ```
   https://notion.so/yourworkspace/THIS-32-CHAR-STRING?v=...
   ```
   Paste as `NOTION_DATABASE_ID`

### Telegram Bot
1. Open Telegram → search **@BotFather** → `/newbot`
2. Follow the prompts → copy the token → paste as `TELEGRAM_BOT_TOKEN`
3. Start the bot (`python run_telegram.py`), then send `/start` to your bot
4. It will reply with your user ID → paste as `TELEGRAM_ALLOWED_USER_ID`

### Webhook secret
Choose any random string — it protects the web UI endpoint. Example:
```bash
openssl rand -hex 32
```

---

## Running

### Telegram bot (receives reel URLs)
```bash
python run_telegram.py
```
Send any Instagram Reel URL to your bot → it replies "Queued!".

### Webhook server (optional web UI)
```bash
python run_server.py
```
Open `http://localhost:8000` in your browser — paste a reel URL and hit Save.

### Daily digest (process queue → transcribe → summarize → publish)
```bash
python run_digest.py

# Dry run (process everything but skip Notion publish)
python run_digest.py --dry-run

# Retry previously failed reels
python run_digest.py --reset-errors
```

---

## Automating (run on startup + daily schedule)

### macOS / Linux — launchd / cron

**Telegram bot and server** — add to your shell startup, or create a launchd plist / systemd service.

**Daily digest** — add to crontab (`crontab -e`):
```cron
0 8 * * * cd /path/to/reels-digest-agent && .venv/bin/python run_digest.py >> logs/digest.log 2>&1
```

### Windows — Task Scheduler

Run these in an **Administrator** PowerShell:

```powershell
# Telegram bot — start at login
$action = New-ScheduledTaskAction `
  -Execute "$PWD\.venv\Scripts\python.exe" `
  -Argument "run_telegram.py" `
  -WorkingDirectory "$PWD"
$trigger = New-ScheduledTaskTrigger -AtLogon
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 0) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "ReelsTelegramBot" -Action $action -Trigger $trigger -Settings $settings

# Webhook server — start at login
$action2 = New-ScheduledTaskAction `
  -Execute "$PWD\.venv\Scripts\python.exe" `
  -Argument "run_server.py" `
  -WorkingDirectory "$PWD"
Register-ScheduledTask -TaskName "ReelsServer" -Action $action2 -Trigger $trigger -Settings $settings

# Daily digest — 8am every day
$action3 = New-ScheduledTaskAction `
  -Execute "$PWD\.venv\Scripts\python.exe" `
  -Argument "run_digest.py" `
  -WorkingDirectory "$PWD"
$trigger3 = New-ScheduledTaskTrigger -Daily -At "08:00AM"
Register-ScheduledTask -TaskName "ReelsDigest" -Action $action3 -Trigger $trigger3
```

---

## Project structure

```
reels-digest-agent/
├── src/
│   ├── config.py            # Environment variables and paths
│   ├── db.py                # SQLite queue and digest log
│   ├── server.py            # FastAPI webhook + web UI
│   ├── downloader.py        # yt-dlp audio extraction
│   ├── transcriber.py       # Local Whisper transcription
│   ├── summarizer.py        # Claude API summarization
│   ├── notion_publisher.py  # Notion page builder
│   ├── telegram_bot.py      # Telegram bot (URL intake)
│   ├── daily_job.py         # Orchestrator
│   └── static/
│       └── index.html       # Mobile web UI
├── run_server.py            # Start webhook server
├── run_telegram.py          # Start Telegram bot
├── run_digest.py            # Run digest job
├── requirements.txt
├── .env.example
└── Dockerfile
```

---

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key |
| `NOTION_API_KEY` | Yes | — | Notion integration token |
| `NOTION_DATABASE_ID` | Yes | — | Target Notion database ID |
| `WEBHOOK_SECRET` | Yes | — | Shared secret for web UI auth |
| `TELEGRAM_BOT_TOKEN` | Yes* | — | Telegram bot token from BotFather |
| `TELEGRAM_ALLOWED_USER_ID` | Yes* | — | Your Telegram user ID |
| `WHISPER_MODEL` | No | `base` | Whisper model size (`tiny`/`base`/`small`/`medium`/`large`) |
| `PORT` | No | `8000` | Webhook server port |
| `DB_PATH` | No | `data/reels.db` | SQLite database path |
| `DOWNLOAD_DIR` | No | `data/downloads` | Temporary audio download directory |

*Required if using the Telegram bot (recommended).

---

## Whisper model sizes

| Model | Size | Speed | Accuracy |
|-------|------|-------|----------|
| `tiny` | 75 MB | Very fast | Basic |
| `base` | 140 MB | Fast | Good (recommended) |
| `small` | 460 MB | Medium | Better |
| `medium` | 1.5 GB | Slow | Great |
| `large` | 3 GB | Very slow | Best |

---

## Docker (optional)

```bash
# Build
docker build -t reels-digest-agent .

# Run Telegram bot
docker run -d --name reels-telegram --env-file .env -v $(pwd)/data:/app/data reels-digest-agent python run_telegram.py

# Run webhook server
docker run -d --name reels-server -p 8000:8000 --env-file .env -v $(pwd)/data:/app/data reels-digest-agent python run_server.py

# Run digest job
docker run --rm --env-file .env -v $(pwd)/data:/app/data reels-digest-agent python run_digest.py
```

---

## Notes

- The Telegram bot and webhook server are **separate processes** from the digest job. They only queue URLs. The actual processing happens when you run `run_digest.py`.
- Downloaded audio files are automatically deleted after transcription.
- Re-submitting the same reel URL is safe — duplicates are silently ignored.
- If a reel fails (private account, deleted, etc.) it's marked as error and skipped. Use `--reset-errors` to retry.
- The Whisper model is loaded once and kept in memory for the duration of the digest run.
