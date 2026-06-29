"""Import Medium cookies from your real browser — no Playwright, no bot detection.

One-time setup:

1. Install the "Cookie-Editor" extension in your normal browser (Chrome / Edge /
   Firefox) — https://cookie-editor.com
2. Visit https://medium.com — you should already be signed in.
3. Click the Cookie-Editor toolbar icon.
4. Click "Export" → choose "JSON" → it copies the cookie array to your clipboard.
5. Paste the clipboard contents into a file inside the repo, e.g.
   ~/projects/reels-digest-agent/data/medium_cookies.json
6. Run:

    python scripts/import_medium_cookies.py data/medium_cookies.json

The script writes data/medium_storage_state.json — the article fetcher loads
that automatically for medium.com URLs. Re-run if Medium ever invalidates the
session (logout from another device, etc.).
"""

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_FILE = BASE_DIR / "data" / "medium_storage_state.json"

# Cookie-Editor and Playwright disagree on sameSite spelling.
_SAMESITE_MAP = {
    "no_restriction": "None",
    "none": "None",
    "lax": "Lax",
    "strict": "Strict",
    "unspecified": "Lax",
    "": "Lax",
}


def _convert(cookie: dict) -> dict | None:
    name = cookie.get("name")
    value = cookie.get("value")
    if not name or value is None:
        return None
    out = {
        "name": name,
        "value": value,
        "domain": cookie.get("domain", ""),
        "path": cookie.get("path", "/"),
        "httpOnly": bool(cookie.get("httpOnly", False)),
        "secure": bool(cookie.get("secure", False)),
        "sameSite": _SAMESITE_MAP.get(str(cookie.get("sameSite", "")).lower(), "Lax"),
    }
    exp = cookie.get("expirationDate")
    if exp is not None:
        try:
            out["expires"] = float(exp)
        except (TypeError, ValueError):
            pass
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python scripts/import_medium_cookies.py <cookies.json>", file=sys.stderr)
        return 2
    src = Path(argv[1]).expanduser()
    if not src.is_absolute():
        src = (BASE_DIR / src).resolve()
    if not src.exists():
        print(f"File not found: {src}", file=sys.stderr)
        return 1
    raw = json.loads(src.read_text())
    if not isinstance(raw, list):
        print("Expected a JSON array of cookies (Cookie-Editor JSON export).", file=sys.stderr)
        return 1
    converted = [c for c in (_convert(x) for x in raw) if c]
    medium_cookies = [c for c in converted if "medium.com" in c["domain"].lower()]
    if not medium_cookies:
        print("No medium.com cookies found in the export.", file=sys.stderr)
        return 1
    state = {"cookies": medium_cookies, "origins": []}
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))
    print(f"Wrote {len(medium_cookies)} medium.com cookies to {STATE_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
