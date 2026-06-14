from pathlib import Path

import pytest

from app.config import (
    ConfigError,
    DEFAULT_HYBRID_AI_MODE,
    DEFAULT_OPENAI_MAX_REFINES,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_OPENAI_TIMEOUT_SECONDS,
    DEFAULT_OPENWA_CHAT_DRAFT_ENDPOINT,
    DEFAULT_RESULT_PAGE_MAX_RESULTS,
    DEFAULT_RESULT_PAGE_TTL_SECONDS,
    DEFAULT_RUNTIME_MODE,
    DEFAULT_SEARCH_CACHE_TTL_SECONDS,
    DEFAULT_SEARCH_MAX_CONCURRENT_SEARCHES,
    DEFAULT_SEARCH_RETRIEVAL_CONCURRENCY,
    DEFAULT_TELEGRAM_MAX_CONCURRENT_SEARCHES,
    DEFAULT_TELEGRAM_RESULT_LIMIT,
    DEFAULT_WATCHFACTS_URL,
    DEFAULT_WATCHFACTS_FORM_CACHE_TTL_SECONDS,
    DEFAULT_WATCHFACTS_HTTP_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_WATCHFACTS_HTTP_CLIENT_ENABLED,
    DEFAULT_WATCHFACTS_HTTP_FAILURE_COOLDOWN_SECONDS,
    DEFAULT_WATCHFACTS_HTTP_KEEPALIVE_EXPIRY_SECONDS,
    DEFAULT_WATCHFACTS_HTTP_POOL_TIMEOUT_SECONDS,
    DEFAULT_WATCHFACTS_HTTP_READ_TIMEOUT_SECONDS,
    DEFAULT_WATCHFACTS_HTTP_SEARCH_READ_TIMEOUT_SECONDS,
    DEFAULT_WATCHFACTS_HTTP_WARMUP_ON_HEALTH,
    MAX_SEARCH_RETRIEVAL_CONCURRENCY,
    load_settings,
    load_search_settings,
    parse_bool,
    parse_hybrid_ai_mode,
    parse_positive_int,
    parse_user_ids,
)


def test_load_settings_requires_telegram_token() -> None:
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN is required"):
        load_settings(env={})


def test_load_search_settings_does_not_require_telegram_token(tmp_path: Path) -> None:
    settings = load_search_settings(env={}, project_root=tmp_path)

    assert settings.runtime_mode == "search"
    assert settings.telegram_bot_token == ""
    assert settings.watchfacts_url == DEFAULT_WATCHFACTS_URL


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("false", False),
        ("FALSE", False),
        ("0", False),
        ("no", False),
    ],
)
def test_parse_bool_supports_common_forms(value: str, expected: bool) -> None:
    assert parse_bool(value, name="TEST_BOOL") is expected


def test_parse_bool_rejects_unknown_values() -> None:
    with pytest.raises(ConfigError, match="TEST_BOOL must be a boolean value"):
        parse_bool("sometimes", name="TEST_BOOL")


@pytest.mark.parametrize("value", ["off", "shadow", "review", "guarded"])
def test_parse_hybrid_ai_mode_accepts_supported_modes(value: str) -> None:
    assert parse_hybrid_ai_mode(value, name="HYBRID_AI_MODE") == value


def test_parse_hybrid_ai_mode_rejects_unknown_modes() -> None:
    with pytest.raises(ConfigError, match="HYBRID_AI_MODE must be one of"):
        parse_hybrid_ai_mode("auto", name="HYBRID_AI_MODE")


def test_parse_user_ids_accepts_empty_and_comma_separated_values() -> None:
    assert parse_user_ids("", name="TEST_IDS") == ()
    assert parse_user_ids("123, 456,123", name="TEST_IDS") == (123, 456)


def test_parse_user_ids_rejects_non_numeric_values() -> None:
    with pytest.raises(ConfigError, match="TEST_IDS must contain only numeric"):
        parse_user_ids("123,abc", name="TEST_IDS")


def test_parse_positive_int_accepts_positive_values() -> None:
    assert parse_positive_int("7", name="TEST_LIMIT") == 7


