"""Orchestrator: process the pending queue and publish a Notion digest."""

import json
import logging
from datetime import date, datetime, timezone
from typing import Any, Optional

from . import db
from .downloader import cleanup_audio, download_audio
from .notion_publisher import publish_digest
from .summarizer import summarize
from .transcriber import transcribe

logger = logging.getLogger(__name__)


def _process_reel(reel: Any) -> Optional[dict[str, Any]]:
    """
    Download → transcribe → summarize a single reel row.

    Returns a result dict on success, or ``None`` on failure (the reel is
    already marked as error in the DB before returning).
    """
    reel_id: str = reel["id"]
    url: str = reel["url"]
    logger.info("Processing reel %s (%s)…", reel_id, url)

    audio_path = None
    try:
        # 1. Download
        audio_path = download_audio(reel_id, url)

        # 2. Transcribe
        transcript = transcribe(audio_path)

        # 3. Summarize
        summary = summarize(transcript)

        # 4. Persist
        db.mark_done(reel_id, transcript, json.dumps(summary))
        logger.info("Reel %s done — title: %s", reel_id, summary["title"])

        return {
            "id": reel_id,
            "url": url,
            "transcript": transcript,
            "summary": summary,
        }

    except Exception as exc:
        logger.error("Failed to process reel %s: %s", reel_id, exc, exc_info=True)
        db.mark_error(reel_id, str(exc))
        return None

    finally:
        if audio_path is not None:
            cleanup_audio(audio_path)


def run_daily_digest(dry_run: bool = False) -> bool:
    """
    Main entry point for the daily digest job.

    Parameters
    ----------
    dry_run:
        If ``True``, process reels (download + transcribe + summarize) but
        skip the Notion publish step.

    Returns
    -------
    bool
        ``True`` if the job completed without fatal errors, ``False`` otherwise.
    """
    db.init_db()

    pending = db.get_pending_reels()
    total = len(pending)
    logger.info("Daily digest started — %d pending reel(s) found.", total)

    if total == 0:
        logger.info("Nothing to process. Exiting.")
        return True

    results: list[dict[str, Any]] = []
    failed = 0

    for reel in pending:
        result = _process_reel(reel)
        if result is not None:
            results.append(result)
        else:
            failed += 1

    succeeded = len(results)
    logger.info(
        "Processing complete — %d succeeded, %d failed out of %d.",
        succeeded,
        failed,
        total,
    )

    # ── Publish to Notion ─────────────────────────────────────────────────────
    if not results:
        logger.warning("No reels were processed successfully — skipping Notion publish.")
        return failed == 0

    today = date.today()

    if dry_run:
        logger.info(
            "[DRY RUN] Would publish %d reel(s) to Notion for %s.",
            succeeded,
            today.isoformat(),
        )
        _print_summary(results, failed, notion_url=None, dry_run=True)
        return True

    try:
        notion_url = publish_digest(today, results)
        db.log_digest(today.isoformat(), notion_url, succeeded)
        _print_summary(results, failed, notion_url=notion_url, dry_run=False)
        return True
    except Exception as exc:
        logger.error("Failed to publish digest to Notion: %s", exc, exc_info=True)
        _print_summary(results, failed, notion_url=None, dry_run=False)
        return False


def _print_summary(
    results: list[dict[str, Any]],
    failed: int,
    notion_url: Optional[str],
    dry_run: bool,
) -> None:
    """Print a human-readable summary to stdout."""
    sep = "─" * 60
    print(f"\n{sep}")
    print("  REELS DIGEST — SUMMARY")
    print(sep)
    for r in results:
        summary = r["summary"]
        print(f"\n  ✓ {summary['title']}")
        print(f"    URL : {r['url']}")
        for t in summary["takeaways"]:
            print(f"    • {t}")
    if failed:
        print(f"\n  ✗ {failed} reel(s) failed — see log for details.")
    if dry_run:
        print("\n  [DRY RUN] Notion publish skipped.")
    elif notion_url:
        print(f"\n  Notion page → {notion_url}")
    else:
        print("\n  Notion publish failed — see log for details.")
    print(f"{sep}\n")
