"""yt-dlp wrapper: download a reel and extract its audio as an MP3."""

import logging
import shutil
from pathlib import Path
from typing import Optional

import yt_dlp

from .config import DOWNLOAD_DIR, INSTAGRAM_COOKIES_FILE

# Resolve ffmpeg: prefer PATH, fall back to the WinGet install location
def _find_ffmpeg() -> Optional[str]:
    if shutil.which("ffmpeg"):
        return None  # yt-dlp will find it itself
    winget_path = (
        Path.home()
        / "AppData/Local/Microsoft/WinGet/Packages"
    )
    for exe in winget_path.rglob("ffmpeg.exe"):
        return str(exe.parent)
    return None

_FFMPEG_LOCATION = _find_ffmpeg()

logger = logging.getLogger(__name__)


def download_audio(reel_id: str, url: str) -> Path:
    """
    Download the reel at *url* and extract audio to an MP3 file.

    Parameters
    ----------
    reel_id:
        Short identifier used as the output filename stem.
    url:
        Public Instagram reel URL.

    Returns
    -------
    Path
        Absolute path to the extracted ``.mp3`` file.

    Raises
    ------
    RuntimeError
        If yt-dlp cannot download or extract the audio (private/deleted reel,
        network error, etc.).
    """
    output_template = str(DOWNLOAD_DIR / f"{reel_id}.%(ext)s")
    audio_path = DOWNLOAD_DIR / f"{reel_id}.mp3"

    ydl_opts: dict = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": False,
        **({"ffmpeg_location": _FFMPEG_LOCATION} if _FFMPEG_LOCATION else {}),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "128",
            }
        ],
        # Avoid rate-limiting / login walls where possible
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                "Mobile/15E148 Safari/604.1"
            )
        },
        "socket_timeout": 30,
        "retries": 3,
        **({"cookiefile": str(INSTAGRAM_COOKIES_FILE)} if INSTAGRAM_COOKIES_FILE.exists() else {}),
    }

    logger.info("Downloading reel %s from %s", reel_id, url)
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as exc:
        raise RuntimeError(f"yt-dlp failed to download {url}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"Unexpected error while downloading {url}: {exc}") from exc

    if not audio_path.exists():
        raise RuntimeError(
            f"Audio extraction succeeded but expected file not found: {audio_path}"
        )

    logger.info("Audio saved to %s (%.1f KB)", audio_path, audio_path.stat().st_size / 1024)
    return audio_path


def fetch_caption(url: str) -> str:
    """Return the post caption via yt-dlp metadata-only extraction.

    Used as a last-resort signal when audio + visuals both fail (typical for
    image-only carousels that yt-dlp can't pull image URLs from). Returns ""
    on any error so callers can fall through quietly.
    """
    from .config import INSTAGRAM_COOKIES_FILE

    ydl_opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignore_no_formats_error": True,
        "socket_timeout": 30,
        **({"cookiefile": str(INSTAGRAM_COOKIES_FILE)} if INSTAGRAM_COOKIES_FILE.exists() else {}),
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return (info.get("description") or "").strip()
    except Exception as exc:
        logger.warning("fetch_caption failed for %s: %s", url, exc)
        return ""


def cleanup_audio(audio_path: Path) -> None:
    """Delete the audio file after transcription to free disk space."""
    try:
        audio_path.unlink(missing_ok=True)
        logger.debug("Deleted audio file %s", audio_path)
    except OSError as exc:
        logger.warning("Could not delete %s: %s", audio_path, exc)
