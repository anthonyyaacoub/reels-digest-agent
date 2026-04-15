"""Fetch and parse Medium articles into clean Markdown documents."""

import io
import logging
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .config import MEDIUM_SID

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_MEDIUM_RE = re.compile(
    r"https?://(www\.)?medium\.com/|"
    r"https?://[a-zA-Z0-9\-]+\.medium\.com/",
    re.IGNORECASE,
)

_FREEDIUM_BASE = "https://freedium.cfd/"


@dataclass
class ArticleData:
    title: str
    author: str
    url: str
    content_md: str
    images: dict[str, bytes] = field(default_factory=dict)  # filename -> bytes


def is_medium_url(url: str) -> bool:
    return bool(_MEDIUM_RE.match(url))


def _freedium_url(url: str) -> str:
    return _FREEDIUM_BASE + url


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_HEADERS)
    if MEDIUM_SID:
        s.cookies.set("sid", MEDIUM_SID, domain=".medium.com")
        s.cookies.set("uid", "", domain=".medium.com")  # required alongside sid
    return s


def _fetch_html(url: str) -> str:
    resp = _session().get(url, timeout=20)
    resp.raise_for_status()
    return resp.text


def _download_image(url: str) -> bytes | None:
    try:
        resp = _session().get(url, timeout=15)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        logger.warning("Could not download image %s: %s", url, exc)
        return None


def _best_srcset(srcset: str, base_url: str) -> str:
    """Pick the highest-resolution URL from a srcset string."""
    if not srcset:
        return ""
    best_url, best_w = "", 0
    for part in srcset.split(","):
        part = part.strip()
        if not part:
            continue
        tokens = part.split()
        url = tokens[0]
        w = 0
        if len(tokens) > 1:
            descriptor = tokens[1]
            if descriptor.endswith("w") and descriptor[:-1].isdigit():
                w = int(descriptor[:-1])
            elif descriptor.endswith("x"):
                try:
                    w = int(float(descriptor[:-1]) * 1000)
                except ValueError:
                    pass
        if w > best_w or (w == 0 and not best_url):
            best_w, best_url = w, url
    return urljoin(base_url, best_url) if best_url else ""


def _best_src(img_node, base_url: str) -> str:
    """Return the best image URL from an <img> tag, preferring srcset over src."""
    srcset = img_node.get("srcset", "")
    if srcset:
        result = _best_srcset(srcset, base_url)
        if result:
            return result
    src = img_node.get("src") or img_node.get("data-src") or ""
    # Ignore base64 placeholders and data URIs (they're blurred previews)
    if src.startswith("data:"):
        return ""
    return urljoin(base_url, src) if src else ""


