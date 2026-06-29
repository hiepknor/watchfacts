from __future__ import annotations

from app import main as app_main
from app.config import Settings


def test_main_returns_config_error_exit_code(monkeypatch) -> None:
    def raise_config_error():
        raise app_main.ConfigError("missing")

    monkeypatch.setattr(app_main, "load_settings", raise_config_error)

    assert app_main.main() == 2


def test_main_healthcheck_exits_without_loading_settings(monkeypatch) -> None:
    monkeypatch.setattr(app_main.sys, "argv", ["python", "--healthcheck"])

    def fail_load_settings():
        raise AssertionError("healthcheck should not load settings")

    monkeypatch.setattr(app_main, "load_settings", fail_load_settings)

    assert app_main.main() == 0


def test_main_starts_bot_with_loaded_settings(monkeypatch, tmp_path) -> None:
    settings = Settings(
        telegram_bot_token="token",
        telegram_allowed_user_ids=(),
        telegram_result_limit=5,
        watchfacts_url="https://example.test",
        headless=True,
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        logs_dir=tmp_path / "logs",
        db_path=tmp_path / "data" / "bot.db",
        browser_state_path=tmp_path / "data" / "watchfacts_state.json",
    )
    started: list[Settings] = []

    monkeypatch.setattr(app_main, "load_settings", lambda: settings)
    monkeypatch.setattr(app_main, "run_bot", started.append)

    assert app_main.main() == 0
    assert started == [settings]
