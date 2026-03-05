"""Entry point: run the daily digest job (designed for cron or manual use)."""

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Process queued reels and publish a daily Notion digest."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Process reels (download/transcribe/summarize) but skip Notion publish.",
    )
    parser.add_argument(
        "--reset-errors",
        action="store_true",
        help="Reset all error reels back to pending before running.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    if args.reset_errors:
        from src.db import init_db, reset_errors
        init_db()
        n = reset_errors()
        logger.info("Reset %d error reel(s) to pending.", n)

    if args.dry_run:
        logger.info("Running in DRY RUN mode — Notion will not be updated.")

    from src.daily_job import run_daily_digest

    success = run_daily_digest(dry_run=args.dry_run)
    sys.exit(0 if success else 1)
