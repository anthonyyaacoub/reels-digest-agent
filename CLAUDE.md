# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the project

All commands assume the virtualenv is active. On WSL: `source .venv/bin/activate`.

```bash
# Install dependencies
pip install -r requirements.txt

# Start the FastAPI server (auto-started by cron on WSL boot via @reboot)
python run_server.py          # runs on PORT (default 8000)

# Run the daily digest manually
python run_digest.py

# Dry-run the digest (process reels but skip Notion publish)
python -c "from src.daily_job import run_daily_digest; run_daily_digest(dry_run=True)"

# Send a recap to Telegram on demand
python run_recap.py morning
python run_recap.py evening
```

**Do not** run `src/telegram_bot.py` — see Architecture below.

Tests: one smoke test, `python tests/test_routing.py` (plain asserts, no framework) — covers the `src.db` reel-queue dedup + action ladder + note-backfill. Add more the same way (a `test_*.py` with a `__main__` runner) rather than pulling in pytest.

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

**Telegram front door**: The Telegram bot token is consumed by the Claude Code `plugin:telegram:telegram` MCP plugin (not by `src/telegram_bot.py`). When an Instagram reel or Medium article URL arrives, Claude detects it and POSTs to the local FastAPI server. Routing rules live in `~/.claude/rules-scoped/telegram-handlers.md`. **`src/telegram_bot.py` is kept for reference but must not be run** — its long-polling grab on the bot token would conflict with the MCP plugin.

**FastAPI server** (`run_server.py` → `src/server.py`) — the single backend. Endpoints:
- `POST /api/reel` (Bearer auth) — queues an Instagram reel/post URL in SQLite. Idempotent: response `{"status": "<action>", "id": "<reel_id>"}` where action is `queued` (new), `requeued` (was in error, reset to pending), `already_pending`, or `already_done`. Re-sharing a previously processed URL is a no-op. Also accepts requests from the iOS Shortcut and the mobile web UI at `/`.
- `POST /api/article` (Bearer auth) — synchronous Medium fetch. Returns `{title, author, image_count, zip_path}` after writing the zip to `data/downloads/articles/`.
- `GET /api/status` — queue counts.

**Daily digest job** (`run_digest.py` → `src/daily_job.py`)
Runs daily at 06:00 local (cron). For each pending reel: download audio (yt-dlp, best-effort — image-only carousels skip this) → transcribe (local Whisper) → extract visuals (see below) → summarize transcript + images jointly via Claude vision → publish to Notion. Audio files and extracted frames are deleted after summarization.

**Visual extraction** (`src/visual_extractor.py`)
Handles both reels and carousel posts. yt-dlp downloads the media; for videos, ffmpeg extracts 3 evenly-spaced keyframes and the source video is deleted; for image carousels, all images are kept as-is. Output is capped at 6 images per post to bound vision-token cost. The summarizer reads on-screen text overlays directly via Claude vision (not Tesseract) — see `decisions.md` for rationale.

**Recap** (`run_recap.py` → `src/recap.py`)
Runs at 09:00 (morning) and 21:00 (evening) local. Reads the SQLite queue + the digests log + unchecked items in `data/todos.md`, composes a recap, and posts it to Telegram via `sendMessage` (which is independent of the MCP plugin's `getUpdates` polling — same token, no conflict).

**Outstanding TODOs** (`data/todos.md`)
Markdown checklist of things the user still owes the system (secrets, ffmpeg, deployment). Hand-editable. The recap script reads unchecked rows and pings them to Telegram.

**Medium article fetcher** (`src/article_fetcher.py`)
Fetches Medium articles using the authenticated session cookie (`MEDIUM_SID`). Parses HTML with BeautifulSoup, resolves images from `srcset`/`<picture><source>` (Medium lazy-loads images — `src` is often a blurred base64 placeholder). Falls back to `freedium.cfd` only when no `MEDIUM_SID` is configured.

**Persistence** (`src/db.py`)
SQLite with WAL mode. Two tables: `reels` (queue with status: `pending`/`done`/`error`) and `digests` (log of published Notion pages). `db.init_db()` is idempotent and called at startup in both entry points. `db.add_reel(url)` returns `(reel_id, action)` and resets error rows back to pending; `db.reset_errors()` is a manual escape hatch.

**Model router** (`src/model_router.py`)
Two-tier wrapper around the Anthropic SDK. `call_executor` runs on the cheap model (default `claude-haiku-4-5`, override via `EXECUTOR_MODEL`); `call_advisor` runs on the synthesis-tier model (default `claude-sonnet-4-6`, override via `ADVISOR_MODEL`). Every call is logged to `data/cost_log.jsonl` with `model`, `label`, `input_tokens`, `output_tokens`, `latency_ms`. New code that calls the SDK should go through this router rather than instantiating `Anthropic()` directly. `src/reel_planner.py` is the canonical example of the two-phase pattern (Haiku classify → Sonnet synthesize). `src/summarizer.py` keeps its own client (vision call needs custom params) but reuses `log_cost` so the cost log is single-source-of-truth.

**Notion publisher** (`src/notion_publisher.py`)
Before creating a page, queries the Notion data source for an existing page on the digest date (notion-client 3.x uses `data_sources.query`, not the deprecated `databases.query`). If found, appends the new reel blocks to it; otherwise creates a fresh page. One Notion page per day, regardless of how many digest runs happen.

## Key implementation notes

- **ffmpeg**: The config and downloader both probe for ffmpeg in the WinGet install path if it's not on `PATH`, so this works on Windows without shell restarts.
- **Whisper model**: Loaded once as a module-level singleton on first transcription call; `fp16=False` for CPU safety.
- **Notion blocks**: Rich text has a 2000-char limit per block — long transcripts are chunked in `_transcript_blocks()`.
- **Medium images**: Always prefer `srcset` over `src`; ignore `data:` URIs (blurred previews). The `<picture><source srcset>` pattern is the primary path for Medium article images.

## Project memory

Structured per-project memory lives in `docs/project_notes/`. Check it before answering questions about this project's history, and log to it when relevant:

- `bugs.md` — incidents that took real time to diagnose or are likely to recur. Log when fixing a bug whose root cause wasn't obvious.
- `decisions.md` — ADRs (context, decision, alternatives, consequences). Log when making a non-obvious architectural call.
- `key_facts.md` — configuration, ports, URLs, account names, schedules. Update in place when a value changes.
- `issues.md` — work log keyed by ticket / PR. Optional; this project uses `data/todos.md` and `docs/plans/` instead, so `issues.md` is reserved for cross-cutting logs that don't fit either.

User-scope memory at `~/.claude/memory/` covers cross-project identity, patterns, and decisions. Per-project memory here covers anything specific to this codebase. Check both when answering questions about this project; prefer the per-project file when they conflict.

## Scoped rules

FastAPI rules (blocking I/O in async routes, response-model leakage, CORS, dependency-override cleanup) — not loaded globally, pulled in here because `src/server.py` is FastAPI:

@/home/antho/.claude/rules-scoped/fastapi.md
