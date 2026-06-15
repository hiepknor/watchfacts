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


def test_makefile_has_search_engine_deploy_gate_targets() -> None:
    makefile = Path("Makefile").read_text()
    predeploy_target = makefile.split("\nsearch-engine-predeploy-check:", 1)[
        1
    ].split("\n\nsearch-engine-postdeploy-check:", 1)[0]
    postdeploy_target = makefile.split("\nsearch-engine-postdeploy-check:", 1)[
        1
    ].split("\n\nsearch-engine-deploy-check:", 1)[0]
    deploy_target = makefile.split("\nsearch-engine-deploy-check:", 1)[
        1
    ].split("\n\nquality-audit:", 1)[0]

    assert "SEARCH_ENGINE_AUDIT_QUERIES" in makefile
    assert "rm07-01 rg snow" in makefile
    assert "rm07-01 rose gold" in makefile
    assert "rm07-01 white gold" in makefile
    assert "rm07-01 mother of pearl" in makefile
    assert "git diff --check" in predeploy_target
    assert "$(PYTHON) -m pytest -q" in predeploy_target
    assert "$(PYTHON) -m compileall app scripts" in predeploy_target
    assert "scripts/diagnostics/audit_quality.py" in predeploy_target
    assert "--limit $(SEARCH_ENGINE_AUDIT_LIMIT)" in predeploy_target
    assert "$(MAKE) mcp-smoke-set" in postdeploy_target
    assert "$(MAKE) mcp-benchmark" in postdeploy_target
    assert "search-engine-predeploy-check search-engine-postdeploy-check" in deploy_target


def test_makefile_has_search_engine_baseline_snapshot_target() -> None:
    makefile = Path("Makefile").read_text()
    target = makefile.split("\nsearch-engine-baseline-snapshot:", 1)[1].split(
        "\n\nquality-audit:",
        1,
    )[0]

    assert "SEARCH_ENGINE_BASELINE_DIR ?= logs/search-engine-baseline" in makefile
    assert "SEARCH_ENGINE_BASELINE_LABEL ?=" in makefile
    assert "set -e" in target
    assert "$(MAKE) mcp-runtime-config > \"$$out/runtime-config.txt\"" in target
    assert (
        "$(MAKE) MCP_BENCHMARK_FORMAT=markdown "
        "MCP_BENCHMARK_EXTRA_ARGS= mcp-benchmark"
    ) in target
    assert "hot-benchmark.md" in target
    assert (
        "$(MAKE) MCP_BENCHMARK_FORMAT=markdown "
        "MCP_BENCHMARK_EXTRA_ARGS=--clear-search-cache mcp-benchmark"
    ) in target
    assert "cold-benchmark.md" in target
    assert "$(MAKE) MCP_COLD_BUDGET_FORMAT=markdown mcp-cold-budget" in target
    assert "cold-budget.md" in target
    assert "SEARCH_ENGINE_BASELINE_DIR=$$out" in target


def test_makefile_has_ai_audit_triage_target() -> None:
    makefile = Path("Makefile").read_text()
    target = makefile.split("\nai-audit-triage:", 1)[1].split(
        "\n\npredeploy-quality-check:",
        1,
    )[0]

    assert "AI_AUDIT_ARTIFACT ?= audit-report.jsonl" in makefile
    assert "scripts/diagnostics/ai_audit_triage.py" in target
    assert '$(AI_AUDIT_TRIAGE_OPENAI)" = "1"' in target
    assert "--use-openai" in target


def test_makefile_deploy_targets_are_scoped() -> None:
    makefile = Path("Makefile").read_text()

    assert "IMAGE ?= watchfacts:local" in makefile
    assert "BOT_SERVICE ?= watchfacts-bot" in makefile
    assert "LEGACY_BOT_CONTAINER ?= watchfacts" in makefile
    assert "MCP_SERVICE ?= watchfacts-mcp" in makefile
    assert "SERVICE ?= bot" not in makefile
    assert "export IMAGE" in makefile
    assert "\ndeploy: deploy-bot-mcp\n" in makefile
    assert "verify-bot-env: verify-env" in makefile
    assert "TELEGRAM_BOT_TOKEN is missing or still set to the placeholder" in makefile
    assert "\ndeploy-bot: verify-bot-env pull build predeploy-check\n" in makefile
    assert "\ndeploy-mcp: verify-env pull mcp-build mcp-predeploy-check\n" in makefile
    assert "\ndeploy-bot-mcp: deploy-bot deploy-mcp\n" in makefile
    assert "docker rm -f $(LEGACY_BOT_CONTAINER)" in makefile
    assert "$(COMPOSE) up -d --force-recreate --remove-orphans $(BOT_SERVICE)" in makefile
    assert "$(MCP_COMPOSE_CMD) up -d --force-recreate --remove-orphans $(MCP_SERVICE)" in makefile


