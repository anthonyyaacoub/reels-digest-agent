"""Compose and send the morning/evening Telegram recap."""

import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .config import DB_PATH, TELEGRAM_ALLOWED_USER_ID, TELEGRAM_BOT_TOKEN
from .db import _connect, get_reel_counts, harness_events_since, init_db

logger = logging.getLogger(__name__)

_TODOS_PATH = Path(__file__).resolve().parent.parent / "data" / "todos.md"
_UNCHECKED = re.compile(r"^\s*-\s*\[\s\]\s*(.+?)\s*$")

# Strip path appendices like " — /home/..." or " — `/home/...`..." (with anything trailing)
_TRAILING_PATH = re.compile(r"\s*[—–-]+\s*`?[~/][^\s`]+(?:\.[a-zA-Z]+)?`?.*$")
# Strip leading kind prefix ("Plan: ", "Research: ", "Refactor: ", etc.) — recap section already implies it's a todo
_LEADING_KIND = re.compile(r"^(?:Plan|Research|Refactor|Fix|Build|Add|Wire|Implement|Investigate|Audit)\s*:\s*", re.IGNORECASE)
# Strip "Added YYYY-MM-DD" trailing annotations
_ADDED_TAIL = re.compile(r"[.,;]?\s*Added\s+\d{4}-\d{2}-\d{2}\.?\s*$", re.IGNORECASE)
_MAX_TODO_LEN = 100
# Legacy-Markdown entity starters. Stripped from dynamic todo text so a stray or
# truncation-orphaned char (e.g. an opening backtick with no close) can't break
# Telegram's parser — see the 2026-05-13→20 recap outage.
_MD_SPECIALS = re.compile(r"[`*_\[\]]")


def _summarize_todo(text: str) -> str:
    """Strip path appendices, leading kind prefixes, and trailing annotations.

    Recap sections like 'Still on your plate' already imply these are todos;
    full paths and date stamps are noise on a mobile screen.
    """
    text = _TRAILING_PATH.sub("", text)
    text = _ADDED_TAIL.sub("", text)
    text = _LEADING_KIND.sub("", text, count=1)
    text = text.strip().rstrip(".,;:—–-").strip()
    if len(text) > _MAX_TODO_LEN:
        text = text[: _MAX_TODO_LEN - 1].rstrip() + "…"
    # Neutralize Markdown specials last — truncation above can orphan an entity.
    text = _MD_SPECIALS.sub("", text)
    return text


def _recent_digest_urls(hours: int = 24) -> list[tuple[str, str, int]]:
    """Return digests created in the last `hours` hours: (date, notion_url, reel_count)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT date, notion_url, reel_count FROM digests WHERE created_at >= ? ORDER BY date DESC",
            (cutoff,),
        ).fetchall()
    return [(r["date"], r["notion_url"], r["reel_count"]) for r in rows]


def _harness_summary(hours: int = 12) -> str | None:
    """One-line summary of harness events in the last `hours`. Returns None if no events.

    Counts: pattern dispatches by kind (reel.*, article.*, transcribe.*),
    blocks (outcome='blocked' or kind='bash.blocked'), state changes (kind='jarvis.state_changed'
    — currently observe-only via log file, not yet wired to harness_events;
    plan writes (kind='plan.written') get their own count.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = harness_events_since(cutoff)
    if not rows:
        return None

    reels = sum(1 for r in rows if r["kind"].startswith("reel."))
    articles = sum(1 for r in rows if r["kind"].startswith("article."))
    transcripts = sum(1 for r in rows if r["kind"].startswith("transcribe."))
    plans = sum(1 for r in rows if r["kind"] == "plan.written")
    telegram_replies = sum(1 for r in rows if r["kind"] == "telegram.reply")
    blocked = sum(1 for r in rows if r["outcome"] == "blocked")
    errors = sum(1 for r in rows if r["outcome"] == "error")

    parts = []
    dispatches = reels + articles + transcripts
    if dispatches:
        breakdown = []
        if reels:
            breakdown.append(f"{reels} reel{'s' if reels != 1 else ''}")
        if articles:
            breakdown.append(f"{articles} article{'s' if articles != 1 else ''}")
        if transcripts:
            breakdown.append(f"{transcripts} transcript{'s' if transcripts != 1 else ''}")
        parts.append(f"{dispatches} dispatch{'es' if dispatches != 1 else ''} ({', '.join(breakdown)})")
    if telegram_replies:
        parts.append(f"{telegram_replies} reply" if telegram_replies == 1 else f"{telegram_replies} replies")
    if plans:
        parts.append(f"{plans} plan{'s' if plans != 1 else ''} written")
    if blocked:
        parts.append(f"{blocked} blocked")
    if errors:
        parts.append(f"{errors} error{'s' if errors != 1 else ''}")

    if not parts:
        return None
    return "  ·  ".join(parts)


def _open_todos() -> list[str]:
    if not _TODOS_PATH.exists():
        return []
    items: list[str] = []
    for line in _TODOS_PATH.read_text(encoding="utf-8").splitlines():
        match = _UNCHECKED.match(line)
        if match:
            items.append(match.group(1))
    return items


def compose_recap(slot: str) -> str:
    """slot: 'morning' or 'evening'."""
    init_db()
    counts = get_reel_counts()
    pending = counts.get("pending", 0)
    done = counts.get("done", 0)
    error = counts.get("error", 0)

    digests = _recent_digest_urls(hours=24)
    todos = _open_todos()

    greeting = "🌅 Morning recap" if slot == "morning" else "🌙 Evening recap"
    lines = [greeting, ""]

    harness = _harness_summary(hours=12)
    if harness:
        lines.append("*Harness (last 12h)*")
        lines.append(f"• {harness}")
        lines.append("")

    lines.append("*Queue*")
    lines.append(f"• {pending} pending  ·  {done} done  ·  {error} errored")
    lines.append("")

    if digests:
        lines.append("*Published in the last 24h*")
        for date, url, count in digests:
            lines.append(f"• {date} ({count} reel{'s' if count != 1 else ''}) → {url}")
        lines.append("")
    else:
        lines.append("_No digest published in the last 24h._")
        lines.append("")

    if todos:
        lines.append("*Still on your plate*")
        for item in todos:
            summary = _summarize_todo(item)
            if summary:
                lines.append(f"• {summary}")
    else:
        lines.append("✅ No outstanding TODOs — nice.")

    return "\n".join(lines)


def send_to_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN.startswith("PLACEHOLDER"):
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set in .env — recap can't be sent. "
            "Paste the real bot token and re-run."
        )
    if not TELEGRAM_ALLOWED_USER_ID:
        raise RuntimeError("TELEGRAM_ALLOWED_USER_ID is not set in .env.")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_ALLOWED_USER_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, json=payload, timeout=10)
    if resp.status_code == 400:
        # A stray Markdown entity in dynamic content (todo/URL) breaks the parser.
        # Don't lose the recap — resend without parse_mode so the update lands.
        try:
            reason = resp.json().get("description", resp.text)
        except ValueError:
            reason = resp.text
        logger.warning("Markdown send failed (%s); retrying as plain text", reason)
        payload.pop("parse_mode")
        resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()
    logger.info("Recap sent (%d chars)", len(text))


def run(slot: str) -> None:
    text = compose_recap(slot)
    send_to_telegram(text)
