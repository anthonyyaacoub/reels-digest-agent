"""Orchestrator: process the pending queue and publish a Notion digest."""

import json
import logging
from datetime import date, datetime, timezone
from typing import Any, Optional

from . import db
from .brain_dropper import drop_reel
from .downloader import cleanup_audio, download_audio, fetch_caption
from .notion_publisher import publish_digest
from .reel_planner import generate_plan
from .summarizer import summarize
from .transcriber import transcribe
from .visual_extractor import cleanup_visuals, extract_visuals

logger = logging.getLogger(__name__)


def _process_reel(reel: Any) -> Optional[dict[str, Any]]:
    """
    Download → transcribe → summarize a single reel row.

    Returns a result dict on success, or ``None`` on failure (the reel is
    already marked as error in the DB before returning).
    """
    reel_id: str = reel["id"]
    url: str = reel["url"]
    note: Optional[str] = reel["note"] if "note" in reel.keys() else None
    logger.info("Processing reel %s (%s)…", reel_id, url)

    audio_path = None
    visual_paths: list = []
    try:
        # 1. Audio (best-effort — image-only carousels have none)
        transcript = ""
        try:
            audio_path = download_audio(reel_id, url)
            transcript = transcribe(audio_path)
        except RuntimeError as exc:
            logger.info("No audio track for %s (likely image-only post): %s", reel_id, exc)

        # 2. Visuals — keyframes for videos, full images for carousels
        visual_paths = extract_visuals(reel_id, url)

        # 2b. Caption fallback — yt-dlp can't pull image URLs from many
        # image-only IG carousels, so when audio + visuals are both empty
        # we use the post caption as the textual signal for summarization.
        if not transcript.strip() and not visual_paths:
            caption = fetch_caption(url)
            if caption:
                logger.info("Using caption fallback for %s (%d chars)", reel_id, len(caption))
                transcript = caption

        # 3. Summarize transcript + images together (+ any note the user attached)
        summary = summarize(transcript, image_paths=visual_paths, note=note)

        # 4. Persist
        db.mark_done(reel_id, transcript, json.dumps(summary))
        logger.info("Reel %s done — title: %s", reel_id, summary["title"])

        # 5. Drop into brain/raw/reels/ for the wiki ingest (best-effort).
        drop_reel(reel_id, url, transcript, summary)

        return {
            "id": reel_id,
            "url": url,
            "transcript": transcript,
            "summary": summary,
            "note": note,
        }

    except Exception as exc:
        logger.error("Failed to process reel %s: %s", reel_id, exc, exc_info=True)
        db.mark_error(reel_id, str(exc))
        return None

    finally:
        if audio_path is not None:
            cleanup_audio(audio_path)
        if visual_paths:
            cleanup_visuals(visual_paths)


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
    except Exception as exc:
        logger.error("Failed to publish digest to Notion: %s", exc, exc_info=True)
        _print_summary(results, failed, notion_url=None, dry_run=False)
        # Don't return early — still try to generate plans even if Notion failed.

    # Per-reel planning pass — wraps each call so a single failure doesn't
    # block the rest. Plan files land in <matched-project>/docs/plans/ or
    # ~/projects/_inbox/, with a pointer appended to data/todos.md.
    for r in results:
        try:
            generate_plan(r)
        except Exception as exc:
            logger.warning("Plan generation crashed for %s: %s", r.get("url"), exc)

    return True


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
