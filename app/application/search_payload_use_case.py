from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from app.searching.search_result import SearchResult


class SearchWorkflow(Protocol):
    async def search(self, query: str) -> list[SearchResult]:
        ...


StoreResults = Callable[[str, list[SearchResult], int], None]
GenerateResultPage = Callable[..., dict[str, object] | None]


@dataclass(frozen=True)
class SearchPayloadPage:
    query: str
    results: tuple[SearchResult, ...]
    visible_results: tuple[SearchResult, ...]
    total_count: int
    offset: int
    limit: int | None
    result_count: int
    truncated: bool
    has_more: bool
    next_offset: int | None
    result_cache_ttl_seconds: int
    search_diagnostics: dict[str, object] | None = None
    result_page: dict[str, object] | None = None


@dataclass
class SearchPayloadUseCase:
    workflow: SearchWorkflow
    result_cache_ttl_seconds: int
    store_results: StoreResults | None = None
    generate_result_page: GenerateResultPage | None = None

    async def search_page(
        self,
        query: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> SearchPayloadPage:
        results = await self.workflow.search(query)
        if self.store_results is not None:
            self.store_results(query, results, self.result_cache_ttl_seconds)

        visible_results = (
            results[offset : offset + limit] if limit is not None else results[offset:]
        )
        next_offset = offset + len(visible_results)
        has_more = next_offset < len(results)
        result_page = self._generate_result_page(
            query=query,
            results=results,
            offset=offset,
            limit=limit,
            total_count=len(results),
            next_offset=next_offset if has_more else None,
        )
        return SearchPayloadPage(
            query=query,
            results=tuple(results),
            visible_results=tuple(visible_results),
            total_count=len(results),
            offset=offset,
            limit=limit,
            result_count=len(visible_results),
            truncated=offset > 0 or has_more,
            has_more=has_more,
            next_offset=next_offset if has_more else None,
            result_cache_ttl_seconds=self.result_cache_ttl_seconds,
            search_diagnostics=_search_diagnostics_payload(self.workflow),
            result_page=result_page,
        )

    def _generate_result_page(
        self,
        **kwargs: Any,
    ) -> dict[str, object] | None:
        if self.generate_result_page is None:
            return None
        result_page = self.generate_result_page(**kwargs)
        if result_page is None:
            return None
        to_payload = getattr(result_page, "to_payload", None)
        if callable(to_payload):
            payload = to_payload()
            return payload if isinstance(payload, dict) else None
        return result_page if isinstance(result_page, dict) else None


def _search_diagnostics_payload(workflow: SearchWorkflow) -> dict[str, object] | None:
    diagnostics = getattr(workflow, "last_search_diagnostics", None)
    if diagnostics is None:
        return None
    to_payload = getattr(diagnostics, "to_payload", None)
    if callable(to_payload):
        payload = to_payload()
        return payload if isinstance(payload, dict) else None
    if isinstance(diagnostics, dict):
        return diagnostics
    return None