def test_compose_services_use_explicit_runtime_names() -> None:
    compose = Path("docker-compose.yml").read_text()
    openwa_compose = Path("docker-compose.openwa.yml").read_text()

    assert "\n  watchfacts-bot:\n" in compose
    assert "image: ${IMAGE:-watchfacts:local}" in compose
    assert "container_name: watchfacts-bot" in compose
    assert "\n  watchfacts-mcp:\n" in compose
    assert "container_name: watchfacts-mcp" in compose
    assert "\n  bot:\n" not in compose
    assert "\n  watchfacts-bot:\n" in openwa_compose
    assert "\n  bot:\n" not in openwa_compose


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
        "\n\nmcp-cold-budget:",
        1,
    )[0]
    cold_budget_target = makefile.split("\nmcp-cold-budget:", 1)[1].split(
        "\n\nmcp-prewarm:",
        1,
    )[0]

    assert "scripts/diagnostics/benchmark_mcp_queries.py" in benchmark_target
    assert '--url "$(MCP_SMOKE_URL)"' in benchmark_target
    assert "--repeat $(MCP_BENCHMARK_REPEAT)" in benchmark_target
    assert "--format $(MCP_BENCHMARK_FORMAT)" in benchmark_target
    assert "$(MCP_BENCHMARK_EXTRA_ARGS)" in benchmark_target
    assert "--clear-search-cache" in cold_budget_target
    assert "--use-cold-path-budget-defaults" in cold_budget_target
    assert "--format $(MCP_COLD_BUDGET_FORMAT)" in cold_budget_target


def test_makefile_has_mcp_prewarm_target() -> None:
    makefile = Path("Makefile").read_text()
    prewarm_target = makefile.split("\nmcp-prewarm:", 1)[1].split(
        "\n\nmcp-prewarm-benchmark-defaults:",
        1,
    )[0]
    benchmark_prewarm_target = makefile.split(
        "\nmcp-prewarm-benchmark-defaults:",
        1,
    )[1].split("\n\nmcp-postdeploy-prewarm:", 1)[0]
    postdeploy_prewarm_target = makefile.split(
        "\nmcp-postdeploy-prewarm:",
        1,
    )[1].split("\n\nmcp-runtime-config:", 1)[0]
    deploy_mcp_target = makefile.split("\ndeploy-mcp:", 1)[1].split(
        "\n\ndeploy-bot-mcp:",
        1,
    )[0]
    search_engine_postdeploy_target = makefile.split(
        "\nsearch-engine-postdeploy-check:",
        1,
    )[1].split("\n\nsearch-engine-deploy-check:", 1)[0]

    assert "scripts/diagnostics/prewarm_mcp_cache.py" in prewarm_target
    assert '--url "$(MCP_SMOKE_URL)"' in prewarm_target
    assert "$(MCP_PREWARM_VERIFY_HOT_ARGS)" in prewarm_target
    assert "--format $(MCP_PREWARM_FORMAT)" in prewarm_target
    assert "--use-benchmark-defaults" not in prewarm_target
    assert "--use-benchmark-defaults" in benchmark_prewarm_target
    assert "mcp-predeploy-check" in deploy_mcp_target
    assert "$(MAKE) mcp-postdeploy-prewarm" in deploy_mcp_target
    assert '$(MAKE) mcp-prewarm || printf "%s\\n"' in postdeploy_prewarm_target
    assert (
        '$(MAKE) mcp-prewarm-benchmark-defaults || printf "%s\\n"'
        in postdeploy_prewarm_target
    )
    assert "deploy remains active" in postdeploy_prewarm_target
    assert "$(MAKE) mcp-smoke-set" in search_engine_postdeploy_target
    assert "$(MAKE) mcp-cold-budget" in search_engine_postdeploy_target
    assert "$(MAKE) mcp-prewarm-benchmark-defaults" in search_engine_postdeploy_target
    assert "$(MAKE) mcp-benchmark" in search_engine_postdeploy_target
    assert search_engine_postdeploy_target.index("mcp-cold-budget") < (
        search_engine_postdeploy_target.index("mcp-prewarm-benchmark-defaults")
    )
    assert search_engine_postdeploy_target.index("mcp-prewarm-benchmark-defaults") < (
        search_engine_postdeploy_target.index("mcp-benchmark")
    )


def test_makefile_has_mcp_runtime_config_target() -> None:
    makefile = Path("Makefile").read_text()
    runtime_config_target = makefile.split("\nmcp-runtime-config:", 1)[1].split(
        "\n\nmcp-wait-healthy:",
        1,
    )[0]

    assert "scripts/diagnostics/runtime_config.py" in runtime_config_target
