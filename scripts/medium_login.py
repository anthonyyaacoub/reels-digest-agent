"""One-time Medium login. Saves the full Playwright session (cookies +
localStorage) to data/medium_storage_state.json so the article fetcher can
read member-only stories the same way the user's signed-in browser does.

Run from the project root with the venv active:

    python scripts/medium_login.py

A Chromium window opens; sign in to Medium normally (Google / email link / etc.),
then come back to the terminal and press Enter. Re-run only if Medium expires
the session (rare).

Cloudflare on Medium may still flag this even with anti-detection patches
applied below — Playwright's headless-shell binary has too many tells. If you
get stuck on the "verify you are human" loop, fall back to importing cookies
from your real browser via scripts/import_medium_cookies.py.
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_FILE = BASE_DIR / "data" / "medium_storage_state.json"

# Modern Chrome on Linux UA — matches what Cloudflare expects from a normal
# desktop browser, not the Playwright default which advertises HeadlessChrome.
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_HIDE_AUTOMATION_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || { runtime: {} };
"""


def main() -> int:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        try:
            context = browser.new_context(
                locale="en-US",
                user_agent=_USER_AGENT,
                viewport={"width": 1366, "height": 800},
            )
            context.add_init_script(_HIDE_AUTOMATION_JS)
            page = context.new_page()
            page.goto("https://medium.com/m/signin", wait_until="domcontentloaded")
            print("Chromium is open. Sign in to Medium, wait until you see your homepage, then return here.")
            print("If you get stuck on a Cloudflare 'verify you are human' loop, close this and run")
            print("scripts/import_medium_cookies.py instead — that path bypasses bot detection entirely.")
            input("Press Enter once you're signed in… ")
            context.storage_state(path=str(STATE_FILE))
            print(f"Saved Medium session to {STATE_FILE}")
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
