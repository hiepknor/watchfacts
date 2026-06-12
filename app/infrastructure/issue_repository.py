from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.db import Database


@dataclass
class IssueRepository:
    database: Any

    @classmethod
    def from_settings(cls, settings: Settings) -> "IssueRepository":
        return cls(Database(settings.db_path))

    def record_feedback(
        self,
        *,
        query_text: str,
        result_rank: int,
        reason: str,
        listing_text: str,
        raw_listing_text: str | None,
        seller: str | None,
        posted_date: str | None,
        source_url: str | None,
        notes: str | None,
        telegram_user_id: int | None = None,
    ) -> int:
        return self.database.record_result_feedback(
            query_text=query_text,
            result_rank=result_rank,
            reason=reason,
            listing_text=listing_text,
            raw_listing_text=raw_listing_text,
            seller=seller,
            posted_date=posted_date,
            source_url=source_url,
            notes=notes,
            telegram_user_id=telegram_user_id,
        )

    def get_issue(self, issue_id: int, *, issue_type: str | None) -> Any:
        return self.database.get_issue(issue_id, issue_type=issue_type)

    def list_feedback(self, *, status: str, limit: int) -> list[Any]:
        return self.database.list_feedback_issues(status=status, limit=limit)

    def list_suspicious(
        self,
        *,
        status: str,
        limit: int,
        min_severity: int | None = None,
    ) -> list[Any]:
        return self.database.list_suspicious_issues(
            status=status,
            limit=limit,
            min_severity=min_severity,
        )

    def mark_status(
        self,
        issue_id: int,
        *,
        issue_type: str | None,
        status: str,
        notes: str | None = None,
    ) -> Any:
        return self.database.mark_issue_status(
            issue_id,
            issue_type=issue_type,
            status=status,
            notes=notes,
        )

    def summarize_suspicious(self, *, limit: int) -> list[Any]:
        return self.database.summarize_open_suspicious_issues(limit=limit)

    def record_suspicious(
        self,
        *,
        query_text: str,
        result_rank: int,
        reason: str,
        severity: int,
        listing_text: str,
        raw_listing_text: str | None = None,
        source_url: str | None = None,
    ) -> None:
        self.database.record_suspicious_result(
            query_text=query_text,
            result_rank=result_rank,
            reason=reason,
            severity=severity,
            listing_text=listing_text,
            raw_listing_text=raw_listing_text,
            source_url=source_url,
        )
