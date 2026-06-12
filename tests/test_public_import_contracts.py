from __future__ import annotations

import importlib


PUBLIC_MODULE_ALIASES = {
    "app.ai_refiner": "app.integrations.ai_refiner",
    "app.dedupe": "app.searching.dedupe",
    "app.fuzzy_diagnostics": "app.searching.fuzzy_diagnostics",
    "app.issues": "app.searching.issues",
    "app.match_debug": "app.searching.match_debug",
    "app.matcher": "app.searching.matcher",
    "app.matcher_aliases": "app.searching.matcher_aliases",
    "app.matcher_normalization": "app.searching.matcher_normalization",
    "app.matcher_rulebook": "app.searching.matcher_rulebook",
    "app.matcher_rules": "app.searching.matcher_rules",
    "app.matcher_token_classification": "app.searching.matcher_token_classification",
    "app.mcp_server": "app.runtime.mcp_server",
    "app.openwa_handoff": "app.integrations.openwa_handoff",
    "app.parser": "app.searching.parser",
    "app.query_intent": "app.searching.query_intent",
    "app.result_pages": "app.results.result_pages",
    "app.result_scoring": "app.searching.result_scoring",
    "app.scraper": "app.integrations.scraper",
    "app.search": "app.searching.search",
    "app.search_contracts": "app.searching.search_contracts",
    "app.search_result": "app.searching.search_result",
    "app.similarity": "app.searching.similarity",
    "app.telegram_bot": "app.runtime.telegram_bot",
    "app.tool_runtime": "app.runtime.tool_runtime",
    "app.watchfacts_forms": "app.integrations.watchfacts_forms",
    "app.watchfacts_http": "app.integrations.watchfacts_http",
}


def test_public_module_imports_resolve_to_domain_implementations() -> None:
    for public_name, implementation_name in PUBLIC_MODULE_ALIASES.items():
        public_module = importlib.import_module(public_name)
        implementation_module = importlib.import_module(implementation_name)

        assert public_module is implementation_module
        assert public_module.__name__ == implementation_name


def test_public_logger_names_remain_stable_after_module_move() -> None:
    expected_logger_names = {
        "app.ai_refiner": "app.ai_refiner",
        "app.mcp_server": "app.mcp_server",
        "app.search": "app.search",
        "app.telegram_bot": "app.telegram_bot",
        "app.tool_runtime": "app.tool_runtime",
        "app.watchfacts_http": "app.watchfacts_http",
    }

    for module_name, logger_name in expected_logger_names.items():
        module = importlib.import_module(module_name)

        assert module.logger.name == logger_name


def test_public_mcp_entrypoint_exposes_main() -> None:
    module = importlib.import_module("app.mcp_server")

    assert callable(module.main)
