from __future__ import annotations

import ast
from pathlib import Path


DETERMINISTIC_DOMAIN_FILES = {
    "app/searching/dedupe.py",
    "app/searching/fuzzy_diagnostics.py",
    "app/searching/issues.py",
    "app/searching/match_debug.py",
    "app/searching/matcher.py",
    "app/searching/matcher_aliases.py",
    "app/searching/matcher_normalization.py",
    "app/searching/matcher_rulebook.py",
    "app/searching/matcher_rules.py",
    "app/searching/matcher_token_classification.py",
    "app/searching/parser.py",
    "app/searching/query_intent.py",
    "app/searching/result_scoring.py",
    "app/searching/search_contracts.py",
    "app/searching/search_result.py",
}
PROHIBITED_DOMAIN_IMPORT_PREFIXES = (
    "app.runtime",
    "app.integrations",
    "app.results",
    "app.db",
    "httpx",
    "mcp",
    "playwright",
    "sqlite3",
    "telegram",
)
PROHIBITED_APPLICATION_IMPORT_PREFIXES = (
    "app.db",
    "app.runtime",
    "mcp",
    "telegram",
)


def test_deterministic_domain_modules_do_not_import_runtime_or_infrastructure() -> None:
    violations: list[str] = []
    for filename in sorted(DETERMINISTIC_DOMAIN_FILES):
        path = Path(filename)
        for imported in _imported_modules(path):
            if _is_prohibited_domain_import(imported):
                violations.append(f"{filename} imports {imported}")

    assert violations == []


def test_search_orchestration_exceptions_are_explicit() -> None:
    documented_exceptions = {
        "app/searching/search.py",
        "app/searching/similarity.py",
    }
    existing_files = {
        str(path)
        for path in Path("app/searching").glob("*.py")
        if path.name not in {"__init__.py"}
    }

    assert documented_exceptions <= existing_files
    assert DETERMINISTIC_DOMAIN_FILES.isdisjoint(documented_exceptions)
    assert existing_files == DETERMINISTIC_DOMAIN_FILES | documented_exceptions


def test_application_use_cases_do_not_import_interface_adapters() -> None:
    violations: list[str] = []
    for path in sorted(Path("app/application").glob("*.py")):
        if path.name == "__init__.py":
            continue
        for imported in _imported_modules(path):
            if _is_prohibited_application_import(imported):
                violations.append(f"{path} imports {imported}")

    assert violations == []


def test_infrastructure_modules_do_not_import_interface_adapters() -> None:
    violations: list[str] = []
    for path in sorted(Path("app/infrastructure").glob("*.py")):
        if path.name == "__init__.py":
            continue
        for imported in _imported_modules(path):
            if _is_prohibited_infrastructure_import(imported):
                violations.append(f"{path} imports {imported}")

    assert violations == []


def test_tool_runtime_uses_repository_boundary_for_result_references() -> None:
    source = Path("app/runtime/tool_runtime.py").read_text(encoding="utf-8")

    assert "record_search_result_references" not in source
    assert "get_fresh_search_result_reference_by_id" not in source
    assert "get_fresh_search_result_reference_by_stable_listing_id" not in source
    assert "get_fresh_search_result_reference_by_rank" not in source


def test_search_workflow_uses_repository_boundary_for_search_cache() -> None:
    source = Path("app/searching/search.py").read_text(encoding="utf-8")

    assert "self.database.record_query_results(" not in source
    assert "self.database.get_search_cache_quality_metrics(" not in source
    assert "self.database.get_fresh_search_cache_row(" not in source
    assert "self.database.record_search_cache(" not in source
    assert "self.database.record_suspicious_result(" not in source
    assert "self.database.record_ai_refinement_suggestion(" not in source


def test_telegram_bot_uses_repository_boundary_for_ai_suggestions() -> None:
    source = Path("app/runtime/telegram_bot.py").read_text(encoding="utf-8")

    assert "_issue_database(context).list_ai_refinement_suggestions(" not in source
    assert "_issue_database(context).get_ai_refinement_suggestion(" not in source
    assert "_issue_database(context).mark_ai_refinement_suggestion_status(" not in source
    assert "_issue_database(context).export_reviewed_ai_suggestions(" not in source
    assert "database.record_ai_refinement_suggestion(" not in source


def test_openai_requests_use_infrastructure_client_boundary() -> None:
    client_source = Path("app/infrastructure/openai_client.py").read_text(
        encoding="utf-8"
    )
    assert "https://api.openai.com/v1/responses" in client_source

    for filename in (
        "app/integrations/ai_refiner.py",
        "scripts/diagnostics/ai_audit_triage.py",
    ):
        source = Path(filename).read_text(encoding="utf-8")
        assert "urllib.request" not in source
        assert "api.openai.com" not in source
        assert "OpenAIResponsesClient" in source


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def _is_prohibited_domain_import(module_name: str) -> bool:
    return any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in PROHIBITED_DOMAIN_IMPORT_PREFIXES
    )


def _is_prohibited_application_import(module_name: str) -> bool:
    return any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in PROHIBITED_APPLICATION_IMPORT_PREFIXES
    )


def _is_prohibited_infrastructure_import(module_name: str) -> bool:
    return any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in ("app.runtime", "mcp", "telegram")
    )
