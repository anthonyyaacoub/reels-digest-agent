"""Entry point: start the Telegram bot."""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

if __name__ == "__main__":
    from src.telegram_bot import run_bot
    run_bot()
