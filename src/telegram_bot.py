"""Telegram bot — receive Instagram reel URLs and Medium article URLs."""

import io
import logging
import re

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from . import db
from .article_fetcher import build_zip, fetch_article, is_medium_url
from .config import TELEGRAM_ALLOWED_USER_ID, TELEGRAM_BOT_TOKEN

logger = logging.getLogger(__name__)

_INSTAGRAM_RE = re.compile(
    r"https?://(www\.)?instagram\.com/(reel|p|tv)/[A-Za-z0-9_\-]+/?",
    re.IGNORECASE,
)

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    await update.message.reply_text(
        f"👋 Reels Digest Bot\n\n"
        f"Your Telegram user ID is: `{user_id}`\n\n"
        f"Send me any Instagram Reel URL and I'll queue it for the daily digest.",
        parse_mode="Markdown",
    )


async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    # If TELEGRAM_ALLOWED_USER_ID is set, reject all other users
    if TELEGRAM_ALLOWED_USER_ID and user_id != TELEGRAM_ALLOWED_USER_ID:
        await update.message.reply_text(
            f"⛔ Unauthorized.\n\nYour user ID is `{user_id}` — add it to TELEGRAM_ALLOWED_USER_ID in your .env to allow access.",
            parse_mode="Markdown",
        )
        logger.warning("Rejected message from unauthorized user %d", user_id)
        return

    text = update.message.text or ""

    # Check for any URL in the message
    url_match = _URL_RE.search(text)
    if not url_match:
        await update.message.reply_text(
            "Send me an Instagram Reel URL or a Medium article URL.\n\n"
            "Examples:\n"
            "• https://www.instagram.com/reel/ABC123/\n"
            "• https://medium.com/@author/article-title-abc123"
        )
        return

    url = url_match.group(0).rstrip(".,;)")

    # Medium article
    if is_medium_url(url):
        await _handle_medium_article(update, url)
        return

    # Instagram reel
    match = _INSTAGRAM_RE.search(text)
    if not match:
        await update.message.reply_text(
            "That URL doesn't look like an Instagram Reel or a Medium article.\n\n"
            "Supported:\n"
            "• https://www.instagram.com/reel/ABC123/\n"
            "• https://medium.com/@author/article-title"
        )
        return

    url = match.group(0).rstrip("/") + "/"
    db.init_db()
    reel_id = db.add_reel(url)
    logger.info("Telegram: queued reel %s from user %d", reel_id, user_id)

    counts = db.get_reel_counts()
    pending = counts.get("pending", 0)

    await update.message.reply_text(
        f"✅ Queued!\n\n`{reel_id}`\n\n{pending} reel(s) pending in today's digest.",
        parse_mode="Markdown",
    )


async def _handle_medium_article(update: Update, url: str) -> None:
    """Fetch a Medium article and send it back as a zip (markdown + images)."""
    msg = await update.message.reply_text("Fetching article, please wait...")
    try:
        article = fetch_article(url)
        zip_bytes = build_zip(article)

        import re as _re
        safe_title = _re.sub(r"[^\w\s\-]", "", article.title)[:60].strip().replace(" ", "_") or "article"
        fname = f"{safe_title}.zip"

        caption = f"*{article.title}*"
        if article.author:
            caption += f"\nby {article.author}"
        caption += f"\n\n{len(article.images)} image(s) included"

        await msg.delete()
        await update.message.reply_document(
            document=io.BytesIO(zip_bytes),
            filename=fname,
            caption=caption,
            parse_mode="Markdown",
        )
        logger.info("Telegram: sent article '%s' to user %d", article.title, update.effective_user.id)
    except Exception as exc:
        logger.error("Failed to fetch article %s: %s", url, exc, exc_info=True)
        await msg.edit_text(f"Failed to fetch article: {exc}")


async def _status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db.init_db()
    counts = db.get_reel_counts()
    await update.message.reply_text(
        f"📊 Queue status:\n"
        f"• Pending: {counts.get('pending', 0)}\n"
        f"• Done: {counts.get('done', 0)}\n"
        f"• Errors: {counts.get('error', 0)}",
    )


def run_bot() -> None:
    """Start the Telegram bot with long polling (blocking)."""
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")

    db.init_db()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", _start))
    app.add_handler(CommandHandler("status", _status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))

    logger.info("Telegram bot started — polling for messages…")
    app.run_polling(drop_pending_updates=True)
