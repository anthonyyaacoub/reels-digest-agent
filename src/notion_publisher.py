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

def _reel_blocks(reel: dict[str, Any]) -> list[dict[str, Any]]:
    """Render a single reel as a sequence of Notion blocks."""
    summary: Summary = reel["summary"]
    url: str = reel["url"]
    transcript: str = reel.get("transcript", "")

    blocks: list[dict[str, Any]] = [_heading(2, summary["title"])]
    if summary["takeaways"]:
        blocks.append(_callout(summary["takeaways"], emoji="💡"))
    if summary["action_items"]:
        blocks.append(_callout(summary["action_items"], emoji="✅"))
    transcript_children = _transcript_blocks(transcript) if transcript else [
        _paragraph("(No transcript available)")
    ]
    blocks.append(_toggle("Full Transcript", transcript_children))
    blocks.append(_link_paragraph("View original reel →", url))
    blocks.append(_divider())
    return blocks


_data_source_id: str | None = None


def _get_data_source_id() -> str | None:
    """Resolve and cache the database's primary data source ID (notion-client 3.x)."""
    global _data_source_id
    if _data_source_id is not None:
        return _data_source_id
    try:
        db = _notion.databases.retrieve(NOTION_DATABASE_ID)
        sources = db.get("data_sources") or []
        if sources:
            _data_source_id = sources[0]["id"]
            return _data_source_id
    except Exception as exc:
        logger.warning("Could not retrieve data source for database: %s", exc)
    return None


def _find_existing_page(date_str: str) -> tuple[str, str] | None:
    """Return (page_id, page_url) for an existing digest page on this date, or None."""
    ds_id = _get_data_source_id()
    if not ds_id:
        return None
    try:
        result = _notion.data_sources.query(
            data_source_id=ds_id,
            filter={"property": "Date", "date": {"equals": date_str}},
            page_size=1,
        )
    except Exception as exc:
        logger.warning("Notion query for existing page failed: %s — will create new page", exc)
        return None

    pages = result.get("results", [])
    if not pages:
        return None
    page = pages[0]
    return page["id"], page.get("url", "")


def publish_digest(
    digest_date: date,
    reels: list[dict[str, Any]],
) -> str:
    """
    Publish a daily digest to Notion. If a page for ``digest_date`` already
    exists in the database, append the new reels to it; otherwise create a
    new page.

    Returns the URL of the (new or existing) page.
    """
    date_str = digest_date.isoformat()
    count = len(reels)
    title = f"Reels Digest — {date_str}"

    logger.info("Publishing digest '%s' with %d reel(s) to Notion…", title, count)

    existing = _find_existing_page(date_str)

    if existing:
        page_id, page_url = existing
        new_blocks = [block for reel in reels for block in _reel_blocks(reel)]
        try:
            _notion.blocks.children.append(block_id=page_id, children=new_blocks)
        except Exception as exc:
            raise RuntimeError(f"Failed to append to Notion page {page_id}: {exc}") from exc
        logger.info("Appended %d reel(s) to existing Notion page: %s", count, page_url)
        return page_url

    blocks: list[dict[str, Any]] = [
        _heading(1, f"Daily Reels Digest — {date_str}"),
        _paragraph(f"{count} reel{'s' if count != 1 else ''} processed"),
        _divider(),
    ]
    for reel in reels:
        blocks.extend(_reel_blocks(reel))

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
