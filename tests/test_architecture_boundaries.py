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
