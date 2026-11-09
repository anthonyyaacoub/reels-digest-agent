"""FastAPI webhook server — receives reel URLs from the iOS Shortcut or web UI."""

import logging
import re
import tempfile
from pathlib import Path
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, field_validator

from . import db
from .article_fetcher import build_zip
from .brain_dropper import drop_article
from .config import DOWNLOAD_DIR, WEBHOOK_SECRET
from .generic_article_fetcher import fetch_generic_article

_STATIC = Path(__file__).parent / "static"

logger = logging.getLogger(__name__)

# ── Patterns accepted as valid Instagram reel URLs ────────────────────────────

_INSTAGRAM_PATTERNS = re.compile(
    r"^https?://(www\.)?instagram\.com/(reel|p|tv)/[A-Za-z0-9_\-]+/?",
    re.IGNORECASE,
)

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Reels Digest Agent", version="1.0.0")
_bearer = HTTPBearer()


# ── Auth ──────────────────────────────────────────────────────────────────────

def _verify_token(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> None:
    """Validate the shared webhook secret."""
    if credentials.credentials != WEBHOOK_SECRET:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Authorization token.",
        )


# ── Request / Response models ─────────────────────────────────────────────────

class ReelRequest(BaseModel):
    url: str
    note: Optional[str] = None

    @field_validator("url")
    @classmethod
    def must_be_instagram(cls, v: str) -> str:
        if not _INSTAGRAM_PATTERNS.match(v):
            raise ValueError(
                "URL must be an Instagram reel link "
                "(e.g. https://www.instagram.com/reel/ABC123/)"
            )
        return v.strip()


class QueuedResponse(BaseModel):
    status: str
    id: str


class StatusResponse(BaseModel):
    pending: int
    done: int
    error: int


class ArticleRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def must_be_http(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^https?://", v, re.IGNORECASE):
            raise ValueError("URL must be a valid HTTP(S) link.")
        return v


class ArticleResponse(BaseModel):
    title: str
    author: str
    image_count: int
    zip_path: str


# OS temp dir (/tmp on Linux, %TEMP% on Windows, where Apollo drops reel audio),
# the project data dir, and the Telegram MCP inbox.
_TRANSCRIBE_ROOTS = tuple(
    Path(p).resolve()
    for p in (
        tempfile.gettempdir(),
        Path(__file__).resolve().parent.parent / "data",
        Path.home() / ".claude" / "channels" / "telegram" / "inbox",
    )
)


class TranscribeRequest(BaseModel):
    file_path: str

    @field_validator("file_path")
    @classmethod
    def must_be_safe_path(cls, v: str) -> str:
        v = v.strip()
        # resolve() collapses ".." so a path can't climb out of an allowed dir.
        path = Path(v).resolve()
        if not any(path.is_relative_to(root) for root in _TRANSCRIBE_ROOTS):
            raise ValueError(
                "file_path must live under the temp dir, the project data dir, "
                "or the Telegram MCP inbox."
            )
        return v


class TranscribeResponse(BaseModel):
    text: str


class HarnessEventRequest(BaseModel):
    source: str
    kind: str
    payload: dict | None = None
    outcome: str = "ok"

    @field_validator("source")
    @classmethod
    def must_be_known_source(cls, v: str) -> str:
        if v not in {"telegram-pattern", "hook", "fastapi"}:
            raise ValueError("source must be one of: telegram-pattern, hook, fastapi")
        return v

    @field_validator("outcome")
    @classmethod
    def must_be_known_outcome(cls, v: str) -> str:
        if v not in {"ok", "error", "blocked"}:
            raise ValueError("outcome must be one of: ok, error, blocked")
        return v


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    logger.info("Webhook server started — database ready.")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post(
    "/api/reel",
    response_model=QueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(_verify_token)],
)
def queue_reel(body: ReelRequest) -> QueuedResponse:
    """Accept a reel URL from the iOS Shortcut and add it to the processing queue.

    Existing rows in error state are reset to pending so they get retried on
    the next digest run. Done and pending rows are no-ops (idempotent).
    """
    reel_id, action = db.add_reel(body.url, note=body.note)
    logger.info("POST /api/reel → %s %s", action, reel_id)
    db.add_harness_event(
        source="fastapi",
        kind=f"reel.{action}",
        payload={"reel_id": reel_id, "url": body.url, "has_note": bool(body.note)},
    )
    return QueuedResponse(status=action, id=reel_id)


