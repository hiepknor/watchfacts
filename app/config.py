from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping, cast

from dotenv import load_dotenv


DEFAULT_WATCHFACTS_URL = "https://watchfacts.com/simon-match-making"
DEFAULT_TELEGRAM_RESULT_LIMIT = 5
DEFAULT_TELEGRAM_MAX_CONCURRENT_SEARCHES = 1
DEFAULT_OPENAI_MODEL = "gpt-5-mini"
DEFAULT_OPENAI_TIMEOUT_SECONDS = 12
DEFAULT_OPENAI_MAX_REFINES = 3
DEFAULT_SEARCH_CACHE_TTL_SECONDS = 5 * 60
DEFAULT_SEARCH_MAX_CONCURRENT_SEARCHES = 1
DEFAULT_WATCHFACTS_HTTP_CLIENT_ENABLED = True
DEFAULT_WATCHFACTS_FORM_CACHE_TTL_SECONDS = 15 * 60
DEFAULT_WATCHFACTS_HTTP_CONNECT_TIMEOUT_SECONDS = 10
DEFAULT_WATCHFACTS_HTTP_POOL_TIMEOUT_SECONDS = 10
DEFAULT_WATCHFACTS_HTTP_KEEPALIVE_EXPIRY_SECONDS = 60
DEFAULT_WATCHFACTS_HTTP_READ_TIMEOUT_SECONDS = 30
DEFAULT_WATCHFACTS_HTTP_SEARCH_READ_TIMEOUT_SECONDS = 120
DEFAULT_WATCHFACTS_HTTP_FAILURE_COOLDOWN_SECONDS = 60
DEFAULT_WATCHFACTS_HTTP_WARMUP_ON_HEALTH = True
DEFAULT_OPENWA_CHAT_DRAFT_ENDPOINT = "/api/chats/drafts"
DEFAULT_RESULT_PAGE_TTL_SECONDS = 24 * 60 * 60
DEFAULT_RESULT_PAGE_MAX_RESULTS = 200
DEFAULT_RESULT_PAGE_STORAGE_DIR = "data/result_pages"
DEFAULT_RESULT_PAGE_RATE_LIMIT_ENABLED = True
DEFAULT_RESULT_PAGE_RATE_LIMIT_MAX_REQUESTS = 60
DEFAULT_RESULT_PAGE_RATE_LIMIT_WINDOW_SECONDS = 60
DEFAULT_RESULT_PAGE_RATE_LIMIT_BLOCK_SECONDS = 120
HybridAIMode = Literal["off", "shadow", "review", "guarded"]
DEFAULT_HYBRID_AI_MODE: HybridAIMode = "off"
RuntimeMode = Literal["telegram", "search"]
DEFAULT_RUNTIME_MODE: RuntimeMode = "telegram"


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
    telegram_max_concurrent_searches: int = DEFAULT_TELEGRAM_MAX_CONCURRENT_SEARCHES
    hybrid_ai_mode: HybridAIMode = DEFAULT_HYBRID_AI_MODE
    openai_api_key: str = ""
    openai_model: str = DEFAULT_OPENAI_MODEL
    openai_timeout_seconds: int = DEFAULT_OPENAI_TIMEOUT_SECONDS
    openai_max_refines: int = DEFAULT_OPENAI_MAX_REFINES
    search_cache_ttl_seconds: int = DEFAULT_SEARCH_CACHE_TTL_SECONDS
    search_max_concurrent_searches: int = DEFAULT_SEARCH_MAX_CONCURRENT_SEARCHES
    openwa_base_url: str = ""
    openwa_api_key: str = ""
    openwa_dashboard_url: str = ""
    openwa_chat_draft_endpoint: str = DEFAULT_OPENWA_CHAT_DRAFT_ENDPOINT
    enable_openwa_chat_handoff: bool = False
    runtime_mode: RuntimeMode = DEFAULT_RUNTIME_MODE
    watchfacts_http_client_enabled: bool = DEFAULT_WATCHFACTS_HTTP_CLIENT_ENABLED
    watchfacts_form_cache_ttl_seconds: int = DEFAULT_WATCHFACTS_FORM_CACHE_TTL_SECONDS
    watchfacts_http_connect_timeout_seconds: int = (
        DEFAULT_WATCHFACTS_HTTP_CONNECT_TIMEOUT_SECONDS
    )
    watchfacts_http_pool_timeout_seconds: int = DEFAULT_WATCHFACTS_HTTP_POOL_TIMEOUT_SECONDS
    watchfacts_http_keepalive_expiry_seconds: int = (
        DEFAULT_WATCHFACTS_HTTP_KEEPALIVE_EXPIRY_SECONDS
    )
    watchfacts_http_read_timeout_seconds: int = (
        DEFAULT_WATCHFACTS_HTTP_READ_TIMEOUT_SECONDS
    )
    watchfacts_http_search_read_timeout_seconds: int = (
        DEFAULT_WATCHFACTS_HTTP_SEARCH_READ_TIMEOUT_SECONDS
    )
    watchfacts_http_failure_cooldown_seconds: int = (
        DEFAULT_WATCHFACTS_HTTP_FAILURE_COOLDOWN_SECONDS
    )
    watchfacts_http_warmup_on_health: bool = DEFAULT_WATCHFACTS_HTTP_WARMUP_ON_HEALTH
    result_page_public_base_url: str = ""
    result_page_ttl_seconds: int = DEFAULT_RESULT_PAGE_TTL_SECONDS
    result_page_max_results: int = DEFAULT_RESULT_PAGE_MAX_RESULTS
    result_page_storage_dir: Path = field(
        default_factory=lambda: Path(DEFAULT_RESULT_PAGE_STORAGE_DIR)
    )
    result_page_rate_limit_enabled: bool = DEFAULT_RESULT_PAGE_RATE_LIMIT_ENABLED
    result_page_rate_limit_max_requests: int = (
        DEFAULT_RESULT_PAGE_RATE_LIMIT_MAX_REQUESTS
    )
    result_page_rate_limit_window_seconds: int = (
        DEFAULT_RESULT_PAGE_RATE_LIMIT_WINDOW_SECONDS
    )
    result_page_rate_limit_block_seconds: int = (
        DEFAULT_RESULT_PAGE_RATE_LIMIT_BLOCK_SECONDS
    )


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


