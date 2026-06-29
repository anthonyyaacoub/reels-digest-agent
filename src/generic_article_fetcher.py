"""Fetch arbitrary articles by rendering the page in headless Chromium.

A real browser session sidesteps most lightweight bot blocks and lets the
MEDIUM_SID cookie ride into Medium's member-only stories the same way the
user's own browser does. readability-lxml isolates the article body for the
markdown conversion; image collection scans the original article subtree as a
backstop in case readability strips inline images.
"""

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from readability import Document

from .article_fetcher import ArticleData, _HEADERS, _best_src, _best_srcset, _extract_article
from .config import MEDIUM_SID

logger = logging.getLogger(__name__)

_NAV_TIMEOUT_MS = 30_000

_FETCH_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "fetch_log.jsonl"


def log_fetch(record: dict) -> None:
    """Append one JSONL line to data/fetch_log.jsonl. Best-effort, never raises.

    Public so alternative fetchers (scrapling_fetcher, etc.) can write into the
    same log for cross-fetcher comparison.
    """
    try:
        record["ts"] = datetime.now(timezone.utc).isoformat()
        _FETCH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _FETCH_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as exc:
        logger.warning("fetch_log write failed: %s", exc)

# Full Playwright session captured by scripts/medium_login.py. When present,
# every Medium request loads from this state — far more reliable than just
# injecting MEDIUM_SID, since Medium needs uid/xsrf/localStorage too for
# member-only stories. Falls back to sid-only when the file doesn't exist.
_MEDIUM_STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "medium_storage_state.json"

# Inject into the page after navigation: walks every <img>, picks the best
# real URL from the lazy-load attrs Medium / Substack / Ghost / WordPress all
# use, and rewrites img.src so the post-render HTML has resolvable srcs.
_NORMALIZE_LAZY_IMAGES_JS = """
() => {
    for (const img of document.querySelectorAll('img')) {
        const candidates = [
            img.getAttribute('src'),
            img.dataset && img.dataset.src,
            img.dataset && img.dataset.lazySrc,
            img.dataset && img.dataset.original,
            img.getAttribute('data-src'),
            img.getAttribute('data-lazy-src'),
            img.getAttribute('data-original'),
            img.getAttribute('data-hi-res-src'),
        ].filter(Boolean);
        const real = candidates.find(s => s && !s.startsWith('data:') && !s.startsWith('blob:'));
        if (real && real !== img.getAttribute('src')) {
            img.setAttribute('src', real);
        }
        const srcset = img.getAttribute('srcset') || '';
        const currentSrc = img.getAttribute('src') || '';
        if (srcset && (!currentSrc || currentSrc.startsWith('data:'))) {
            const parts = srcset.split(',').map(s => s.trim().split(/\\s+/));
            const last = parts[parts.length - 1];
            if (last && last[0]) img.setAttribute('src', last[0]);
        }
    }
}
"""

_PROGRESSIVE_SCROLL_JS = """
async () => {
    const step = 600;
    const delay = 250;
    let pos = 0;
    while (pos < document.body.scrollHeight) {
        window.scrollTo(0, pos);
        await new Promise(r => setTimeout(r, delay));
        pos += step;
    }
    window.scrollTo(0, document.body.scrollHeight);
    await new Promise(r => setTimeout(r, 600));
    window.scrollTo(0, 0);
}
"""


def _render_html(url: str) -> tuple[str, str]:
    """Render the URL in headless Chromium and return (final_url, html)."""
    is_medium = "medium.com" in (urlparse(url).netloc or "").lower()
    use_storage_state = is_medium and _MEDIUM_STATE_FILE.exists()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context_kwargs = {
                "user_agent": _HEADERS["User-Agent"],
                "locale": "en-US",
                "viewport": {"width": 1280, "height": 1800},
            }
            if use_storage_state:
                context_kwargs["storage_state"] = str(_MEDIUM_STATE_FILE)
            context = browser.new_context(**context_kwargs)
            if is_medium and not use_storage_state and MEDIUM_SID:
                context.add_cookies([
                    {"name": "sid", "value": MEDIUM_SID, "domain": ".medium.com", "path": "/"},
                ])
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
            try:
                page.wait_for_load_state("networkidle", timeout=_NAV_TIMEOUT_MS)
            except Exception:
                pass
            page.evaluate(_PROGRESSIVE_SCROLL_JS)
            try:
                page.wait_for_load_state("networkidle", timeout=_NAV_TIMEOUT_MS)
            except Exception:
                pass
            page.evaluate(_NORMALIZE_LAZY_IMAGES_JS)
            return page.url, page.content()
        finally:
            browser.close()


