from __future__ import annotations

from app.application import SearchUseCase
from app.config import load_search_settings
from app.infrastructure.search_workflow import (
    build_search_use_case,
    build_search_workflow,
)


def test_build_search_workflow_injects_explicit_ports(tmp_path) -> None:
    settings = load_search_settings(env={}, project_root=tmp_path)
    ai_repository = object()
    issue_repository = object()
    cache_repository = object()

    async def fetch_html(*_args, **_kwargs):
        raise AssertionError("not called")

    workflow = build_search_workflow(
        settings,
        ai_suggestion_repository=ai_repository,
        issue_repository=issue_repository,
        search_cache_repository=cache_repository,
        fetch_html=fetch_html,
    )

    assert workflow.ai_suggestion_repository is ai_repository
    assert workflow.issue_repository is issue_repository
    assert workflow.search_cache_repository is cache_repository
    assert workflow.fetch_html is fetch_html
    assert not hasattr(workflow, "database")


def test_build_search_use_case_wraps_composed_workflow(tmp_path) -> None:
    settings = load_search_settings(env={}, project_root=tmp_path)

    use_case = build_search_use_case(settings)

    assert isinstance(use_case, SearchUseCase)
    assert use_case.workflow.settings is settings
