"""Claude API summarizer — extracts title, takeaways, and action items."""

import json
import logging
from typing import TypedDict

import anthropic

from .config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

_MODEL = "claude-sonnet-4-20250514"

_SYSTEM_PROMPT = """You are an expert content analyst. Given a transcript from an Instagram Reel, extract:
1) A one-line title/topic
2) 3-5 key takeaways as bullet points
3) Any actionable advice mentioned

Respond ONLY with valid JSON matching this exact schema (no markdown, no extra text):
{
  "title": "<one-line title>",
  "takeaways": ["<takeaway 1>", "<takeaway 2>", ...],
  "action_items": ["<action 1>", ...]
}

If there are no action items, use an empty array. Be concise and direct."""


class Summary(TypedDict):
    title: str
    takeaways: list[str]
    action_items: list[str]


def summarize(transcript: str) -> Summary:
    """
    Send *transcript* to Claude and return a structured summary.

    Parameters
    ----------
    transcript:
        Full text transcript of the reel (with optional timestamps).

    Returns
    -------
    Summary
        Dict with keys ``title``, ``takeaways``, and ``action_items``.

    Raises
    ------
    RuntimeError
        If the Claude API call fails or returns unparseable JSON.
    """
    if not transcript.strip():
        logger.warning("Empty transcript — returning placeholder summary.")
        return Summary(
            title="(No transcript available)",
            takeaways=["Could not extract content — audio may be silent or non-verbal."],
            action_items=[],
        )

    user_message = f"Here is the transcript of an Instagram Reel:\n\n{transcript}"

    logger.info("Calling Claude (%s) to summarize transcript (%d chars)…", _MODEL, len(transcript))

    try:
        response = _client.messages.create(
            model=_MODEL,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as exc:
        raise RuntimeError(f"Claude API error: {exc}") from exc

    raw = response.content[0].text.strip()
    logger.debug("Claude raw response: %s", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Attempt to extract JSON if Claude wrapped it in markdown code fences
        import re
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
            except json.JSONDecodeError:
                raise RuntimeError(
                    f"Claude returned non-JSON response: {raw[:200]}"
                ) from exc
        else:
            raise RuntimeError(
                f"Claude returned non-JSON response: {raw[:200]}"
            ) from exc

    return Summary(
        title=str(data.get("title", "(Untitled)")),
        takeaways=[str(t) for t in data.get("takeaways", [])],
        action_items=[str(a) for a in data.get("action_items", [])],
    )
