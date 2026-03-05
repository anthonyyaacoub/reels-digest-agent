"""Telegram bot — receive Instagram reel URLs and add them to the queue."""

import logging
import re

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from . import db
from .config import TELEGRAM_ALLOWED_USER_ID, TELEGRAM_BOT_TOKEN

logger = logging.getLogger(__name__)

_INSTAGRAM_RE = re.compile(
    r"https?://(www\.)?instagram\.com/(reel|p|tv)/[A-Za-z0-9_\-]+/?",
    re.IGNORECASE,
)


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

    # Extract Instagram URL from message
    match = _INSTAGRAM_RE.search(text)
    if not match:
        await update.message.reply_text(
            "Please send an Instagram Reel URL.\n"
            "Example: https://www.instagram.com/reel/ABC123/"
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