def _extract_article(html: str, base_url: str) -> tuple[str, str, str, list[tuple[str, str]]]:
    """Return (title, author, body_md, [(img_url, alt_text), ...])."""
    soup = BeautifulSoup(html, "html.parser")

    # Title
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        og = soup.find("meta", property="og:title")
        title = og["content"].strip() if og and og.get("content") else "Untitled"

    # Author
    author = ""
    author_meta = soup.find("meta", attrs={"name": "author"})
    if author_meta and author_meta.get("content"):
        author = author_meta["content"].strip()
    if not author:
        # Try common Medium author selectors
        for sel in ["[data-testid='authorName']", ".pw-author-name", "[rel='author']"]:
            el = soup.select_one(sel)
            if el:
                author = el.get_text(strip=True)
                break

    # Main article body
    article = (
        soup.find("article")
        or soup.find("main")
        or soup.find("div", class_=re.compile(r"post-content|article-content|story", re.I))
        or soup.body
    )

    images: list[tuple[str, str]] = []
    md_lines: list[str] = []

    def _process_node(node) -> str:
        if isinstance(node, str):
            return node.strip()

        tag = node.name
        if tag is None:
            return ""

        if tag in ("script", "style", "nav", "footer", "header", "aside"):
            return ""

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            text = node.get_text(strip=True)
            return f"\n{'#' * level} {text}\n"

        if tag == "p":
            text = "".join(_process_node(c) for c in node.children).strip()
            return f"\n{text}\n" if text else ""

        if tag in ("ul", "ol"):
            items = []
            for i, li in enumerate(node.find_all("li", recursive=False)):
                text = "".join(_process_node(c) for c in li.children).strip()
                prefix = f"{i + 1}." if tag == "ol" else "-"
                items.append(f"{prefix} {text}")
            return "\n" + "\n".join(items) + "\n"

        if tag == "blockquote":
            text = "".join(_process_node(c) for c in node.children).strip()
            quoted = "\n".join(f"> {line}" for line in text.splitlines())
            return f"\n{quoted}\n"

        if tag == "pre":
            code = node.get_text()
            return f"\n```\n{code}\n```\n"

        if tag == "code":
            return f"`{node.get_text()}`"

        if tag in ("strong", "b"):
            return f"**{node.get_text(strip=True)}**"

        if tag in ("em", "i"):
            return f"*{node.get_text(strip=True)}*"

        if tag == "a":
            href = node.get("href", "")
            text = node.get_text(strip=True)
            if href and text:
                full_href = urljoin(base_url, href)
                return f"[{text}]({full_href})"
            return text

        if tag == "img":
            alt = node.get("alt", "").strip()
            src = _best_src(node, base_url)
            if not src:
                return ""
            # Skip tiny tracking pixels
            width = node.get("width", "")
            if width and str(width).isdigit() and int(width) < 10:
                return ""
            images.append((src, alt))
            img_index = len(images)
            fname = f"image_{img_index:03d}{Path(urlparse(src).path).suffix or '.jpg'}"
            caption = f" *{alt}*" if alt else ""
            return f"\n![{alt}]({fname}){caption}\n"

        if tag == "picture":
            # Prefer <source srcset> over <img src>
            alt = ""
            img_tag = node.find("img")
            if img_tag:
                alt = img_tag.get("alt", "").strip()
            src = None
            for source in node.find_all("source"):
                src = _best_srcset(source.get("srcset", ""), base_url)
                if src:
                    break
            if not src and img_tag:
                src = _best_src(img_tag, base_url)
            if not src:
                return ""
            images.append((src, alt))
            img_index = len(images)
            fname = f"image_{img_index:03d}{Path(urlparse(src).path).suffix or '.jpg'}"
            caption = f" *{alt}*" if alt else ""
            return f"\n![{alt}]({fname}){caption}\n"

        if tag == "figure":
            parts = []
            # Let picture/img tags be processed recursively (don't double-process)
            picture = node.find("picture")
            if picture:
                parts.append(_process_node(picture))
            elif node.find("img"):
                parts.append(_process_node(node.find("img")))
            figcaption = node.find("figcaption")
            if figcaption:
                caption = figcaption.get_text(strip=True)
                if caption:
                    parts.append(f"*{caption}*\n")
            return "\n".join(parts)

        if tag == "hr":
            return "\n---\n"

        if tag == "br":
            return "\n"

        return "".join(_process_node(c) for c in node.children)

    body_md = _process_node(article)

    # Collapse excessive blank lines
    body_md = re.sub(r"\n{3,}", "\n\n", body_md).strip()

    return title, author, body_md, images


def fetch_article(url: str) -> ArticleData:
    """Fetch a Medium article and return structured ArticleData."""
    source_url = url

    html = _fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    article_tag = soup.find("article")
    content_ok = article_tag and len(article_tag.get_text(strip=True)) >= 200

    if content_ok:
        logger.info("Fetched article directly from %s", url)
    elif MEDIUM_SID:
        # Logged-in fetch should always work — surface the error rather than silently falling back
        raise ValueError(
            "Article body was empty even with your Medium session cookie. "
            "The cookie may have expired — please update MEDIUM_SID in .env."
        )
    else:
        logger.info("Direct fetch thin, trying freedium…")
        source_url = _freedium_url(url)
        html = _fetch_html(source_url)
        logger.info("Fetched via freedium: %s", source_url)

    title, author, body_md, image_refs = _extract_article(html, source_url)

    # Build header
    header = f"# {title}\n"
    if author:
        header += f"\n**Author:** {author}\n"
    header += f"\n**Source:** {url}\n\n---\n\n"

    content_md = header + body_md

    # Download images
    images: dict[str, bytes] = {}
    for i, (img_url, alt) in enumerate(image_refs, start=1):
        suffix = Path(urlparse(img_url).path).suffix or ".jpg"
        fname = f"image_{i:03d}{suffix}"
        data = _download_image(img_url)
        if data:
            images[fname] = data

    return ArticleData(
        title=title,
        author=author,
        url=url,
        content_md=content_md,
        images=images,
    )


def build_zip(article: ArticleData) -> bytes:
    """Package the markdown + images into an in-memory zip archive."""
    buf = io.BytesIO()
    safe_title = re.sub(r"[^\w\s\-]", "", article.title)[:60].strip().replace(" ", "_") or "article"

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{safe_title}.md", article.content_md.encode("utf-8"))
        for fname, data in article.images.items():
            zf.writestr(fname, data)

    buf.seek(0)
    return buf.read()
