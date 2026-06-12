from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.db import Database


@dataclass
class AiSuggestionRepository:
    database: Any

    @classmethod
    def from_settings(cls, settings: Settings) -> "AiSuggestionRepository":
        return cls(Database(settings.db_path))

    @classmethod
    def from_database(cls, database: Any) -> "AiSuggestionRepository":
        return cls(database)

    def record_suggestion(
        self,
        *,
        query_text: str,
        result_rank: int,
        mode: str,
        model: str,
        deterministic_text: str,
        suggested_text: str,
        raw_listing_text: str | None = None,
        source_url: str | None = None,
        gate_status: str,
        gate_reasons: Iterable[str],
        latency_ms: int | None = None,
        prompt_version: str = "watchfacts-refine-v1",
    ) -> int:
        return self.database.record_ai_refinement_suggestion(
            query_text=query_text,
            result_rank=result_rank,
            mode=mode,
            model=model,
            deterministic_text=deterministic_text,
            suggested_text=suggested_text,
            raw_listing_text=raw_listing_text,
            source_url=source_url,
            gate_status=gate_status,
            gate_reasons=gate_reasons,
            latency_ms=latency_ms,
            prompt_version=prompt_version,
        )

    def list_suggestions(
        self,
        *,
        limit: int = 20,
        review_status: str | None = None,
    ) -> list[Any]:
        return self.database.list_ai_refinement_suggestions(
            limit=limit,
            review_status=review_status,
        )

    def get_suggestion(self, suggestion_id: int) -> Any:
        return self.database.get_ai_refinement_suggestion(suggestion_id)

    def mark_status(
        self,
        suggestion_id: int,
        *,
        status: str,
        notes: str | None = None,
    ) -> Any:
        return self.database.mark_ai_refinement_suggestion_status(
            suggestion_id,
            status=status,
            notes=notes,
        )

    def export_reviewed(
        self,
        *,
        status: str = "accepted",
        limit: int = 50,
    ) -> list[dict[str, object]]:
        return self.database.export_reviewed_ai_suggestions(
            status=status,
            limit=limit,
        )
