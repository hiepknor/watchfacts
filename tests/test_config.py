from pathlib import Path

import pytest

from app.config import ConfigError, DEFAULT_WATCHFACTS_URL, load_settings, parse_bool


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


def test_load_settings_uses_defaults_and_runtime_paths(tmp_path: Path) -> None:
    settings = load_settings(
        env={"TELEGRAM_BOT_TOKEN": "token"},
        project_root=tmp_path,
    )

    assert settings.telegram_bot_token == "token"
    assert settings.watchfacts_url == DEFAULT_WATCHFACTS_URL
    assert settings.headless is True
    assert settings.enable_crawl4ai is True
    assert settings.data_dir == tmp_path / "data"
    assert settings.logs_dir == tmp_path / "logs"
    assert settings.db_path == tmp_path / "data" / "bot.db"
    assert settings.browser_state_path == tmp_path / "data" / "watchfacts_state.json"
