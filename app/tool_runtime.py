from __future__ import annotations

from typing import Protocol

from app.config import Settings, load_search_settings
from app.search import WatchFactsSearchWorkflow
from app.search_result import SearchResult, search_results_to_dicts


class SearchWorkflow(Protocol):
    async def search(self, query: str) -> list[SearchResult]:
        ...


async def watchfacts_search_payload(
    query: str,
    *,
    limit: int | None = None,
    include_similar: bool = True,
    include_raw: bool = False,
    settings: Settings | None = None,
    workflow: SearchWorkflow | None = None,
) -> dict[str, object]:
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("query must not be empty")
    if limit is not None and limit <= 0:
        raise ValueError("limit must be a positive integer")

    active_workflow = workflow or WatchFactsSearchWorkflow(
        settings or load_search_settings()
    )
    results = await active_workflow.search(normalized_query)
    visible_results = results[:limit] if limit is not None else results

    return {
        "query": normalized_query,
        "total_count": len(results),
        "result_count": len(visible_results),
        "truncated": len(visible_results) < len(results),
        "results": search_results_to_dicts(
            visible_results,
            include_similar=include_similar,
            include_raw=include_raw,
        ),
    }
