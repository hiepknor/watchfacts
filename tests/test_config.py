from pathlib import Path

import pytest

from app.config import (
    ConfigError,
    DEFAULT_LOCAL_LLM_BASE_URL,
    DEFAULT_LOCAL_LLM_MAX_REFINES,
    DEFAULT_LOCAL_LLM_MODEL,
    DEFAULT_LOCAL_LLM_TIMEOUT_SECONDS,
    DEFAULT_TELEGRAM_RESULT_LIMIT,
    DEFAULT_WATCHFACTS_URL,
    load_settings,
    parse_bool,
    parse_positive_int,
    parse_user_ids,
)


def test_load_settings_requires_telegram_token() -> None:
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN is required"):
        load_settings(env={})


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
    assert settings.telegram_allowed_user_ids == ()
    assert settings.telegram_result_limit == DEFAULT_TELEGRAM_RESULT_LIMIT
    assert settings.watchfacts_url == DEFAULT_WATCHFACTS_URL
    assert settings.headless is True
    assert settings.enable_crawl4ai is True
    assert settings.local_llm_enabled is False
    assert settings.local_llm_base_url == DEFAULT_LOCAL_LLM_BASE_URL
    assert settings.local_llm_model == DEFAULT_LOCAL_LLM_MODEL
    assert settings.local_llm_timeout_seconds == DEFAULT_LOCAL_LLM_TIMEOUT_SECONDS
    assert settings.local_llm_max_refines == DEFAULT_LOCAL_LLM_MAX_REFINES
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


def test_load_settings_reads_local_llm_options(tmp_path: Path) -> None:
    settings = load_settings(
        env={
            "TELEGRAM_BOT_TOKEN": "token",
            "LOCAL_LLM_ENABLED": "true",
            "LOCAL_LLM_BASE_URL": "http://llama-cpp:8080",
            "LOCAL_LLM_MODEL": "gemma4-e2b-q4",
            "LOCAL_LLM_TIMEOUT_SECONDS": "45",
            "LOCAL_LLM_MAX_REFINES": "2",
        },
        project_root=tmp_path,
    )

    assert settings.local_llm_enabled is True
    assert settings.local_llm_base_url == "http://llama-cpp:8080"
    assert settings.local_llm_model == "gemma4-e2b-q4"
    assert settings.local_llm_timeout_seconds == 45
    assert settings.local_llm_max_refines == 2
