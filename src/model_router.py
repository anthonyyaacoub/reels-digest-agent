"""Cost-aware Anthropic SDK wrapper with executor/advisor model tiering.

Two thin wrappers around ``messages.create`` for the dual-model pattern:

- ``call_executor`` — fast cheap model (default ``claude-haiku-4-5``) for
  extraction, classification, mechanical work.
- ``call_advisor`` — smarter model (default ``claude-sonnet-4-6``) for
  synthesis, architectural decisions, free-form writing that requires
  cross-referencing multiple sources.

Both wrappers append a JSONL line per call to ``data/cost_log.jsonl`` with
``input_tokens``, ``output_tokens``, ``model``, ``label``, ``latency_ms`` so
cost-vs-quality tradeoffs stay auditable. Models are swappable via the
``EXECUTOR_MODEL`` / ``ADVISOR_MODEL`` environment variables.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from anthropic import Anthropic

from .config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

_DEFAULT_EXECUTOR_MODEL = "claude-haiku-4-5"
_DEFAULT_ADVISOR_MODEL = "claude-sonnet-4-6"

EXECUTOR_MODEL = os.getenv("EXECUTOR_MODEL", _DEFAULT_EXECUTOR_MODEL)
ADVISOR_MODEL = os.getenv("ADVISOR_MODEL", _DEFAULT_ADVISOR_MODEL)

_COST_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "cost_log.jsonl"

# Module-level singleton — same pattern as src/transcriber.py and src/summarizer.py
_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def log_cost(record: dict) -> None:
    """Append one JSONL line to data/cost_log.jsonl. Best-effort, never raises."""
    try:
        record["ts"] = datetime.now(timezone.utc).isoformat()
        _COST_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _COST_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as exc:
        logger.warning("cost_log write failed: %s", exc)


def _call(model: str, prompt: str, max_tokens: int, label: str, **kwargs) -> str:
    """Run a single-message Anthropic call; return the concatenated text response."""
    t0 = time.monotonic()
    client = _get_client()
    error: str | None = None
    input_tokens = 0
    output_tokens = 0
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text"))
        usage = getattr(resp, "usage", None)
        if usage is not None:
            input_tokens = getattr(usage, "input_tokens", 0) or 0
            output_tokens = getattr(usage, "output_tokens", 0) or 0
        return text
    except Exception as exc:
        error = repr(exc)
        raise
    finally:
        log_cost({
            "model": model,
            "label": label,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "error": error,
        })


def call_executor(prompt: str, max_tokens: int = 1000, label: str = "executor", **kwargs) -> str:
    """Cheap, fast model — use for extraction, classification, mechanical work."""
    return _call(EXECUTOR_MODEL, prompt, max_tokens, label, **kwargs)


def call_advisor(prompt: str, max_tokens: int = 2000, label: str = "advisor", **kwargs) -> str:
    """Smart model — use for synthesis, architectural decisions, gap analysis."""
    return _call(ADVISOR_MODEL, prompt, max_tokens, label, **kwargs)
