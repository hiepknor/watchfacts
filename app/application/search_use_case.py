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
        workflow_factory: WorkflowFactory,
        **workflow_options: Any,
    ) -> "SearchUseCase":
        """Compatibility constructor for explicitly supplied workflow factories."""

        return cls(workflow_factory(settings, **workflow_options))

    async def search(self, query: str) -> list[SearchResult]:
        return await self.workflow.search(query)

    @property
    def last_search_diagnostics(self) -> Any:
        return getattr(self.workflow, "last_search_diagnostics", None)

    @property
    def last_search_audit_events(self) -> Any:
        return getattr(self.workflow, "last_search_audit_events", ())
