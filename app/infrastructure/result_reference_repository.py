from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.db import Database
from app.searching.search_result import SearchResult


@dataclass
class ResultReferenceRepository:
    database: Any

    @classmethod
    def from_settings(cls, settings: Settings) -> "ResultReferenceRepository":
        return cls(Database(settings.db_path))

    def record_results(
        self,
        *,
        cache_key: str,
        query_text: str,
        results: Iterable[SearchResult],
        ttl_seconds: int,
    ) -> None:
        self.database.record_search_result_references(
            cache_key=cache_key,
            query_text=query_text,
            results=results,
            ttl_seconds=ttl_seconds,
        )

    def get_by_result_id(
        self,
        *,
        cache_key: str,
        result_id: str,
    ) -> tuple[int, SearchResult] | None:
        return self.database.get_fresh_search_result_reference_by_id(
            cache_key=cache_key,
            result_id=result_id,
        )

    def get_by_stable_listing_id(
        self,
        *,
        cache_key: str,
        stable_listing_id: str,
    ) -> tuple[int, SearchResult] | None:
        return self.database.get_fresh_search_result_reference_by_stable_listing_id(
            cache_key=cache_key,
            stable_listing_id=stable_listing_id,
        )

    def get_by_rank(
        self,
        *,
        cache_key: str,
        result_rank: int,
    ) -> tuple[str, SearchResult] | None:
        return self.database.get_fresh_search_result_reference_by_rank(
            cache_key=cache_key,
            result_rank=result_rank,
        )
