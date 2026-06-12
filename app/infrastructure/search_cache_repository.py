from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.db import Database


@dataclass
class SearchCacheRepository:
    database: Any

    @classmethod
    def from_settings(cls, settings: Settings) -> "SearchCacheRepository":
        return cls(Database(settings.db_path))

    def record_query_results(
        self,
        query_text: str,
        listings: Iterable[Any],
        *,
        image_missing_count: int = 0,
        server_filtered_hit_count: int = 0,
        playwright_fallback_count: int = 0,
    ) -> Any:
        return self.database.record_query_results(
            query_text,
            listings,
            image_missing_count=image_missing_count,
            server_filtered_hit_count=server_filtered_hit_count,
            playwright_fallback_count=playwright_fallback_count,
        )

    def get_quality_metrics(self, cache_key: str) -> dict[str, int]:
        return self.database.get_search_cache_quality_metrics(cache_key)

    def get_fresh_row(self, cache_key: str) -> tuple[str, int, int, int] | None:
        return self.database.get_fresh_search_cache_row(cache_key)

    def record_cache(
        self,
        *,
        cache_key: str,
        query_text: str,
        result_json: str,
        result_count: int,
        image_missing_count: int,
        server_filtered_hit_count: int,
        playwright_fallback_count: int,
        ttl_seconds: int,
    ) -> None:
        self.database.record_search_cache(
            cache_key=cache_key,
            query_text=query_text,
            result_json=result_json,
            result_count=result_count,
            image_missing_count=image_missing_count,
            server_filtered_hit_count=server_filtered_hit_count,
            playwright_fallback_count=playwright_fallback_count,
            ttl_seconds=ttl_seconds,
        )