def _meta(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        for attrs in ({"name": name}, {"property": name}):
            tag = soup.find("meta", attrs=attrs)
            if tag and tag.get("content"):
                return tag["content"].strip()
    return ""


def _collect_fallback_images(full_soup: BeautifulSoup, base_url: str, already: set[str]) -> list[tuple[str, str]]:
    """Walk the full-page article/main subtree for images readability stripped."""
    container = full_soup.find("article") or full_soup.find("main") or full_soup.body
    if container is None:
        return []
    extra: list[tuple[str, str]] = []
    for node in container.find_all(["img", "picture"]):
        if node.name == "picture":
            alt = ""
            inner_img = node.find("img")
            if inner_img:
                alt = (inner_img.get("alt") or "").strip()
            src = ""
            for source in node.find_all("source"):
                src = _best_srcset(source.get("srcset", ""), base_url)
                if src:
                    break
            if not src and inner_img:
                src = _best_src(inner_img, base_url)
        else:
            alt = (node.get("alt") or "").strip()
            src = _best_src(node, base_url)
        if not src or src in already:
            continue
        width = node.get("width", "")
        if width and str(width).isdigit() and int(width) < 40:
            continue
        extra.append((src, alt))
        already.add(src)
    return extra


_MIN_BODY_CHARS = 1500
_CANONICAL_BODY_MULTIPLIER = 1.5
# Header (title/author/source/separator) adds ~100 chars to content_md, so the
# acceptable threshold on full content_md is set slightly above _MIN_BODY_CHARS.
_SCRAPLING_MIN_CONTENT_CHARS = 1600

# Medium post IDs are 12-char hex tails on the URL slug. Custom-domain
# publications (ai.plainenglish.io, betterprogramming.pub, etc.) keep this
# pattern, so we can detect them and re-fetch via medium.com/p/<id> where
# our logged-in cookies actually apply.
_MEDIUM_POST_ID_RE = re.compile(r"-([0-9a-f]{12})(?:[/?#]|$)")


def _extract_medium_post_id(url: str) -> str | None:
    m = _MEDIUM_POST_ID_RE.search(url)
    return m.group(1) if m else None


def _is_medium_family(url: str) -> bool:
    """True for medium.com proper or any custom-domain Medium publication.

    Used to skip the Scrapling fast tier for Medium URLs — they need cookie
    injection (medium.com proper) or the canonical-medium.com/p/<id> retry
    trick (custom-domain pubs), neither of which Scrapling supports.
    """
    host = (urlparse(url).netloc or "").lower()
    if "medium.com" in host:
        return True
    return _extract_medium_post_id(url) is not None


def fetch_generic_article(url: str) -> ArticleData:
    # Fast tier: Scrapling for non-Medium URLs. Falls through on short body or
    # any exception. Medium-family always uses Playwright (cookies + canonical
    # retry).
    if not _is_medium_family(url):
        try:
            from .scrapling_fetcher import fetch_with_scrapling
            fast = fetch_with_scrapling(url)
            if len(fast.content_md) >= _SCRAPLING_MIN_CONTENT_CHARS:
                return fast
            logger.info("Scrapling returned %d chars (<%d), falling back to Playwright for %s",
                        len(fast.content_md), _SCRAPLING_MIN_CONTENT_CHARS, url)
        except Exception as exc:
            logger.warning("Scrapling fast tier failed for %s: %s — falling back to Playwright", url, exc)

    article = _fetch_to_article_data(url, source_url=url)

    host = (urlparse(url).netloc or "").lower()
    if "medium.com" not in host and _MEDIUM_STATE_FILE.exists():
        post_id = _extract_medium_post_id(url)
        if post_id:
            canonical = f"https://medium.com/p/{post_id}"
            logger.info("Custom-domain URL on %s — also trying canonical %s", host, canonical)
            try:
                alt = _fetch_to_article_data(canonical, source_url=url)
                if len(alt.content_md) > len(article.content_md) * _CANONICAL_BODY_MULTIPLIER:
                    logger.info("Canonical Medium fetch won (%d > %d chars)",
                                len(alt.content_md), len(article.content_md))
                    return alt
            except Exception as exc:
                logger.warning("Canonical Medium fetch failed for %s: %s", canonical, exc)

    return article


def _fetch_to_article_data(fetch_url: str, source_url: str) -> ArticleData:
    """Render fetch_url, extract, return ArticleData with source_url in the header."""
    t0 = time.monotonic()
    fetcher_label = "playwright"
    body_md = ""
    error: str | None = None
    try:
        final_url, html = _render_html(fetch_url)

        full_soup = BeautifulSoup(html, "html.parser")
        title = _meta(full_soup, "og:title", "twitter:title")
        if not title and full_soup.title and full_soup.title.string:
            title = full_soup.title.string.strip()
        author = _meta(full_soup, "author", "article:author", "og:article:author")

        # Try <article>/<main> extraction first — this is what works for Medium and
        # most modern article sites. Readability is only the fallback for pages
        # that lack semantic article tags or wrap them in noise.
        _, _, body_md, image_refs = _extract_article(html, final_url)

        if len(body_md.strip()) < _MIN_BODY_CHARS:
            doc = Document(html)
            summary_html = doc.summary(html_partial=True)
            if not title:
                title = doc.short_title() or "Untitled"
            wrapped = f"<article>{summary_html}</article>"
            _, _, body_md_alt, image_refs_alt = _extract_article(wrapped, final_url)
            if len(body_md_alt.strip()) > len(body_md.strip()):
                logger.info("Generic fetcher: readability fallback won (%d > %d chars)",
                            len(body_md_alt.strip()), len(body_md.strip()))
                body_md, image_refs = body_md_alt, image_refs_alt
                fetcher_label = "playwright+readability"

        if not title:
            title = "Untitled"

        seen_urls = {img_url for img_url, _ in image_refs}
        fallback = _collect_fallback_images(full_soup, final_url, seen_urls)
        if fallback:
            logger.info("Generic fetcher: extractor returned %d images, full-DOM walk added %d", len(image_refs), len(fallback))
        image_refs = image_refs + fallback

        header = f"# {title}\n"
        if author:
            header += f"\n**Author:** {author}\n"
        header += f"\n**Source:** {source_url}\n\n---\n\n"
        content_md = header + body_md

        images: dict[str, bytes] = {}
        sess = requests.Session()
        sess.headers.update(_HEADERS)
        for i, (img_url, _alt) in enumerate(image_refs, start=1):
            suffix = Path(urlparse(img_url).path).suffix or ".jpg"
            if suffix.lower() not in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".avif"}:
                suffix = ".jpg"
            fname = f"image_{i:03d}{suffix}"
            try:
                r = sess.get(img_url, timeout=15)
                r.raise_for_status()
                images[fname] = r.content
            except Exception as exc:
                logger.warning("Could not download image %s: %s", img_url, exc)

        return ArticleData(
            title=title,
            author=author,
            url=source_url,
            content_md=content_md,
            images=images,
        )
    except Exception as exc:
        error = repr(exc)
        raise
    finally:
        log_fetch({
            "url": fetch_url,
            "source_url": source_url,
            "host": (urlparse(fetch_url).netloc or "").lower(),
            "fetcher": fetcher_label,
            "fetch_duration_ms": int((time.monotonic() - t0) * 1000),
            "body_chars": len(body_md.strip()),
            "error": error,
        })
