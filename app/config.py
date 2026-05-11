from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv


DEFAULT_WATCHFACTS_URL = "https://watchfacts.com/simon-match-making"


class ConfigError(ValueError):
    """Raised when runtime configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    watchfacts_url: str
    headless: bool
    enable_crawl4ai: bool
    project_root: Path
    data_dir: Path
    logs_dir: Path
    db_path: Path
    browser_state_path: Path


def parse_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "f", "no", "n", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean value such as true, false, 1, or 0")


def load_settings(
    env: Mapping[str, str] | None = None,
    *,
    env_file: str | Path | None = ".env",
    project_root: Path | None = None,
) -> Settings:
    if env is None and env_file is not None:
        load_dotenv(env_file)

    source = env if env is not None else os.environ
    root = (project_root or Path(__file__).resolve().parents[1]).resolve()

    token = source.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ConfigError("TELEGRAM_BOT_TOKEN is required")

    watchfacts_url = source.get("WATCHFACTS_URL", DEFAULT_WATCHFACTS_URL).strip()
    if not watchfacts_url:
        raise ConfigError("WATCHFACTS_URL must not be empty")

    headless = parse_bool(source.get("HEADLESS", "true"), name="HEADLESS")
    enable_crawl4ai = parse_bool(
        source.get("ENABLE_CRAWL4AI", "true"),
        name="ENABLE_CRAWL4AI",
    )

    data_dir = root / "data"
    logs_dir = root / "logs"

    return Settings(
        telegram_bot_token=token,
        watchfacts_url=watchfacts_url,
        headless=headless,
        enable_crawl4ai=enable_crawl4ai,
        project_root=root,
        data_dir=data_dir,
        logs_dir=logs_dir,
        db_path=data_dir / "bot.db",
        browser_state_path=data_dir / "watchfacts_state.json",
    )
