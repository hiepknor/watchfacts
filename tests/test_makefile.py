from __future__ import annotations

from pathlib import Path


def test_make_check_runs_repository_gates() -> None:
    makefile = Path("Makefile").read_text()
    check_target = makefile.split("\ncheck:", 1)[1].split("\n\nclean:", 1)[0]

    assert "git diff --check" in check_target
    assert "$(PYTHON) -m pytest -q" in check_target
    assert "$(PYTHON) -m compileall" in check_target
    assert "$(MCP_COMPOSE_CMD) config" in check_target
    assert "mcp-smoke" not in check_target


def test_makefile_has_authorized_httpx_smoke_target() -> None:
    makefile = Path("Makefile").read_text()
    smoke_target = makefile.split("\nmcp-smoke:", 1)[1].split(
        "\n\nrestart-hermes:",
        1,
    )[0]

    assert "benchmark_watchfacts_http.py" in smoke_target
    assert '--query "$(SMOKE_QUERY)"' in smoke_target
    assert "--warmup --repeat 1" in smoke_target
