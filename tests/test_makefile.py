from __future__ import annotations

from pathlib import Path


def _target(makefile: str, name: str, next_name: str) -> str:
    return makefile.split(f"\n{name}:", 1)[1].split(f"\n\n{next_name}:", 1)[0]


def test_make_check_runs_repository_gates() -> None:
    makefile = Path("Makefile").read_text()
    target = _target(makefile, "check", "clean")

    assert "git diff --check" in target
    assert "$(PYTHON) -m pytest -q" in target
    assert "$(PYTHON) -m compileall" in target
    assert "$(COMPOSE) config" in target


def test_makefile_deploys_bot_and_web_as_scoped_services() -> None:
    makefile = Path("Makefile").read_text()

    assert "BOT_SERVICE ?= watchfacts-bot" in makefile
    assert "WEB_SERVICE ?= watchfacts-web" in makefile
    assert "LEGACY_MCP_CONTAINER ?= watchfacts-mcp" in makefile
    assert "\ndeploy: deploy-bot-web\n" in makefile
    assert "\ndeploy-bot: verify-bot-env pull build predeploy-check\n" in makefile
    assert "\ndeploy-web: verify-env pull web-build web-predeploy-check\n" in makefile
    assert "\ndeploy-bot-web: deploy-bot deploy-web\n" in makefile
    assert "TELEGRAM_BOT_TOKEN is missing or still set to the placeholder" in makefile
    assert "docker rm -f $(LEGACY_BOT_CONTAINER)" in makefile
    assert "docker rm -f $(LEGACY_MCP_CONTAINER)" in makefile


def test_makefile_has_runtime_quality_and_benchmark_targets() -> None:
    makefile = Path("Makefile").read_text()
    runtime_smoke = _target(makefile, "runtime-smoke", "search-benchmark")
    benchmark = _target(makefile, "search-benchmark", "search-cold-budget")
    cold_budget = _target(makefile, "search-cold-budget", "search-prewarm")
    prewarm = _target(makefile, "search-prewarm", "search-prewarm-benchmark-defaults")

    assert "scripts/diagnostics/runtime_smoke.py" in runtime_smoke
    assert "scripts/diagnostics/benchmark_search_queries.py" in benchmark
    assert "--repeat $(SEARCH_BENCHMARK_REPEAT)" in benchmark
    assert "$(SEARCH_BENCHMARK_EXTRA_ARGS)" in benchmark
    assert "--clear-search-cache" in cold_budget
    assert "--use-cold-path-budget-defaults" in cold_budget
    assert "scripts/diagnostics/prewarm_search_cache.py" in prewarm
    assert "$(SEARCH_PREWARM_VERIFY_HOT_ARGS)" in prewarm


def test_makefile_has_search_engine_deploy_gates() -> None:
    makefile = Path("Makefile").read_text()
    predeploy = _target(
        makefile,
        "search-engine-predeploy-check",
        "search-engine-postdeploy-check",
    )
    postdeploy = _target(
        makefile,
        "search-engine-postdeploy-check",
        "production-postdeploy-check",
    )

    assert "$(PYTHON) -m pytest -q" in predeploy
    assert "$(PYTHON) -m compileall app scripts" in predeploy
    assert "scripts/diagnostics/audit_quality.py" in predeploy
    assert "$(MAKE) web-wait-healthy" in postdeploy
    assert "$(MAKE) runtime-smoke" in postdeploy
    assert "$(MAKE) search-cold-budget" in postdeploy
    assert "$(MAKE) search-prewarm-benchmark-defaults" in postdeploy
    assert "$(MAKE) search-benchmark" in postdeploy


def test_makefile_has_baseline_and_quality_targets() -> None:
    makefile = Path("Makefile").read_text()
    baseline = _target(makefile, "search-engine-baseline-snapshot", "quality-audit")
    quality = _target(makefile, "quality-audit", "ai-audit-triage")

    assert "$(MAKE) runtime-config" in baseline
    assert "hot-benchmark.md" in baseline
    assert "cold-benchmark.md" in baseline
    assert "cold-budget.md" in baseline
    assert "scripts/diagnostics/audit_quality.py" in quality


def test_makefile_has_no_mcp_runtime_targets() -> None:
    makefile = Path("Makefile").read_text().casefold()

    assert "deploy-mcp" not in makefile
    assert "mcp-" not in makefile
