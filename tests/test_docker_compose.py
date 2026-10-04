from __future__ import annotations

from pathlib import Path


def test_watchfacts_web_service_has_http_healthcheck() -> None:
    compose = Path("docker-compose.yml").read_text()
    service = compose.split("  watchfacts-web:", 1)[1]

    assert "    healthcheck:" in service
    assert "http://127.0.0.1:8766/readyz" in service
    assert 'command: ["python", "-m", "app.web_server"]' in service


def test_watchfacts_web_is_published_on_localhost_only() -> None:
    compose = Path("docker-compose.yml").read_text()
    service = compose.split("  watchfacts-web:", 1)[1]

    assert '      - "127.0.0.1:8765:8766"' in service
    assert '      - "0.0.0.0:8765:8766"' not in service
    assert '      - "8765:8766"' not in service


def test_watchfacts_web_has_least_privilege_runtime_boundary() -> None:
    compose = Path("docker-compose.yml").read_text()
    service = compose.split("  watchfacts-web:", 1)[1]

    assert "    env_file:" not in service
    assert "target: web" in service
    assert "./data/database:/app/runtime/database" in service
    assert "./data/result_pages:/app/runtime/result_pages" in service
    assert "./data/browser" not in service
    assert "./logs" not in service
    assert "./data:/app/data" not in service


def test_watchfacts_bot_keeps_browser_state_outside_shared_mounts() -> None:
    compose = Path("docker-compose.yml").read_text()
    service = compose.split("  watchfacts-bot:", 1)[1].split(
        "  watchfacts-web:", 1
    )[0]

    assert "target: bot" in service
    assert "./data/browser:/app/runtime/browser" in service
    assert "./data/database:/app/runtime/database" in service
    assert "./data/result_pages:/app/runtime/result_pages" in service
    assert "./data:/app/data" not in service


def test_bot_and_web_targets_use_distinct_image_tags() -> None:
    compose = Path("docker-compose.yml").read_text()
    bot = compose.split("  watchfacts-bot:", 1)[1].split("  watchfacts-web:", 1)[0]
    web = compose.split("  watchfacts-web:", 1)[1]

    assert "image: ${IMAGE:-watchfacts:local}-bot" in bot
    assert "image: ${IMAGE:-watchfacts:local}-web" in web
