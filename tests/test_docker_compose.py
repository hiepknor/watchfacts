from __future__ import annotations

from pathlib import Path


def test_watchfacts_mcp_service_has_lightweight_healthcheck() -> None:
    compose_text = Path("docker-compose.yml").read_text()
    service_start = compose_text.index("  watchfacts-mcp:")
    service_text = compose_text[service_start:]

    assert "    healthcheck:" in service_text
    healthcheck_text = (
        service_text.split("healthcheck:", 1)[1].split("    command:", 1)[0]
    )
    assert '"python"' in healthcheck_text
    assert "socket.create_connection(('127.0.0.1', 8765), 5)" in healthcheck_text
    assert '"app.mcp_server"' not in healthcheck_text
    assert "--healthcheck" not in healthcheck_text
    assert 'command: ["python", "-m", "app.mcp_server"]' in service_text


def test_watchfacts_mcp_override_joins_openwa_network() -> None:
    compose_text = Path("docker-compose.watchfacts-mcp.yml").read_text()

    assert "  watchfacts-mcp:" in compose_text
    service_text = compose_text.split("  watchfacts-mcp:", 1)[1].split(
        "\n\nnetworks:",
        1,
    )[0]
    assert "      - openwa" in service_text
    assert "  openwa:" in compose_text
    assert "name: ${OPENWA_DOCKER_NETWORK:-openwa-network}" in compose_text
