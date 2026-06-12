from __future__ import annotations

import time
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from typing import Protocol

from app.searching.search_result import SearchResult, source_result_id, stable_listing_id


@dataclass(frozen=True)
class StoredResult:
    query: str
    rank: int
    result: SearchResult
    stored_at: float


class SearchWorkflow(Protocol):
    async def search(self, query: str) -> list[SearchResult]:
        ...


class ResultReferenceRepository(Protocol):
    def get_by_result_id(
        self,
        *,
        cache_key: str,
        result_id: str,
    ) -> tuple[int, SearchResult] | None:
        ...

    def get_by_stable_listing_id(
        self,
        *,
        cache_key: str,
        stable_listing_id: str,
    ) -> tuple[int, SearchResult] | None:
        ...

    def get_by_rank(
        self,
        *,
        cache_key: str,
        result_rank: int,
    ) -> tuple[str, SearchResult] | None:
        ...


WorkflowFactory = Callable[[], SearchWorkflow]
StoreResults = Callable[[str, list[SearchResult], int], None]
ResultIdFactory = Callable[[str, int, SearchResult], str]
StableIdFactory = Callable[[SearchResult], str]


@dataclass
class ResultReferenceUseCase:
    cache: MutableMapping[str, StoredResult]
    repository: ResultReferenceRepository
    cache_key: str
    cache_ttl_seconds: int
    result_id_factory: ResultIdFactory = source_result_id
    stable_id_factory: StableIdFactory = stable_listing_id
    monotonic: Callable[[], float] = time.monotonic

    async def resolve_result_reference(
        self,
        query: str,
        *,
        result_id: str | None,
        rank: int | None,
        workflow: SearchWorkflow | None,
        workflow_factory: WorkflowFactory,
        store_results: StoreResults,
    ) -> StoredResult:
        if result_id is not None:
            return await self.resolve_result(
                query,
                result_id,
                workflow=workflow,
                workflow_factory=workflow_factory,
                store_results=store_results,
            )
        if rank is None:
            raise ValueError("result_id or rank is required")
        return await self.resolve_result_by_rank(
            query,
            rank,
            workflow=workflow,
            workflow_factory=workflow_factory,
            store_results=store_results,
        )

    async def resolve_result(
        self,
        query: str,
        result_id: str,
        *,
        workflow: SearchWorkflow | None,
        workflow_factory: WorkflowFactory,
        store_results: StoreResults,
    ) -> StoredResult:
        now = self.monotonic()
        self.prune(now)
        stored = self.cache.get(result_id)
        if stored is not None and _query_key(stored.query) == _query_key(query):
            return stored

        cached = self.repository.get_by_result_id(
            cache_key=self.cache_key,
            result_id=result_id,
        )
        if cached is not None:
            rank, result = cached
            stored = StoredResult(
                query=query,
                rank=rank,
                result=result,
                stored_at=now,
            )
            self.cache[result_id] = stored
            return stored

        cached = self.repository.get_by_stable_listing_id(
            cache_key=self.cache_key,
            stable_listing_id=result_id,
        )
        if cached is not None:
            rank, result = cached
            source_id = self.result_id_factory(query, rank, result)
            stored = StoredResult(
                query=query,
                rank=rank,
                result=result,
                stored_at=now,
            )
            self.cache[source_id] = stored
            self.cache[self.stable_id_factory(result)] = stored
            return stored

        await self.refresh_results(
            query,
            workflow=workflow,
            workflow_factory=workflow_factory,
            store_results=store_results,
        )
        stored = self.cache.get(result_id)
        if stored is None:
            raise ValueError("result_id was not found for query; run search again")
        return stored

    async def resolve_result_by_rank(
        self,
        query: str,
        rank: int,
        *,
        workflow: SearchWorkflow | None,
        workflow_factory: WorkflowFactory,
        store_results: StoreResults,
    ) -> StoredResult:
        now = self.monotonic()
        self.prune(now)
        stored = self.lookup_stored_result_by_rank(query, rank)
        if stored is not None:
            return stored

        await self.refresh_results(
            query,
            workflow=workflow,
            workflow_factory=workflow_factory,
            store_results=store_results,
        )
        stored = self.lookup_stored_result_by_rank(query, rank)
        if stored is None:
            raise ValueError("rank was not found for query; run search again")
        return stored

    def lookup_stored_result_by_rank(
        self,
        query: str,
        rank: int,
    ) -> StoredResult | None:
        query_key = _query_key(query)
        latest: StoredResult | None = None
        for stored in self.cache.values():
            if stored.rank == rank and _query_key(stored.query) == query_key:
                if latest is None or stored.stored_at >= latest.stored_at:
                    latest = stored
        if latest is not None:
            return latest

        cached = self.repository.get_by_rank(
            cache_key=self.cache_key,
            result_rank=rank,
        )
        if cached is None:
            return None
        result_id, result = cached
        stored = StoredResult(
            query=query,
            rank=rank,
            result=result,
            stored_at=self.monotonic(),
        )
        self.cache[result_id] = stored
        return stored

    async def refresh_results(
        self,
        query: str,
        *,
        workflow: SearchWorkflow | None,
        workflow_factory: WorkflowFactory,
        store_results: StoreResults,
    ) -> None:
        active_workflow = workflow or workflow_factory()
        results = await active_workflow.search(query)
        store_results(query, results, self.cache_ttl_seconds)

    def prune(self, now: float) -> None:
        expired = [
            result_id
            for result_id, stored in self.cache.items()
            if now - stored.stored_at > self.cache_ttl_seconds
        ]
        for result_id in expired:
            self.cache.pop(result_id, None)


def _query_key(value: str) -> str:
    return " ".join(value.casefold().split())
