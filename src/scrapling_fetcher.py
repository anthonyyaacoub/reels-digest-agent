"""Scrapling-based fast first-tier article fetcher.

Tries Scrapling's HTTP-level Fetcher first; escalates to StealthyFetcher only
when the response looks like a Cloudflare interstitial. Returns the same
ArticleData shape as generic_article_fetcher.fetch_generic_article so callers
can swap fetchers without touching downstream code.

Scope: NOT a Medium fetcher. The Medium logged-in cookie path needs Playwright
storage_state, which Scrapling doesn't accept. Caller is responsible for
routing Medium URLs to the Playwright fetcher.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from readability import Document

from .article_fetcher import ArticleData, _HEADERS, _best_src, _best_srcset, _extract_article
from .generic_article_fetcher import log_fetch

logger = logging.getLogger(__name__)

_MIN_BODY_CHARS = 1500

# Cloudflare interstitial fingerprints. The page's body is short and contains
# one of these tells while we're still being challenged.
_CF_TELLS = (
    "Just a moment...",
    "Checking your browser",
    "cf-browser-verification",
    "cf_chl_opt",
    "challenge-platform",
)


def _looks_like_cf_challenge(html: str) -> bool:
    if not html or len(html) > 50_000:
        return False
    return any(tell in html for tell in _CF_TELLS)


def _meta(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        for attrs in ({"name": name}, {"property": name}):
            tag = soup.find("meta", attrs=attrs)
            if tag and tag.get("content"):
                return tag["content"].strip()
    return ""


def _collect_fallback_images(full_soup: BeautifulSoup, base_url: str, already: set[str]) -> list[tuple[str, str]]:
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


def _fetch_html(url: str) -> tuple[str, str, str]:
    """Return (final_url, html, fetcher_label). Escalates to StealthyFetcher on CF challenge."""
    # Lazy-import so this module loads even before scrapling is installed
    from scrapling.fetchers import Fetcher, StealthyFetcher

    fetcher_label = "scrapling"
    page = Fetcher.get(url)
    html = str(page.html_content) if page.html_content is not None else ""
    final_url = str(getattr(page, "url", url))

    if _looks_like_cf_challenge(html):
        logger.info("Scrapling: CF challenge detected, escalating to StealthyFetcher for %s", url)
        page = StealthyFetcher.fetch(url, headless=True)
        html = str(page.html_content) if page.html_content is not None else ""
        final_url = str(getattr(page, "url", url))
        fetcher_label = "scrapling+stealth"

    return final_url, html, fetcher_label


def fetch_with_scrapling(url: str) -> ArticleData:
    """Fetch and extract an article using Scrapling. Mirrors fetch_generic_article's shape."""
    t0 = time.monotonic()
    fetcher_label = "scrapling"
    body_md = ""
    error: str | None = None
    try:
        final_url, html, fetcher_label = _fetch_html(url)

        full_soup = BeautifulSoup(html, "html.parser")
        title = _meta(full_soup, "og:title", "twitter:title")
        if not title and full_soup.title and full_soup.title.string:
            title = full_soup.title.string.strip()
        author = _meta(full_soup, "author", "article:author", "og:article:author")

        _, _, body_md, image_refs = _extract_article(html, final_url)

        if len(body_md.strip()) < _MIN_BODY_CHARS:
            doc = Document(html)
            summary_html = doc.summary(html_partial=True)
            if not title:
                title = doc.short_title() or "Untitled"
            wrapped = f"<article>{summary_html}</article>"
            _, _, body_md_alt, image_refs_alt = _extract_article(wrapped, final_url)
            if len(body_md_alt.strip()) > len(body_md.strip()):
                logger.info("Scrapling: readability fallback won (%d > %d chars)",
                            len(body_md_alt.strip()), len(body_md.strip()))
                body_md, image_refs = body_md_alt, image_refs_alt
                fetcher_label = f"{fetcher_label}+readability"

        if not title:
            title = "Untitled"

        seen_urls = {img_url for img_url, _ in image_refs}
        fallback = _collect_fallback_images(full_soup, final_url, seen_urls)
        image_refs = image_refs + fallback

        header = f"# {title}\n"
        if author:
            header += f"\n**Author:** {author}\n"
        header += f"\n**Source:** {url}\n\n---\n\n"
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
            url=url,
            content_md=content_md,
            images=images,
        )
    except Exception as exc:
        error = repr(exc)
        raise
    finally:
        log_fetch({
            "url": url,
            "source_url": url,
            "host": (urlparse(url).netloc or "").lower(),
            "fetcher": fetcher_label,
            "fetch_duration_ms": int((time.monotonic() - t0) * 1000),
            "body_chars": len(body_md.strip()),
            "error": error,
        })
