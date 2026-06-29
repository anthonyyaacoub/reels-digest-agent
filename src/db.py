"""SQLite persistence: reel queue, status tracking, digest log, harness events."""

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from .config import DB_PATH

logger = logging.getLogger(__name__)

# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS reels (
    id           TEXT PRIMARY KEY,
    url          TEXT NOT NULL UNIQUE,
    added_at     TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    transcript   TEXT,
    summary      TEXT,
    note         TEXT,
    error_msg    TEXT,
    processed_at TEXT
);

CREATE TABLE IF NOT EXISTS digests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT NOT NULL UNIQUE,
    notion_url TEXT NOT NULL,
    reel_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS harness_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    source       TEXT NOT NULL,    -- 'telegram-pattern' | 'hook' | 'fastapi'
    kind         TEXT NOT NULL,    -- event name, e.g. 'reel.queued', 'bash.blocked'
    payload_json TEXT,             -- JSON blob, freeform
    outcome      TEXT NOT NULL DEFAULT 'ok'  -- 'ok' | 'error' | 'blocked'
);
CREATE INDEX IF NOT EXISTS idx_harness_events_ts ON harness_events(ts);
CREATE INDEX IF NOT EXISTS idx_harness_events_kind ON harness_events(kind);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    with _connect() as conn:
        conn.executescript(_DDL)
        # Forward migration: add reels.note to databases created before captions
        # were attachable. Idempotent — skipped once the column exists.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(reels)")}
        if "note" not in columns:
            conn.execute("ALTER TABLE reels ADD COLUMN note TEXT")
            logger.info("Migrated reels table — added note column")
    logger.info("Database initialised at %s", DB_PATH)


def _reel_id(url: str) -> str:
    """Return a 12-char SHA-256 prefix of the URL."""
    return hashlib.sha256(url.encode()).hexdigest()[:12]


def add_reel(url: str, note: Optional[str] = None) -> tuple[str, str]:
    """
    Insert a reel into the queue, or retry it if it previously errored.

    Returns ``(reel_id, action)`` where ``action`` is one of:
      - ``"queued"``       — new row inserted
      - ``"requeued"``     — existing error row reset to pending
      - ``"already_pending"`` — already in the queue, no-op
      - ``"already_done"``  — already processed, no-op
    """
    reel_id = _reel_id(url)
    note = note.strip() if note and note.strip() else None
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO reels (id, url, added_at, status, note) "
                "VALUES (?, ?, ?, 'pending', ?)",
                (reel_id, url, now, note),
            )
        logger.info("Queued reel %s", reel_id)
        return reel_id, "queued"
    except sqlite3.IntegrityError:
        pass

    with _connect() as conn:
        row = conn.execute(
            "SELECT status, note FROM reels WHERE id = ?", (reel_id,)
        ).fetchone()
        current = row["status"] if row else None

        # Backfill a note onto an existing row that didn't have one yet, so a
        # caption sent in a follow-up message lands on the already-queued reel.
        if note and row is not None and not (row["note"] or "").strip():
            conn.execute("UPDATE reels SET note = ? WHERE id = ?", (note, reel_id))
            logger.info("Attached note to existing reel %s", reel_id)

        if current == "error":
            conn.execute(
                "UPDATE reels SET status='pending', error_msg=NULL, processed_at=NULL "
                "WHERE id = ?",
                (reel_id,),
            )
            logger.info("Reel %s was in error state — reset to pending", reel_id)
            return reel_id, "requeued"

        if current == "done":
            logger.info("Reel %s already processed — skipping", reel_id)
            return reel_id, "already_done"

        logger.info("Reel %s already pending — skipping", reel_id)
        return reel_id, "already_pending"


def get_pending_reels() -> list[sqlite3.Row]:
    """Return all reels with status='pending', ordered by added_at."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM reels WHERE status = 'pending' ORDER BY added_at"
        ).fetchall()
    return rows


def mark_done(reel_id: str, transcript: str, summary: str) -> None:
    """Mark a reel as successfully processed."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE reels
            SET status = 'done', transcript = ?, summary = ?, processed_at = ?
            WHERE id = ?
            """,
            (transcript, summary, now, reel_id),
        )
    logger.info("Marked reel %s as done", reel_id)


def mark_error(reel_id: str, error_msg: str) -> None:
    """Mark a reel as failed with an error message."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE reels
            SET status = 'error', error_msg = ?, processed_at = ?
            WHERE id = ?
            """,
            (error_msg, now, reel_id),
        )
    logger.warning("Marked reel %s as error: %s", reel_id, error_msg)


def log_digest(date: str, notion_url: str, count: int) -> None:
    """Record a published digest."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO digests (date, notion_url, reel_count, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (date, notion_url, count, now),
        )
    logger.info("Logged digest for %s (%d reels) → %s", date, count, notion_url)


def reset_errors() -> int:
    """Reset all error reels back to pending so they can be retried. Returns count reset."""
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE reels SET status='pending', error_msg=NULL, processed_at=NULL WHERE status='error'"
        )
        count = cursor.rowcount
    logger.info("Reset %d error reel(s) to pending", count)
    return count


def get_reel_counts() -> dict[str, int]:
    """Return counts of reels grouped by status."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM reels GROUP BY status"
        ).fetchall()
    return {row["status"]: row["cnt"] for row in rows}


# ── Harness events ────────────────────────────────────────────────────────────

def add_harness_event(
    source: str,
    kind: str,
    payload: Optional[dict] = None,
    outcome: str = "ok",
) -> None:
    """Append one row to harness_events. Best-effort: never raises into the caller.

    Sources: 'fastapi' (server-side endpoints), 'hook' (Claude Code hooks),
    'telegram-pattern' (in-session pattern dispatch — currently unused, reserved).
    Outcomes: 'ok', 'error', 'blocked'.
    """
    try:
        now = datetime.now(timezone.utc).isoformat()
        with _connect() as conn:
            conn.execute(
                "INSERT INTO harness_events (ts, source, kind, payload_json, outcome) "
                "VALUES (?, ?, ?, ?, ?)",
                (now, source, kind, json.dumps(payload or {}), outcome),
            )
    except Exception as exc:
        logger.warning("add_harness_event failed (%s/%s): %s", source, kind, exc)


def harness_events_since(cutoff_iso: str) -> list[sqlite3.Row]:
    """Return all harness_events with ts >= cutoff_iso, oldest first."""
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM harness_events WHERE ts >= ? ORDER BY ts",
            (cutoff_iso,),
        ).fetchall()
