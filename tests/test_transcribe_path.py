"""/api/transcribe path guard: OS temp dir passes (Windows %TEMP% included), anything
outside the allowed roots fails, ".." can't climb out. Run: python tests/test_transcribe_path.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError  # noqa: E402

from src.server import TranscribeRequest  # noqa: E402

TEMP = Path(tempfile.gettempdir())


def rejected(path: str) -> bool:
    try:
        TranscribeRequest(file_path=path)
        return False
    except ValidationError:
        return True


assert not rejected(str(TEMP / "apollo-video-abc" / "audio.mp3")), "temp dir must pass"
assert rejected(str(Path.home() / "secrets.txt")), "home dir must fail"
assert rejected(str(TEMP / ".." / ".." / "secrets.txt")), "'..' must not escape"
print("ok")
