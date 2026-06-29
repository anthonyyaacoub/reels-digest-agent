"""Drop article bodies and reel summaries into ~/projects/brain/raw/ for the wiki ingest.

Best-effort: any failure here logs a warning but never raises into the calling flow.
The 07:00 brain ingest cron picks up whatever ends up here on its next run.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BRAIN_RAW = Path.home() / "projects" / "brain" / "raw"
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(title: str, max_len: int = 60) -> str:
    s = _SLUG_RE.sub("-", (title or "untitled").lower()).strip("-")
    return s[:max_len].rstrip("-") or "untitled"


def _write_unique(target_dir: Path, base_name: str, content: str) -> Path | None:
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / f"{base_name}.md"
    suffix = 2
    while out.exists():
        out = target_dir / f"{base_name}-{suffix}.md"
        suffix += 1
    out.write_text(content, encoding="utf-8")
    return out


def drop_article(title: str, author: str, url: str, content_md: str) -> Path | None:
    """Write article body into ~/projects/brain/raw/articles/. Returns path or None."""
    try:
        today = date.today().isoformat()
        base = f"{today}_{_slug(title)}"
        body = (
            "---\n"
            f"source_type: article\n"
            f"title: {title!r}\n"
            f"author: {author!r}\n"
            f"url: {url}\n"
            f"captured: {today}\n"
            "---\n\n"
            f"{content_md}\n"
        )
        out = _write_unique(BRAIN_RAW / "articles", base, body)
        logger.info("brain: dropped article into %s", out)
        return out
    except Exception as exc:
        logger.warning("brain: failed to drop article %r: %s", title, exc)
        return None


def drop_reel(reel_id: str, url: str, transcript: str, summary: dict[str, Any]) -> Path | None:
    """Write reel summary + transcript into ~/projects/brain/raw/reels/. Returns path or None."""
    try:
        title = summary.get("title") or "(Untitled reel)"
        takeaways = summary.get("takeaways") or []
        action_items = summary.get("action_items") or []
        today = date.today().isoformat()
        base = f"{today}_{_slug(title)}"
        sections = [
            "---",
            "source_type: reel",
            f"title: {title!r}",
            f"reel_id: {reel_id}",
            f"url: {url}",
            f"captured: {today}",
            "---",
            "",
            f"# {title}",
            "",
            "## Takeaways",
            "",
        ]
        sections.extend(f"- {t}" for t in takeaways) if takeaways else sections.append("_(none extracted)_")
        sections += ["", "## Action items", ""]
        sections.extend(f"- {a}" for a in action_items) if action_items else sections.append("_(none)_")
        sections += ["", "## Transcript", "", (transcript or "_(no audio track — image-only carousel)_"), ""]
        out = _write_unique(BRAIN_RAW / "reels", base, "\n".join(sections))
        logger.info("brain: dropped reel %s into %s", reel_id, out)
        return out
    except Exception as exc:
        logger.warning("brain: failed to drop reel %s: %s", reel_id, exc)
        return None
