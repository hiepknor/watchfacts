from __future__ import annotations

from scripts.diagnostics import runtime_config


def test_render_text_emits_only_safe_runtime_keys() -> None:
    payload = {key: f"value-{index}" for index, key in enumerate(runtime_config.SAFE_RUNTIME_KEYS)}

    text = runtime_config.render_text(payload)

    assert "search_cache_ttl_seconds=value-0" in text
    assert "telegram_bot_token" not in text
    assert "openwa_api_key" not in text
    assert "openai_api_key" not in text


def test_safe_runtime_config_payload_omits_secrets(monkeypatch, tmp_path) -> None:
    settings = runtime_config.load_search_settings(
        env={
            "OPENAI_API_KEY": "secret-openai",
            "OPENWA_API_KEY": "secret-openwa",
            "SEARCH_CACHE_TTL_SECONDS": "1800",
        },
        project_root=tmp_path,
    )
    monkeypatch.setattr(runtime_config, "load_search_settings", lambda: settings)

    payload = runtime_config.safe_runtime_config_payload()

    assert payload["search_cache_ttl_seconds"] == 1800
    assert "openai_api_key" not in payload
    assert "openwa_api_key" not in payload
