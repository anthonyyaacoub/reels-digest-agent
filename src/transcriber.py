"""Local Whisper transcription — model is loaded once and kept in memory."""

import logging
from pathlib import Path
from typing import Optional

import whisper

from .config import WHISPER_MODEL

logger = logging.getLogger(__name__)

# ── Singleton model ───────────────────────────────────────────────────────────

_model: Optional[whisper.Whisper] = None


def _get_model() -> whisper.Whisper:
    """Load (or return cached) Whisper model."""
    global _model
    if _model is None:
        logger.info("Loading Whisper model '%s' — this may take a moment…", WHISPER_MODEL)
        _model = whisper.load_model(WHISPER_MODEL)
        logger.info("Whisper model loaded.")
    return _model


# ── Public API ────────────────────────────────────────────────────────────────

def transcribe(audio_path: Path) -> str:
    """
    Transcribe an audio file using the local Whisper model.

    Parameters
    ----------
    audio_path:
        Path to the ``.mp3`` (or any Whisper-compatible) audio file.

    Returns
    -------
    str
        Full transcript text.  If Whisper produces segment-level timestamps,
        they are prepended to each segment in ``[MM:SS]`` format.

    Raises
    ------
    FileNotFoundError
        If *audio_path* does not exist.
    RuntimeError
        If Whisper fails to transcribe the file.
    """
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    logger.info("Transcribing %s", audio_path.name)
    model = _get_model()

    try:
        result = model.transcribe(
            str(audio_path),
            fp16=False,        # safe default — GPU fp16 can be enabled manually
            verbose=False,
        )
    except Exception as exc:
        raise RuntimeError(f"Whisper transcription failed for {audio_path}: {exc}") from exc

    segments = result.get("segments", [])

    if segments:
        lines: list[str] = []
        for seg in segments:
            start_sec = int(seg.get("start", 0))
            minutes, seconds = divmod(start_sec, 60)
            timestamp = f"[{minutes:02d}:{seconds:02d}]"
            text = seg.get("text", "").strip()
            lines.append(f"{timestamp} {text}")
        transcript = "\n".join(lines)
    else:
        transcript = result.get("text", "").strip()

    logger.info(
        "Transcription complete for %s — %d characters",
        audio_path.name,
        len(transcript),
    )
    return transcript
