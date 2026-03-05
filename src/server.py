"""FastAPI webhook server — receives reel URLs from the iOS Shortcut or web UI."""

import logging
import re
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, field_validator

from . import db
from .config import WEBHOOK_SECRET

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
    """Accept a reel URL from the iOS Shortcut and add it to the processing queue."""
    reel_id = db.add_reel(body.url)
    logger.info("POST /api/reel → queued %s", reel_id)
    return QueuedResponse(status="queued", id=reel_id)


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
