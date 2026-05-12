from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv


DEFAULT_WATCHFACTS_URL = "https://watchfacts.com/simon-match-making"
DEFAULT_TELEGRAM_RESULT_LIMIT = 5
DEFAULT_LOCAL_LLM_BASE_URL = "http://localhost:8080"
DEFAULT_LOCAL_LLM_MODEL = "gemma-4-e2b-Q4_K_M.gguf"
DEFAULT_LOCAL_LLM_TIMEOUT_SECONDS = 30
DEFAULT_LOCAL_LLM_MAX_REFINES = 3


class ConfigError(ValueError):
    """Raised when runtime configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_allowed_user_ids: tuple[int, ...]
    telegram_result_limit: int
    watchfacts_url: str
    headless: bool
    enable_crawl4ai: bool
    project_root: Path
    data_dir: Path
    logs_dir: Path
    db_path: Path
    browser_state_path: Path
    local_llm_enabled: bool = False
    local_llm_base_url: str = DEFAULT_LOCAL_LLM_BASE_URL
    local_llm_model: str = DEFAULT_LOCAL_LLM_MODEL
    local_llm_timeout_seconds: int = DEFAULT_LOCAL_LLM_TIMEOUT_SECONDS
    local_llm_max_refines: int = DEFAULT_LOCAL_LLM_MAX_REFINES


def parse_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "f", "no", "n", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean value such as true, false, 1, or 0")


def parse_user_ids(value: str, *, name: str) -> tuple[int, ...]:
    normalized = value.strip()
    if not normalized:
        return ()

    user_ids: list[int] = []
    for raw_part in normalized.split(","):
        part = raw_part.strip()
        if not part:
            continue
        try:
            user_id = int(part)
        except ValueError as exc:
            raise ConfigError(f"{name} must contain only numeric Telegram user IDs") from exc
        if user_id <= 0:
            raise ConfigError(f"{name} must contain only positive Telegram user IDs")
        user_ids.append(user_id)

    return tuple(dict.fromkeys(user_ids))


def parse_positive_int(value: str, *, name: str) -> int:
    try:
        parsed = int(value.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise ConfigError(f"{name} must be a positive integer")
    return parsed


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

    telegram_allowed_user_ids = parse_user_ids(
        source.get("TELEGRAM_ALLOWED_USER_IDS", ""),
        name="TELEGRAM_ALLOWED_USER_IDS",
    )
    telegram_result_limit = parse_positive_int(
        source.get("TELEGRAM_RESULT_LIMIT", str(DEFAULT_TELEGRAM_RESULT_LIMIT)),
        name="TELEGRAM_RESULT_LIMIT",
    )

    watchfacts_url = source.get("WATCHFACTS_URL", DEFAULT_WATCHFACTS_URL).strip()
    if not watchfacts_url:
        raise ConfigError("WATCHFACTS_URL must not be empty")

    headless = parse_bool(source.get("HEADLESS", "true"), name="HEADLESS")
    enable_crawl4ai = parse_bool(
        source.get("ENABLE_CRAWL4AI", "true"),
        name="ENABLE_CRAWL4AI",
    )
    local_llm_enabled = parse_bool(
        source.get("LOCAL_LLM_ENABLED", "false"),
        name="LOCAL_LLM_ENABLED",
    )
    local_llm_base_url = source.get("LOCAL_LLM_BASE_URL", DEFAULT_LOCAL_LLM_BASE_URL).strip()
    if not local_llm_base_url:
        raise ConfigError("LOCAL_LLM_BASE_URL must not be empty")
    local_llm_model = source.get("LOCAL_LLM_MODEL", DEFAULT_LOCAL_LLM_MODEL).strip()
    if not local_llm_model:
        raise ConfigError("LOCAL_LLM_MODEL must not be empty")
    local_llm_timeout_seconds = parse_positive_int(
        source.get("LOCAL_LLM_TIMEOUT_SECONDS", str(DEFAULT_LOCAL_LLM_TIMEOUT_SECONDS)),
        name="LOCAL_LLM_TIMEOUT_SECONDS",
    )
    local_llm_max_refines = parse_positive_int(
        source.get("LOCAL_LLM_MAX_REFINES", str(DEFAULT_LOCAL_LLM_MAX_REFINES)),
        name="LOCAL_LLM_MAX_REFINES",
    )

    data_dir = root / "data"
    logs_dir = root / "logs"

    return Settings(
        telegram_bot_token=token,
        telegram_allowed_user_ids=telegram_allowed_user_ids,
        telegram_result_limit=telegram_result_limit,
        watchfacts_url=watchfacts_url,
        headless=headless,
        enable_crawl4ai=enable_crawl4ai,
        local_llm_enabled=local_llm_enabled,
        local_llm_base_url=local_llm_base_url,
        local_llm_model=local_llm_model,
        local_llm_timeout_seconds=local_llm_timeout_seconds,
        local_llm_max_refines=local_llm_max_refines,
        project_root=root,
        data_dir=data_dir,
        logs_dir=logs_dir,
        db_path=data_dir / "bot.db",
        browser_state_path=data_dir / "watchfacts_state.json",
    )
