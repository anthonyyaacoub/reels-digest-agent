"""Extract visual content from Instagram posts.

For reels (videos): downloads the video and extracts keyframes via ffmpeg.
For carousel posts: downloads all images.
Returns a bounded list of image paths suitable for Claude vision input.
"""

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import yt_dlp

from .config import DOWNLOAD_DIR, INSTAGRAM_COOKIES_FILE

logger = logging.getLogger(__name__)

MAX_IMAGES_PER_POST = 6
KEYFRAMES_PER_VIDEO = 3
_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
        "Mobile/15E148 Safari/604.1"
    )
}


def _find_ffmpeg() -> Optional[str]:
    """Return absolute path to the ffmpeg executable, or None if not found."""
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path
    winget_base = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    for exe in winget_base.rglob("ffmpeg.exe"):
        return str(exe)
    return None


_FFMPEG = _find_ffmpeg()


def _parse_duration(ffmpeg_stderr: str) -> float:
    """Parse 'Duration: HH:MM:SS.ms' from ffmpeg stderr output."""
    match = re.search(r"Duration:\s+(\d+):(\d+):(\d+\.\d+)", ffmpeg_stderr)
    if not match:
        return 0.0
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def _extract_keyframes(video_path: Path, count: int) -> list[Path]:
    """Extract `count` evenly-spaced JPEG keyframes from a video."""
    if not _FFMPEG:
        logger.warning("ffmpeg not found; skipping keyframe extraction for %s", video_path)
        return []

    probe = subprocess.run(
        [_FFMPEG, "-i", str(video_path)],
        capture_output=True,
        text=True,
    )
    duration = _parse_duration(probe.stderr)
    if duration <= 0:
        duration = 30.0

    interval = duration / (count + 1)
    frames: list[Path] = []
    for i in range(count):
        ts = interval * (i + 1)
        out = video_path.with_name(f"{video_path.stem}_frame{i:02d}.jpg")
        result = subprocess.run(
            [
                _FFMPEG, "-y", "-ss", f"{ts:.2f}",
                "-i", str(video_path),
                "-vframes", "1", "-q:v", "3",
                str(out),
            ],
            capture_output=True,
        )
        if result.returncode == 0 and out.exists():
            frames.append(out)
        else:
            logger.warning("ffmpeg failed to extract frame at %.2fs from %s", ts, video_path)
    return frames


def extract_visuals(reel_id: str, url: str) -> list[Path]:
    """Download a post's media and return a list of image paths.

    Videos are reduced to keyframes and the source video is deleted.
    Carousel images are returned as-is. Total output is capped at
    ``MAX_IMAGES_PER_POST`` to bound vision-token cost.
    Returns an empty list if extraction fails — callers should treat
    visuals as best-effort.
    """
    output_template = str(
        DOWNLOAD_DIR / f"{reel_id}_visual_%(autonumber)02d.%(ext)s"
    )
    ffmpeg_dir = str(Path(_FFMPEG).parent) if _FFMPEG else None

    ydl_opts: dict = {
        "format": "best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": False,
        "http_headers": _HTTP_HEADERS,
        "socket_timeout": 30,
        "retries": 3,
        **({"ffmpeg_location": ffmpeg_dir} if ffmpeg_dir else {}),
        **({"cookiefile": str(INSTAGRAM_COOKIES_FILE)} if INSTAGRAM_COOKIES_FILE.exists() else {}),
    }

    logger.info("Extracting visuals for %s from %s", reel_id, url)
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as exc:
        logger.warning("Visual extraction failed for %s: %s", reel_id, exc)
        return []
    except Exception as exc:
        logger.warning("Unexpected visual-extraction error for %s: %s", reel_id, exc)
        return []

    images: list[Path] = []
    downloaded = sorted(DOWNLOAD_DIR.glob(f"{reel_id}_visual_*"))
    for path in downloaded:
        if len(images) >= MAX_IMAGES_PER_POST:
            break
        suffix = path.suffix.lower()
        if suffix in _IMAGE_EXTS:
            images.append(path)
        elif suffix in _VIDEO_EXTS:
            for frame in _extract_keyframes(path, KEYFRAMES_PER_VIDEO):
                if len(images) >= MAX_IMAGES_PER_POST:
                    break
                images.append(frame)
            try:
                path.unlink()
            except OSError:
                pass

    logger.info("Extracted %d visual(s) for %s", len(images), reel_id)
    return images


def cleanup_visuals(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not delete %s: %s", path, exc)
