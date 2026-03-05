"""Build and publish a daily digest page to Notion."""

import logging
from datetime import date
from typing import Any

from notion_client import Client

from .config import NOTION_API_KEY, NOTION_DATABASE_ID
from .summarizer import Summary

logger = logging.getLogger(__name__)

_notion = Client(auth=NOTION_API_KEY)

# ── Block helpers ─────────────────────────────────────────────────────────────

def _heading(level: int, text: str) -> dict[str, Any]:
    key = f"heading_{level}"
    return {
        "object": "block",
        "type": key,
        key: {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        },
    }


def _paragraph(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        },
    }


def _divider() -> dict[str, Any]:
    return {"object": "block", "type": "divider", "divider": {}}


def _callout(lines: list[str], emoji: str = "💡") -> dict[str, Any]:
    """Callout block whose body is a joined bullet list."""
    body = "\n".join(f"• {line}" for line in lines)
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [{"type": "text", "text": {"content": body}}],
            "icon": {"type": "emoji", "emoji": emoji},
        },
    }


def _toggle(title: str, children: list[dict[str, Any]]) -> dict[str, Any]:
    """Toggle block containing child blocks."""
    return {
        "object": "block",
        "type": "toggle",
        "toggle": {
            "rich_text": [{"type": "text", "text": {"content": title}}],
            "children": children,
        },
    }


def _link_paragraph(text: str, url: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [
                {
                    "type": "text",
                    "text": {"content": text, "link": {"url": url}},
                }
            ]
        },
    }


# ── Transcript chunking ───────────────────────────────────────────────────────

_NOTION_TEXT_LIMIT = 2000  # Notion rich_text content limit per block


def _transcript_blocks(transcript: str) -> list[dict[str, Any]]:
    """Split a long transcript into multiple paragraph blocks."""
    chunks = [
        transcript[i: i + _NOTION_TEXT_LIMIT]
        for i in range(0, max(len(transcript), 1), _NOTION_TEXT_LIMIT)
    ]
    return [_paragraph(chunk) for chunk in chunks]


# ── Main publisher ────────────────────────────────────────────────────────────

def publish_digest(
    digest_date: date,
    reels: list[dict[str, Any]],
) -> str:
    """
    Create a Notion page for the daily digest.

    Parameters
    ----------
    digest_date:
        The date this digest covers.
    reels:
        List of dicts, each with keys:
        - ``id`` (str)
        - ``url`` (str)
        - ``transcript`` (str)
        - ``summary`` (:class:`Summary`)

    Returns
    -------
    str
        URL of the newly created Notion page.

    Raises
    ------
    RuntimeError
        If the Notion API call fails.
    """
    date_str = digest_date.isoformat()
    count = len(reels)
    title = f"Reels Digest — {date_str}"

    logger.info("Publishing digest '%s' with %d reel(s) to Notion…", title, count)

    # ── Top-level page blocks ─────────────────────────────────────────────────
    blocks: list[dict[str, Any]] = [
        _heading(1, f"Daily Reels Digest — {date_str}"),
        _paragraph(f"{count} reel{'s' if count != 1 else ''} processed"),
        _divider(),
    ]

    for reel in reels:
        summary: Summary = reel["summary"]
        url: str = reel["url"]
        transcript: str = reel.get("transcript", "")

        blocks.append(_heading(2, summary["title"]))

        # Key takeaways callout
        if summary["takeaways"]:
            blocks.append(_callout(summary["takeaways"], emoji="💡"))

        # Action items callout
        if summary["action_items"]:
            blocks.append(_callout(summary["action_items"], emoji="✅"))

        # Full transcript in a toggle
        transcript_children = _transcript_blocks(transcript) if transcript else [
            _paragraph("(No transcript available)")
        ]
        blocks.append(_toggle("Full Transcript", transcript_children))

        # Link to original reel
        blocks.append(_link_paragraph("View original reel →", url))
        blocks.append(_divider())

    # ── Create the Notion page ────────────────────────────────────────────────
    try:
        response = _notion.pages.create(
            parent={"database_id": NOTION_DATABASE_ID},
            properties={
                "Name": {
                    "title": [{"type": "text", "text": {"content": title}}]
                },
                "Date": {
                    "date": {"start": date_str}
                },
            },
            children=blocks,
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to create Notion page: {exc}") from exc

    page_url: str = response.get("url", "")
    logger.info("Notion page created: %s", page_url)
    return page_url
