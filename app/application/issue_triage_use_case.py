from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.infrastructure import IssueRepository


@dataclass
class IssueTriageUseCase:
    repository: Any

    @classmethod
    def from_settings(cls, settings: Settings) -> "IssueTriageUseCase":
        return cls(IssueRepository.from_settings(settings))

    @classmethod
    def from_database(cls, database: Any) -> "IssueTriageUseCase":
        return cls(IssueRepository(database))

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
    ) -> Any:
        issue_id = self.repository.record_feedback(
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
        return self.repository.get_issue(issue_id, issue_type="feedback")

    def list_issues(
        self,
        *,
        issue_type: str,
        status: str,
        limit: int,
        min_severity: int | None = None,
    ) -> list[Any]:
        if issue_type == "feedback":
            return self.repository.list_feedback(status=status, limit=limit)
        if issue_type == "suspicious":
            return self.repository.list_suspicious(
                status=status,
                limit=limit,
                min_severity=min_severity,
            )
        return (
            self.repository.list_feedback(status=status, limit=limit)
            + self.repository.list_suspicious(
                status=status,
                limit=limit,
                min_severity=min_severity,
            )
        )[:limit]

    def get_issue(self, issue_id: int, *, issue_type: str | None) -> Any:
        return self.repository.get_issue(issue_id, issue_type=issue_type)

    def update_issue(
        self,
        issue_id: int,
        *,
        issue_type: str | None,
        status: str,
        notes: str | None = None,
    ) -> Any:
        return self.repository.mark_status(
            issue_id,
            issue_type=issue_type,
            status=status,
            notes=notes,
        )

    def summarize_suspicious(self, *, limit: int) -> list[Any]:
        return self.repository.summarize_suspicious(limit=limit)