def parse_hybrid_ai_mode(value: str, *, name: str) -> HybridAIMode:
    normalized = value.strip().lower()
    if normalized in {"off", "shadow", "review", "guarded"}:
        return cast(HybridAIMode, normalized)
    raise ConfigError(f"{name} must be one of: off, shadow, review, guarded")


def load_settings(
    env: Mapping[str, str] | None = None,
    *,
    env_file: str | Path | None = ".env",
    project_root: Path | None = None,
    runtime_mode: RuntimeMode = DEFAULT_RUNTIME_MODE,
) -> Settings:
    if env is None and env_file is not None:
        load_dotenv(env_file)

    source = env if env is not None else os.environ
    root = (project_root or Path(__file__).resolve().parents[1]).resolve()

    if runtime_mode not in {"telegram", "search"}:
        raise ConfigError("runtime_mode must be one of: telegram, search")

    token = source.get("TELEGRAM_BOT_TOKEN", "").strip()
    if runtime_mode == "telegram" and not token:
        raise ConfigError("TELEGRAM_BOT_TOKEN is required")

    telegram_allowed_user_ids = parse_user_ids(
        source.get("TELEGRAM_ALLOWED_USER_IDS", ""),
        name="TELEGRAM_ALLOWED_USER_IDS",
    )
    telegram_result_limit = parse_positive_int(
        source.get("TELEGRAM_RESULT_LIMIT", str(DEFAULT_TELEGRAM_RESULT_LIMIT)),
        name="TELEGRAM_RESULT_LIMIT",
    )
    telegram_max_concurrent_searches = parse_positive_int(
        source.get(
            "TELEGRAM_MAX_CONCURRENT_SEARCHES",
            str(DEFAULT_TELEGRAM_MAX_CONCURRENT_SEARCHES),
        ),
        name="TELEGRAM_MAX_CONCURRENT_SEARCHES",
    )

    watchfacts_url = source.get("WATCHFACTS_URL", DEFAULT_WATCHFACTS_URL).strip()
    if not watchfacts_url:
        raise ConfigError("WATCHFACTS_URL must not be empty")

    headless = parse_bool(source.get("HEADLESS", "true"), name="HEADLESS")
    enable_crawl4ai = parse_bool(
        source.get("ENABLE_CRAWL4AI", "true"),
        name="ENABLE_CRAWL4AI",
    )
    hybrid_ai_mode = parse_hybrid_ai_mode(
        source.get("HYBRID_AI_MODE", DEFAULT_HYBRID_AI_MODE),
        name="HYBRID_AI_MODE",
    )
    openai_api_key = source.get("OPENAI_API_KEY", "").strip()
    if hybrid_ai_mode != "off" and not openai_api_key:
        raise ConfigError("OPENAI_API_KEY is required when HYBRID_AI_MODE is not off")
    openai_model = source.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip()
    if not openai_model:
        raise ConfigError("OPENAI_MODEL must not be empty")
    openai_timeout_seconds = parse_positive_int(
        source.get("OPENAI_TIMEOUT_SECONDS", str(DEFAULT_OPENAI_TIMEOUT_SECONDS)),
        name="OPENAI_TIMEOUT_SECONDS",
    )
    openai_max_refines = parse_positive_int(
        source.get("OPENAI_MAX_REFINES", str(DEFAULT_OPENAI_MAX_REFINES)),
        name="OPENAI_MAX_REFINES",
    )
    search_cache_ttl_seconds = parse_positive_int(
        source.get("SEARCH_CACHE_TTL_SECONDS", str(DEFAULT_SEARCH_CACHE_TTL_SECONDS)),
        name="SEARCH_CACHE_TTL_SECONDS",
    )
    search_max_concurrent_searches = parse_positive_int(
        source.get(
            "SEARCH_MAX_CONCURRENT_SEARCHES",
            str(DEFAULT_SEARCH_MAX_CONCURRENT_SEARCHES),
        ),
        name="SEARCH_MAX_CONCURRENT_SEARCHES",
    )
    watchfacts_http_client_enabled = parse_bool(
        source.get(
            "WATCHFACTS_HTTP_CLIENT_ENABLED",
            str(DEFAULT_WATCHFACTS_HTTP_CLIENT_ENABLED).lower(),
        ),
        name="WATCHFACTS_HTTP_CLIENT_ENABLED",
    )
    watchfacts_form_cache_ttl_seconds = parse_positive_int(
        source.get(
            "WATCHFACTS_FORM_CACHE_TTL_SECONDS",
            str(DEFAULT_WATCHFACTS_FORM_CACHE_TTL_SECONDS),
        ),
        name="WATCHFACTS_FORM_CACHE_TTL_SECONDS",
    )
    watchfacts_http_connect_timeout_seconds = parse_positive_int(
        source.get(
            "WATCHFACTS_HTTP_CONNECT_TIMEOUT_SECONDS",
            str(DEFAULT_WATCHFACTS_HTTP_CONNECT_TIMEOUT_SECONDS),
        ),
        name="WATCHFACTS_HTTP_CONNECT_TIMEOUT_SECONDS",
    )
    watchfacts_http_pool_timeout_seconds = parse_positive_int(
        source.get(
            "WATCHFACTS_HTTP_POOL_TIMEOUT_SECONDS",
            str(DEFAULT_WATCHFACTS_HTTP_POOL_TIMEOUT_SECONDS),
        ),
        name="WATCHFACTS_HTTP_POOL_TIMEOUT_SECONDS",
    )
    watchfacts_http_keepalive_expiry_seconds = parse_positive_int(
        source.get(
            "WATCHFACTS_HTTP_KEEPALIVE_EXPIRY_SECONDS",
            str(DEFAULT_WATCHFACTS_HTTP_KEEPALIVE_EXPIRY_SECONDS),
        ),
        name="WATCHFACTS_HTTP_KEEPALIVE_EXPIRY_SECONDS",
    )
    watchfacts_http_read_timeout_seconds = parse_positive_int(
        source.get(
            "WATCHFACTS_HTTP_READ_TIMEOUT_SECONDS",
            str(DEFAULT_WATCHFACTS_HTTP_READ_TIMEOUT_SECONDS),
        ),
        name="WATCHFACTS_HTTP_READ_TIMEOUT_SECONDS",
    )
    watchfacts_http_search_read_timeout_seconds = parse_positive_int(
        source.get(
            "WATCHFACTS_HTTP_SEARCH_READ_TIMEOUT_SECONDS",
            str(DEFAULT_WATCHFACTS_HTTP_SEARCH_READ_TIMEOUT_SECONDS),
        ),
        name="WATCHFACTS_HTTP_SEARCH_READ_TIMEOUT_SECONDS",
    )
    watchfacts_http_failure_cooldown_seconds = parse_positive_int(
        source.get(
            "WATCHFACTS_HTTP_FAILURE_COOLDOWN_SECONDS",
            str(DEFAULT_WATCHFACTS_HTTP_FAILURE_COOLDOWN_SECONDS),
        ),
        name="WATCHFACTS_HTTP_FAILURE_COOLDOWN_SECONDS",
    )
    watchfacts_http_warmup_on_health = parse_bool(
        source.get(
            "WATCHFACTS_HTTP_WARMUP_ON_HEALTH",
            str(DEFAULT_WATCHFACTS_HTTP_WARMUP_ON_HEALTH).lower(),
        ),
        name="WATCHFACTS_HTTP_WARMUP_ON_HEALTH",
    )
    enable_openwa_chat_handoff = parse_bool(
        source.get("ENABLE_OPENWA_CHAT_HANDOFF", "false"),
        name="ENABLE_OPENWA_CHAT_HANDOFF",
    )
    openwa_base_url = source.get("OPENWA_BASE_URL", "").strip().rstrip("/")
    openwa_api_key = source.get("OPENWA_API_KEY", "").strip()
    openwa_dashboard_url = source.get("OPENWA_DASHBOARD_URL", "").strip().rstrip("/")
    openwa_chat_draft_endpoint = source.get(
        "OPENWA_CHAT_DRAFT_ENDPOINT",
        DEFAULT_OPENWA_CHAT_DRAFT_ENDPOINT,
    ).strip()
    if not openwa_chat_draft_endpoint:
        raise ConfigError("OPENWA_CHAT_DRAFT_ENDPOINT must not be empty")
    if not openwa_chat_draft_endpoint.startswith("/"):
        openwa_chat_draft_endpoint = f"/{openwa_chat_draft_endpoint}"

    data_dir = root / "data"
    logs_dir = root / "logs"
    result_page_public_base_url = source.get(
        "RESULT_PAGE_PUBLIC_BASE_URL",
        "",
    ).strip().rstrip("/")
    result_page_ttl_seconds = parse_positive_int(
        source.get(
            "RESULT_PAGE_TTL_SECONDS",
            str(DEFAULT_RESULT_PAGE_TTL_SECONDS),
        ),
        name="RESULT_PAGE_TTL_SECONDS",
    )
    result_page_max_results = parse_positive_int(
        source.get(
            "RESULT_PAGE_MAX_RESULTS",
            str(DEFAULT_RESULT_PAGE_MAX_RESULTS),
        ),
        name="RESULT_PAGE_MAX_RESULTS",
    )
    result_page_storage_value = source.get(
        "RESULT_PAGE_STORAGE_DIR",
        DEFAULT_RESULT_PAGE_STORAGE_DIR,
    ).strip()
    if not result_page_storage_value:
        raise ConfigError("RESULT_PAGE_STORAGE_DIR must not be empty")
    result_page_storage_dir = Path(result_page_storage_value)
    if not result_page_storage_dir.is_absolute():
        result_page_storage_dir = root / result_page_storage_dir

    result_page_rate_limit_enabled = parse_bool(
        source.get(
            "RESULT_PAGE_RATE_LIMIT_ENABLED",
            str(DEFAULT_RESULT_PAGE_RATE_LIMIT_ENABLED).lower(),
        ),
        name="RESULT_PAGE_RATE_LIMIT_ENABLED",
    )
    result_page_rate_limit_max_requests = parse_positive_int(
        source.get(
            "RESULT_PAGE_RATE_LIMIT_MAX_REQUESTS",
            str(DEFAULT_RESULT_PAGE_RATE_LIMIT_MAX_REQUESTS),
        ),
        name="RESULT_PAGE_RATE_LIMIT_MAX_REQUESTS",
    )
    result_page_rate_limit_window_seconds = parse_positive_int(
        source.get(
            "RESULT_PAGE_RATE_LIMIT_WINDOW_SECONDS",
            str(DEFAULT_RESULT_PAGE_RATE_LIMIT_WINDOW_SECONDS),
        ),
        name="RESULT_PAGE_RATE_LIMIT_WINDOW_SECONDS",
    )
    result_page_rate_limit_block_seconds = parse_positive_int(
        source.get(
            "RESULT_PAGE_RATE_LIMIT_BLOCK_SECONDS",
            str(DEFAULT_RESULT_PAGE_RATE_LIMIT_BLOCK_SECONDS),
        ),
        name="RESULT_PAGE_RATE_LIMIT_BLOCK_SECONDS",
    )

    return Settings(
        telegram_bot_token=token,
        telegram_allowed_user_ids=telegram_allowed_user_ids,
        telegram_result_limit=telegram_result_limit,
        telegram_max_concurrent_searches=telegram_max_concurrent_searches,
        watchfacts_url=watchfacts_url,
        headless=headless,
        enable_crawl4ai=enable_crawl4ai,
        hybrid_ai_mode=hybrid_ai_mode,
        openai_api_key=openai_api_key,
        openai_model=openai_model,
        openai_timeout_seconds=openai_timeout_seconds,
        openai_max_refines=openai_max_refines,
        search_cache_ttl_seconds=search_cache_ttl_seconds,
        search_max_concurrent_searches=search_max_concurrent_searches,
        watchfacts_http_client_enabled=watchfacts_http_client_enabled,
        watchfacts_form_cache_ttl_seconds=watchfacts_form_cache_ttl_seconds,
        watchfacts_http_connect_timeout_seconds=watchfacts_http_connect_timeout_seconds,
        watchfacts_http_pool_timeout_seconds=watchfacts_http_pool_timeout_seconds,
        watchfacts_http_keepalive_expiry_seconds=watchfacts_http_keepalive_expiry_seconds,
        watchfacts_http_read_timeout_seconds=watchfacts_http_read_timeout_seconds,
        watchfacts_http_search_read_timeout_seconds=(
            watchfacts_http_search_read_timeout_seconds
        ),
        watchfacts_http_failure_cooldown_seconds=watchfacts_http_failure_cooldown_seconds,
        watchfacts_http_warmup_on_health=watchfacts_http_warmup_on_health,
        openwa_base_url=openwa_base_url,
        openwa_api_key=openwa_api_key,
        openwa_dashboard_url=openwa_dashboard_url,
        openwa_chat_draft_endpoint=openwa_chat_draft_endpoint,
        enable_openwa_chat_handoff=enable_openwa_chat_handoff,
        result_page_public_base_url=result_page_public_base_url,
        result_page_ttl_seconds=result_page_ttl_seconds,
        result_page_max_results=result_page_max_results,
        result_page_storage_dir=result_page_storage_dir,
        result_page_rate_limit_enabled=result_page_rate_limit_enabled,
        result_page_rate_limit_max_requests=result_page_rate_limit_max_requests,
        result_page_rate_limit_window_seconds=result_page_rate_limit_window_seconds,
        result_page_rate_limit_block_seconds=result_page_rate_limit_block_seconds,
        project_root=root,
        data_dir=data_dir,
        logs_dir=logs_dir,
        db_path=data_dir / "bot.db",
        browser_state_path=data_dir / "watchfacts_state.json",
        runtime_mode=runtime_mode,
    )


def load_search_settings(
    env: Mapping[str, str] | None = None,
    *,
    env_file: str | Path | None = ".env",
    project_root: Path | None = None,
) -> Settings:
    return load_settings(
        env=env,
        env_file=env_file,
        project_root=project_root,
        runtime_mode="search",
    )
