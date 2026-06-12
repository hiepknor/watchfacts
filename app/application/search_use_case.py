from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from app.config import Settings
from app.searching.search_result import SearchResult


class SearchWorkflow(Protocol):
    async def search(self, query: str) -> list[SearchResult]:
        ...


WorkflowFactory = Callable[..., SearchWorkflow]
RefineResults = Callable[[str, list[SearchResult]], Awaitable[list[SearchResult]]]


@dataclass
class SearchUseCase:
    workflow: SearchWorkflow

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        workflow_factory: WorkflowFactory | None = None,
        database: Any = None,
        ai_suggestion_repository: Any = None,
        issue_repository: Any = None,
        search_cache_repository: Any = None,
        fetch_html: Any = None,
        refine_results: RefineResults | None = None,
    ) -> "SearchUseCase":
        if workflow_factory is None:
            from app.searching.search import WatchFactsSearchWorkflow

            workflow_factory = WatchFactsSearchWorkflow

        kwargs: dict[str, Any] = {}
        if database is not None:
            kwargs["database"] = database
        if ai_suggestion_repository is not None:
            kwargs["ai_suggestion_repository"] = ai_suggestion_repository
        if issue_repository is not None:
            kwargs["issue_repository"] = issue_repository
        if search_cache_repository is not None:
            kwargs["search_cache_repository"] = search_cache_repository
        if fetch_html is not None:
            kwargs["fetch_html"] = fetch_html
        if refine_results is not None:
            kwargs["refine_results"] = refine_results
        return cls(workflow_factory(settings, **kwargs))

    async def search(self, query: str) -> list[SearchResult]:
        return await self.workflow.search(query)

    @property
    def last_search_diagnostics(self) -> Any:
        return getattr(self.workflow, "last_search_diagnostics", None)

    @property
    def last_search_audit_events(self) -> Any:
        return getattr(self.workflow, "last_search_audit_events", ())