def test_parse_positive_int_rejects_invalid_values() -> None:
    with pytest.raises(ConfigError, match="TEST_LIMIT must be a positive integer"):
        parse_positive_int("0", name="TEST_LIMIT")
    with pytest.raises(ConfigError, match="TEST_LIMIT must be a positive integer"):
        parse_positive_int("abc", name="TEST_LIMIT")


def test_load_settings_uses_defaults_and_runtime_paths(tmp_path: Path) -> None:
    settings = load_settings(
        env={"TELEGRAM_BOT_TOKEN": "token"},
        project_root=tmp_path,
    )

    assert settings.telegram_bot_token == "token"
    assert settings.runtime_mode == DEFAULT_RUNTIME_MODE
    assert settings.telegram_allowed_user_ids == ()
    assert settings.telegram_result_limit == DEFAULT_TELEGRAM_RESULT_LIMIT
    assert settings.telegram_max_concurrent_searches == DEFAULT_TELEGRAM_MAX_CONCURRENT_SEARCHES
    assert settings.watchfacts_url == DEFAULT_WATCHFACTS_URL
    assert settings.headless is True
    assert settings.enable_crawl4ai is True
    assert settings.hybrid_ai_mode == DEFAULT_HYBRID_AI_MODE
    assert settings.openai_api_key == ""
    assert settings.openai_model == DEFAULT_OPENAI_MODEL
    assert settings.openai_timeout_seconds == DEFAULT_OPENAI_TIMEOUT_SECONDS
    assert settings.openai_max_refines == DEFAULT_OPENAI_MAX_REFINES
    assert settings.search_cache_ttl_seconds == DEFAULT_SEARCH_CACHE_TTL_SECONDS
    assert settings.search_max_concurrent_searches == DEFAULT_SEARCH_MAX_CONCURRENT_SEARCHES
    assert settings.search_retrieval_concurrency == DEFAULT_SEARCH_RETRIEVAL_CONCURRENCY
    assert settings.watchfacts_http_client_enabled == DEFAULT_WATCHFACTS_HTTP_CLIENT_ENABLED
    assert settings.watchfacts_form_cache_ttl_seconds == DEFAULT_WATCHFACTS_FORM_CACHE_TTL_SECONDS
    assert (
        settings.watchfacts_http_connect_timeout_seconds
        == DEFAULT_WATCHFACTS_HTTP_CONNECT_TIMEOUT_SECONDS
    )
    assert (
        settings.watchfacts_http_pool_timeout_seconds
        == DEFAULT_WATCHFACTS_HTTP_POOL_TIMEOUT_SECONDS
    )
    assert (
        settings.watchfacts_http_keepalive_expiry_seconds
        == DEFAULT_WATCHFACTS_HTTP_KEEPALIVE_EXPIRY_SECONDS
    )
    assert (
        settings.watchfacts_http_read_timeout_seconds
        == DEFAULT_WATCHFACTS_HTTP_READ_TIMEOUT_SECONDS
    )
    assert (
        settings.watchfacts_http_search_read_timeout_seconds
        == DEFAULT_WATCHFACTS_HTTP_SEARCH_READ_TIMEOUT_SECONDS
    )
    assert (
        settings.watchfacts_http_failure_cooldown_seconds
        == DEFAULT_WATCHFACTS_HTTP_FAILURE_COOLDOWN_SECONDS
    )
    assert (
        settings.watchfacts_http_warmup_on_health
        is DEFAULT_WATCHFACTS_HTTP_WARMUP_ON_HEALTH
    )
    assert settings.enable_openwa_chat_handoff is False
    assert settings.openwa_base_url == ""
    assert settings.openwa_api_key == ""
    assert settings.openwa_dashboard_url == ""
    assert settings.openwa_chat_draft_endpoint == DEFAULT_OPENWA_CHAT_DRAFT_ENDPOINT
    assert settings.result_page_public_base_url == ""
    assert settings.result_page_ttl_seconds == DEFAULT_RESULT_PAGE_TTL_SECONDS
    assert settings.result_page_max_results == DEFAULT_RESULT_PAGE_MAX_RESULTS
    assert settings.result_page_storage_dir == tmp_path / "data" / "result_pages"
    assert settings.data_dir == tmp_path / "data"
    assert settings.logs_dir == tmp_path / "logs"
    assert settings.db_path == tmp_path / "data" / "bot.db"
    assert settings.browser_state_path == tmp_path / "data" / "watchfacts_state.json"


