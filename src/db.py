"""SQLite persistence: reel queue, status tracking, digest log."""

import hashlib
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
    logger.info("Database initialised at %s", DB_PATH)


def _reel_id(url: str) -> str:
    """Return a 12-char SHA-256 prefix of the URL."""
    return hashlib.sha256(url.encode()).hexdigest()[:12]


def add_reel(url: str) -> str:
    """
    Insert a reel into the queue.

    Returns the reel ID. If the URL already exists, returns the existing ID
    without raising an error.
    """
    reel_id = _reel_id(url)
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO reels (id, url, added_at, status) VALUES (?, ?, ?, 'pending')",
                (reel_id, url, now),
            )
        logger.info("Queued reel %s", reel_id)
    except sqlite3.IntegrityError:
        logger.info("Reel %s already in queue (skipping duplicate)", reel_id)
    return reel_id


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
