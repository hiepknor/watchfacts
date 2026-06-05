from __future__ import annotations

from pathlib import Path


def test_watchfacts_mcp_service_has_lightweight_healthcheck() -> None:
    compose_text = Path("docker-compose.yml").read_text()
    service_start = compose_text.index("  watchfacts-mcp:")
    service_text = compose_text[service_start:]

    assert "    healthcheck:" in service_text
    assert "socket.create_connection(('127.0.0.1', 8765), 5)" in service_text
    assert 'command: ["python", "-m", "app.mcp_server"]' in service_text