def test_load_settings_reads_allowed_telegram_user_ids(tmp_path: Path) -> None:
    settings = load_settings(
        env={
            "TELEGRAM_BOT_TOKEN": "token",
            "TELEGRAM_ALLOWED_USER_IDS": "111, 222",
        },
        project_root=tmp_path,
    )

    assert settings.telegram_allowed_user_ids == (111, 222)


def test_load_settings_reads_telegram_result_limit(tmp_path: Path) -> None:
    settings = load_settings(
        env={
            "TELEGRAM_BOT_TOKEN": "token",
            "TELEGRAM_RESULT_LIMIT": "12",
        },
        project_root=tmp_path,
    )

    assert settings.telegram_result_limit == 12


def test_load_settings_reads_telegram_max_concurrent_searches(tmp_path: Path) -> None:
    settings = load_settings(
        env={
            "TELEGRAM_BOT_TOKEN": "token",
            "TELEGRAM_MAX_CONCURRENT_SEARCHES": "2",
        },
        project_root=tmp_path,
    )

    assert settings.telegram_max_concurrent_searches == 2


def test_load_settings_reads_openai_options(tmp_path: Path) -> None:
    settings = load_settings(
        env={
            "TELEGRAM_BOT_TOKEN": "token",
            "HYBRID_AI_MODE": "shadow",
            "OPENAI_API_KEY": "sk-test",
            "OPENAI_MODEL": "test-model",
            "OPENAI_TIMEOUT_SECONDS": "45",
            "OPENAI_MAX_REFINES": "2",
        },
        project_root=tmp_path,
    )

    assert settings.hybrid_ai_mode == "shadow"
    assert settings.openai_api_key == "sk-test"
    assert settings.openai_model == "test-model"
    assert settings.openai_timeout_seconds == 45
    assert settings.openai_max_refines == 2


def test_load_settings_requires_openai_key_when_ai_mode_enabled(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="OPENAI_API_KEY is required"):
        load_settings(
            env={
                "TELEGRAM_BOT_TOKEN": "token",
                "HYBRID_AI_MODE": "shadow",
            },
            project_root=tmp_path,
        )


def test_load_settings_reads_search_cache_ttl(tmp_path: Path) -> None:
    settings = load_settings(
        env={
            "TELEGRAM_BOT_TOKEN": "token",
            "SEARCH_CACHE_TTL_SECONDS": "120",
        },
        project_root=tmp_path,
    )

    assert settings.search_cache_ttl_seconds == 120


def test_load_settings_reads_search_max_concurrent_searches(tmp_path: Path) -> None:
    settings = load_search_settings(
        env={
            "SEARCH_MAX_CONCURRENT_SEARCHES": "2",
        },
        project_root=tmp_path,
    )

    assert settings.search_max_concurrent_searches == 2


def test_load_settings_reads_search_retrieval_concurrency(tmp_path: Path) -> None:
    settings = load_search_settings(
        env={
            "SEARCH_RETRIEVAL_CONCURRENCY": "2",
        },
        project_root=tmp_path,
    )

    assert settings.search_retrieval_concurrency == 2


def test_load_settings_rejects_unbounded_search_retrieval_concurrency(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ConfigError,
        match=f"SEARCH_RETRIEVAL_CONCURRENCY must be at most {MAX_SEARCH_RETRIEVAL_CONCURRENCY}",
    ):
        load_search_settings(
            env={
                "SEARCH_RETRIEVAL_CONCURRENCY": str(
                    MAX_SEARCH_RETRIEVAL_CONCURRENCY + 1
                ),
            },
            project_root=tmp_path,
        )


