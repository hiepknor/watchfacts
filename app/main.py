from __future__ import annotations

import logging
import sys

from app.config import ConfigError, load_settings
from app.telegram_bot import run_bot


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def main() -> int:
    configure_logging()

    try:
        settings = load_settings()
    except ConfigError as exc:
        logging.getLogger(__name__).error("Configuration error: %s", exc)
        return 2

    run_bot(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
