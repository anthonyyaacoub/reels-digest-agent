"""Configuration: load env vars, constants, and paths."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Required secrets ──────────────────────────────────────────────────────────

ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]
NOTION_API_KEY: str = os.environ["NOTION_API_KEY"]
NOTION_DATABASE_ID: str = os.environ["NOTION_DATABASE_ID"]
WEBHOOK_SECRET: str = os.environ["WEBHOOK_SECRET"]

# ── Optional settings with defaults ───────────────────────────────────────────

WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "base")
PORT: int = int(os.getenv("PORT", "8000"))
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALLOWED_USER_ID: int = int(os.getenv("TELEGRAM_ALLOWED_USER_ID", "0"))
MEDIUM_SID: str = os.getenv("MEDIUM_SID", "")

# ── Paths ─────────────────────────────────────────────────────────────────────

_BASE_DIR = Path(__file__).resolve().parent.parent

DB_PATH: Path = Path(os.getenv("DB_PATH", str(_BASE_DIR / "data" / "reels.db")))
DOWNLOAD_DIR: Path = Path(
    os.getenv("DOWNLOAD_DIR", str(_BASE_DIR / "data" / "downloads"))
)

# Ensure directories exist at import time
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ── ffmpeg PATH fix ───────────────────────────────────────────────────────────
# If ffmpeg is not on PATH (e.g. installed via WinGet in a session that hasn't
# reloaded env vars), find it and inject the bin dir so Whisper and yt-dlp work.
import shutil as _shutil

if not _shutil.which("ffmpeg"):
    _winget_base = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    for _exe in _winget_base.rglob("ffmpeg.exe"):
        _ffmpeg_bin = str(_exe.parent)
        os.environ["PATH"] = _ffmpeg_bin + os.pathsep + os.environ.get("PATH", "")
        break