def test_load_settings_reads_watchfacts_http_client_options(tmp_path: Path) -> None:
    settings = load_search_settings(
        env={
            "WATCHFACTS_HTTP_CLIENT_ENABLED": "false",
            "WATCHFACTS_FORM_CACHE_TTL_SECONDS": "1200",
            "WATCHFACTS_HTTP_CONNECT_TIMEOUT_SECONDS": "7",
            "WATCHFACTS_HTTP_POOL_TIMEOUT_SECONDS": "3",
            "WATCHFACTS_HTTP_KEEPALIVE_EXPIRY_SECONDS": "11",
            "WATCHFACTS_HTTP_READ_TIMEOUT_SECONDS": "13",
            "WATCHFACTS_HTTP_SEARCH_READ_TIMEOUT_SECONDS": "97",
            "WATCHFACTS_HTTP_FAILURE_COOLDOWN_SECONDS": "17",
            "WATCHFACTS_HTTP_WARMUP_ON_HEALTH": "false",
        },
        project_root=tmp_path,
    )

    assert settings.watchfacts_http_client_enabled is False
    assert settings.watchfacts_form_cache_ttl_seconds == 1200
    assert settings.watchfacts_http_connect_timeout_seconds == 7
    assert settings.watchfacts_http_pool_timeout_seconds == 3
    assert settings.watchfacts_http_keepalive_expiry_seconds == 11
    assert settings.watchfacts_http_read_timeout_seconds == 13
    assert settings.watchfacts_http_search_read_timeout_seconds == 97
    assert settings.watchfacts_http_failure_cooldown_seconds == 17
    assert settings.watchfacts_http_warmup_on_health is False


def test_load_settings_reads_openwa_handoff_options(tmp_path: Path) -> None:
    settings = load_settings(
        env={
            "TELEGRAM_BOT_TOKEN": "token",
            "ENABLE_OPENWA_CHAT_HANDOFF": "true",
            "OPENWA_BASE_URL": "https://openwa.example/",
            "OPENWA_API_KEY": "openwa-secret",
            "OPENWA_DASHBOARD_URL": "https://dashboard.example/",
            "OPENWA_CHAT_DRAFT_ENDPOINT": "api/custom-drafts",
        },
        project_root=tmp_path,
    )

    assert settings.enable_openwa_chat_handoff is True
    assert settings.openwa_base_url == "https://openwa.example"
    assert settings.openwa_api_key == "openwa-secret"
    assert settings.openwa_dashboard_url == "https://dashboard.example"
    assert settings.openwa_chat_draft_endpoint == "/api/custom-drafts"


def test_load_settings_reads_result_page_options(tmp_path: Path) -> None:
    settings = load_search_settings(
        env={
            "RESULT_PAGE_PUBLIC_BASE_URL": "https://mcp.example/results/",
            "RESULT_PAGE_TTL_SECONDS": "60",
            "RESULT_PAGE_MAX_RESULTS": "25",
            "RESULT_PAGE_STORAGE_DIR": "tmp/result-pages",
        },
        project_root=tmp_path,
    )

    assert settings.result_page_public_base_url == "https://mcp.example/results"
    assert settings.result_page_ttl_seconds == 60
    assert settings.result_page_max_results == 25
    assert settings.result_page_storage_dir == tmp_path / "tmp" / "result-pages"


def test_load_settings_rejects_empty_result_page_storage_dir(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="RESULT_PAGE_STORAGE_DIR must not be empty"):
        load_search_settings(
            env={"RESULT_PAGE_STORAGE_DIR": " "},
            project_root=tmp_path,
        )


def test_load_settings_does_not_enable_openwa_from_legacy_handoff_names(
    tmp_path: Path,
) -> None:
    old_enable_name = "ENABLE_OPENWA_" + "DEAL_HANDOFF"
    settings = load_settings(
        env={
            "TELEGRAM_BOT_TOKEN": "token",
            old_enable_name: "true",
            "OPENWA_BASE_URL": "https://openwa.example",
            "OPENWA_API_KEY": "openwa-secret",
            "OPENWA_DASHBOARD_URL": "https://dashboard.example",
        },
        project_root=tmp_path,
    )

    assert settings.enable_openwa_chat_handoff is False
    assert settings.openwa_chat_draft_endpoint == DEFAULT_OPENWA_CHAT_DRAFT_ENDPOINT
