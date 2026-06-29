"""Smoke test for the load-bearing reel-queue logic in src.db.

Covers the dedup key and the add_reel action ladder (queued / already_pending /
requeued / already_done) plus note-backfill onto an already-queued reel — the
exact behavior the Telegram Pattern-A handler relies on when a caption arrives
after the reel. No framework: run with `python tests/test_routing.py`
(plain asserts, exits non-zero on first failure).
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db  # noqa: E402


def _fresh_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.DB_PATH = path  # _connect() reads this at call time
    db.init_db()
    return path


def test_reel_id_is_stable_12char_key():
    a = db._reel_id("https://instagram.com/reel/ABC/")
    assert a == db._reel_id("https://instagram.com/reel/ABC/"), "same url -> same id"
    assert len(a) == 12
    assert a != db._reel_id("https://instagram.com/reel/XYZ/"), "diff url -> diff id"


def test_add_reel_action_ladder():
    _fresh_db()
    url = "https://instagram.com/reel/LADDER/"
    rid, action = db.add_reel(url)
    assert action == "queued"
    assert db.add_reel(url)[1] == "already_pending"

    db.mark_error(rid, "boom")
    assert db.add_reel(url)[1] == "requeued"

    db.mark_done(rid, "transcript", "summary")
    assert db.add_reel(url)[1] == "already_done"


def test_note_backfills_onto_existing_reel_once():
    _fresh_db()
    url = "https://instagram.com/reel/NOTE/"
    rid, _ = db.add_reel(url)  # queued, no note

    db.add_reel(url, note="watch the transition at 0:12")
    with db._connect() as conn:
        note = conn.execute("SELECT note FROM reels WHERE id=?", (rid,)).fetchone()["note"]
    assert note == "watch the transition at 0:12", "follow-up note should attach"

    # An existing note must not be clobbered by a later one.
    db.add_reel(url, note="different note")
    with db._connect() as conn:
        note = conn.execute("SELECT note FROM reels WHERE id=?", (rid,)).fetchone()["note"]
    assert note == "watch the transition at 0:12", "existing note must be preserved"


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok  {_name}")
    print("all routing smoke tests passed")
