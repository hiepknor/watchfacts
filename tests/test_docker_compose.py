from __future__ import annotations

from pathlib import Path


def test_watchfacts_web_service_has_http_healthcheck() -> None:
    compose = Path("docker-compose.yml").read_text()
    service = compose.split("  watchfacts-web:", 1)[1]

    assert "    healthcheck:" in service
    assert "http://127.0.0.1:8766/healthz" in service
    assert 'command: ["python", "-m", "app.web_server"]' in service


def test_watchfacts_web_is_published_on_localhost_only() -> None:
    compose = Path("docker-compose.yml").read_text()
    service = compose.split("  watchfacts-web:", 1)[1]

    assert '      - "127.0.0.1:8765:8766"' in service
    assert '      - "0.0.0.0:8765:8766"' not in service
    assert '      - "8765:8766"' not in service
