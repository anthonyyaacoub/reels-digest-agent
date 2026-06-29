"""Send a morning or evening recap to Telegram.

Usage:
    python run_recap.py morning
    python run_recap.py evening
"""

import logging
import sys

from src.recap import run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"morning", "evening"}:
        print("Usage: python run_recap.py {morning|evening}", file=sys.stderr)
        sys.exit(1)
    run(sys.argv[1])


if __name__ == "__main__":
    main()
