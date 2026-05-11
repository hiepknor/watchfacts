from scripts import login


def test_login_settings_default_to_headed_browser(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(login, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(login, "load_dotenv", lambda _: None)
    monkeypatch.setenv("HEADLESS", "true")
    monkeypatch.delenv("LOGIN_HEADLESS", raising=False)

    settings = login.load_login_settings()

    assert settings.headless is False
    assert settings.browser_state_path == tmp_path / "data" / "watchfacts_state.json"


def test_login_settings_allow_explicit_headless_override(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(login, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(login, "load_dotenv", lambda _: None)
    monkeypatch.setenv("LOGIN_HEADLESS", "true")

    settings = login.load_login_settings()

    assert settings.headless is True