@app.post(
    "/api/article",
    response_model=ArticleResponse,
    dependencies=[Depends(_verify_token)],
)
def fetch_any_article(body: ArticleRequest) -> ArticleResponse:
    """Fetch an article by URL, write a zip (markdown + images) to disk, return its path.

    Every URL — Medium and otherwise — is rendered in headless Chromium and
    run through readability-lxml. Medium URLs get MEDIUM_SID injected into the
    browser context so member-only stories load just like they would in the
    user's signed-in browser.
    """
    try:
        article = fetch_generic_article(body.url)
    except Exception as exc:
        logger.error("article fetch failed for %s: %s", body.url, exc)
        db.add_harness_event(
            source="fastapi",
            kind="article.fetch_failed",
            payload={"url": body.url, "error": str(exc)},
            outcome="error",
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    zip_bytes = build_zip(article)

    safe_title = re.sub(r"[^\w\s\-]", "", article.title)[:60].strip().replace(" ", "_") or "article"
    articles_dir = DOWNLOAD_DIR / "articles"
    articles_dir.mkdir(parents=True, exist_ok=True)
    zip_file = articles_dir / f"{safe_title}.zip"
    zip_file.write_bytes(zip_bytes)

    logger.info("POST /api/article → wrote %s (%d images)", zip_file, len(article.images))
    db.add_harness_event(
        source="fastapi",
        kind="article.fetched",
        payload={
            "url": body.url,
            "title": article.title,
            "image_count": len(article.images),
            "body_chars": len(article.content_md),
        },
    )

    drop_article(article.title, article.author, article.url, article.content_md)

    return ArticleResponse(
        title=article.title,
        author=article.author,
        image_count=len(article.images),
        zip_path=str(zip_file),
    )


@app.post(
    "/api/transcribe",
    response_model=TranscribeResponse,
    dependencies=[Depends(_verify_token)],
)
def transcribe_audio(body: TranscribeRequest) -> TranscribeResponse:
    """Transcribe a local audio file via the shared Whisper singleton.

    Used by the Telegram MCP front door to handle voice messages — the plugin
    downloads the attachment to /tmp, then this endpoint runs Whisper on it.
    """
    from .transcriber import transcribe

    path = Path(body.file_path)
    if not path.exists():
        raise HTTPException(status_code=400, detail=f"File not found: {body.file_path}")
    try:
        text = transcribe(path)
    except Exception as exc:
        logger.error("transcription failed for %s: %s", body.file_path, exc)
        db.add_harness_event(
            source="fastapi",
            kind="transcribe.failed",
            payload={"file_name": path.name, "error": str(exc)},
            outcome="error",
        )
        raise HTTPException(status_code=500, detail=f"Transcription failed: {exc}") from exc

    logger.info("POST /api/transcribe → %d chars from %s", len(text), path.name)
    db.add_harness_event(
        source="fastapi",
        kind="transcribe.ok",
        payload={"file_name": path.name, "chars": len(text)},
    )
    return TranscribeResponse(text=text)


@app.post(
    "/api/harness-event",
    dependencies=[Depends(_verify_token)],
)
def post_harness_event(body: HarnessEventRequest) -> dict:
    """Append one row to harness_events. Used by Claude Code hooks."""
    db.add_harness_event(
        source=body.source,
        kind=body.kind,
        payload=body.payload,
        outcome=body.outcome,
    )
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def web_ui() -> HTMLResponse:
    """Serve the mobile web UI for queuing reels."""
    html = (_STATIC / "index.html").read_text()
    html = html.replace("WEBHOOK_SECRET_PLACEHOLDER", WEBHOOK_SECRET)
    return HTMLResponse(content=html)


@app.get("/api/status", response_model=StatusResponse)
def get_status() -> StatusResponse:
    """Return counts of reels by processing status."""
    counts = db.get_reel_counts()
    return StatusResponse(
        pending=counts.get("pending", 0),
        done=counts.get("done", 0),
        error=counts.get("error", 0),
    )
