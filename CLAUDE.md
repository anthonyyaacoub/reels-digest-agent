# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the project

All commands assume the virtualenv is active (`.venv/Scripts/activate` on Windows).

```bash
# Install dependencies
pip install -r requirements.txt

# Start the Telegram bot (long-polling, blocking)
python -m src.telegram_bot   # not directly — bot is started via:
python -c "from src.telegram_bot import run_bot; run_bot()"

# Start the FastAPI webhook server
python run_server.py          # runs on PORT (default 8000)

# Run the daily digest job manually
python -c "from src.daily_job import run_daily_digest; run_daily_digest()"

# Dry-run (process reels but skip Notion publish)
python -c "from src.daily_job import run_daily_digest; run_daily_digest(dry_run=True)"
```

There are no automated tests in this repo.

## Environment variables (`.env`)

Required:
- `ANTHROPIC_API_KEY` — Claude API key
- `NOTION_API_KEY` — Notion integration token
- `NOTION_DATABASE_ID` — Target Notion database ID
- `WEBHOOK_SECRET` — Bearer token for the FastAPI `/api/reel` endpoint

Optional:
- `TELEGRAM_BOT_TOKEN` — Enables the Telegram bot
- `TELEGRAM_ALLOWED_USER_ID` — Restricts bot to a single Telegram user ID
- `MEDIUM_SID` — Medium session cookie (`sid`) for fetching member-only articles
- `WHISPER_MODEL` — Whisper model size (default: `base`)
- `PORT` — FastAPI port (default: `8000`)
- `DB_PATH` / `DOWNLOAD_DIR` — Override default data paths

## Architecture

The project has two independent entry points that share the SQLite database:

**1. FastAPI webhook server** (`run_server.py` → `src/server.py`)
Accepts Instagram reel URLs via `POST /api/reel` (Bearer auth) and queues them in SQLite. Also serves a mobile web UI at `/`.

**2. Telegram bot** (`src/telegram_bot.py`)
Long-polling bot with two behaviours based on the URL type received:
- **Instagram URL** → queued in SQLite for the daily digest (same as the webhook)
- **Medium URL** → immediately fetched, converted to Markdown + images, zipped, and sent back as a file

**Daily digest job** (`src/daily_job.py`)
Meant to run on a schedule (cron/Task Scheduler). Processes all `pending` reels in the queue: download audio (yt-dlp) → transcribe (local Whisper) → summarize (Claude API) → publish to Notion. Audio files are deleted after transcription.

**Medium article fetcher** (`src/article_fetcher.py`)
Fetches Medium articles using the authenticated session cookie (`MEDIUM_SID`). Parses HTML with BeautifulSoup, resolves images from `srcset`/`<picture><source>` (Medium lazy-loads images — `src` is often a blurred base64 placeholder). Falls back to `freedium.cfd` only when no `MEDIUM_SID` is configured.

**Persistence** (`src/db.py`)
SQLite with WAL mode. Two tables: `reels` (queue with status: `pending`/`done`/`error`) and `digests` (log of published Notion pages). `db.init_db()` is idempotent and called at startup in both entry points.

## Key implementation notes

- **ffmpeg**: The config and downloader both probe for ffmpeg in the WinGet install path if it's not on `PATH`, so this works on Windows without shell restarts.
- **Whisper model**: Loaded once as a module-level singleton on first transcription call; `fp16=False` for CPU safety.
- **Notion blocks**: Rich text has a 2000-char limit per block — long transcripts are chunked in `_transcript_blocks()`.
- **Medium images**: Always prefer `srcset` over `src`; ignore `data:` URIs (blurred previews). The `<picture><source srcset>` pattern is the primary path for Medium article images.
