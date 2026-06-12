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
    ].split("\n\ncheck:", 1)[0]

    assert "scripts/diagnostics/audit_quality.py" in quality_target
    assert "--limit $(QUALITY_AUDIT_LIMIT)" in quality_target
    assert "check quality-audit" in predeploy_quality_target


def test_makefile_deploy_targets_are_scoped() -> None:
    makefile = Path("Makefile").read_text()

    assert "\ndeploy: deploy-bot-mcp\n" in makefile
    assert "\ndeploy-bot: verify-env pull build predeploy-check\n" in makefile
    assert "\ndeploy-mcp: verify-env pull mcp-build mcp-predeploy-check\n" in makefile
    assert "\ndeploy-bot-mcp: deploy-bot deploy-mcp\n" in makefile


def test_makefile_has_mcp_smoke_set_target() -> None:
    makefile = Path("Makefile").read_text()
    smoke_set_target = makefile.split("\nmcp-smoke-set:", 1)[1].split(
        "\n\nmcp-wait-healthy:",
        1,
    )[0]

    assert "scripts/diagnostics/mcp_smoke.py" in smoke_set_target
    assert '--url "$(MCP_SMOKE_URL)"' in smoke_set_target


def test_makefile_has_no_removed_external_agent_targets() -> None:
    makefile = Path("Makefile").read_text()
    removed_agent_name = "her" + "mes"

    assert removed_agent_name.upper() not in makefile
    assert removed_agent_name not in makefile


def test_makefile_has_mcp_benchmark_target() -> None:
    makefile = Path("Makefile").read_text()
    benchmark_target = makefile.split("\nmcp-benchmark:", 1)[1].split(
        "\n\nmcp-wait-healthy:",
        1,
    )[0]

    assert "scripts/diagnostics/benchmark_mcp_queries.py" in benchmark_target
    assert '--url "$(MCP_SMOKE_URL)"' in benchmark_target
    assert "--format $(MCP_BENCHMARK_FORMAT)" in benchmark_target


def test_makefile_has_mcp_prewarm_target() -> None:
    makefile = Path("Makefile").read_text()
    prewarm_target = makefile.split("\nmcp-prewarm:", 1)[1].split(
        "\n\nmcp-wait-healthy:",
        1,
    )[0]

    assert "scripts/diagnostics/prewarm_mcp_cache.py" in prewarm_target
    assert '--url "$(MCP_SMOKE_URL)"' in prewarm_target
    assert "--format $(MCP_PREWARM_FORMAT)" in prewarm_target


def test_makefile_has_mcp_runtime_config_target() -> None:
    makefile = Path("Makefile").read_text()
    runtime_config_target = makefile.split("\nmcp-runtime-config:", 1)[1].split(
        "\n\nmcp-wait-healthy:",
        1,
    )[0]

    assert "scripts/diagnostics/runtime_config.py" in runtime_config_target
