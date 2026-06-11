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
        "\n\nmcp-smoke-set:",
        1,
    )[0]

    assert "benchmark_watchfacts_http.py" in smoke_target
    assert '--query "$(SMOKE_QUERY)"' in smoke_target
    assert "--warmup --repeat 1" in smoke_target


def test_makefile_has_quality_audit_gate_targets() -> None:
    makefile = Path("Makefile").read_text()
    quality_target = makefile.split("\nquality-audit:", 1)[1].split(
        "\n\npredeploy-quality-check:",
        1,
    )[0]
    predeploy_quality_target = makefile.split("\npredeploy-quality-check:", 1)[
        1
    ].split("\n\nrestart-hermes:", 1)[0]

    assert "scripts/diagnostics/audit_quality.py" in quality_target
    assert "--limit $(QUALITY_AUDIT_LIMIT)" in quality_target
    assert "check quality-audit" in predeploy_quality_target


def test_makefile_has_post_deploy_mcp_smoke_set() -> None:
    makefile = Path("Makefile").read_text()
    deploy_target = makefile.split("\ndeploy-hermes-mcp:", 1)[1].split(
        "\n\nupdate:",
        1,
    )[0]
    smoke_set_target = makefile.split("\nmcp-smoke-set:", 1)[1].split(
        "\n\nmcp-wait-healthy:",
        1,
    )[0]

    assert "mcp-wait-healthy restart-hermes mcp-smoke-set" in deploy_target
    assert "scripts/diagnostics/mcp_smoke.py" in smoke_set_target
    assert '--url "$(MCP_SMOKE_URL)"' in smoke_set_target


def test_makefile_has_mcp_benchmark_target() -> None:
    makefile = Path("Makefile").read_text()
    benchmark_target = makefile.split("\nmcp-benchmark:", 1)[1].split(
        "\n\nmcp-wait-healthy:",
        1,
    )[0]

    assert "scripts/diagnostics/benchmark_mcp_queries.py" in benchmark_target
    assert '--url "$(MCP_SMOKE_URL)"' in benchmark_target
    assert "--format $(MCP_BENCHMARK_FORMAT)" in benchmark_target
