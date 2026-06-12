from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.db import Database, IssueRecord, SuspiciousIssueSummary


@dataclass
class IssueTriageUseCase:
    database: Any

    @classmethod
    def from_settings(cls, settings: Settings) -> "IssueTriageUseCase":
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
    ) -> IssueRecord | None:
        issue_id = self.database.record_result_feedback(
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
        return self.database.get_issue(issue_id, issue_type="feedback")

    def list_issues(
        self,
        *,
        issue_type: str,
        status: str,
        limit: int,
        min_severity: int | None = None,
    ) -> list[IssueRecord]:
        if issue_type == "feedback":
            return self.database.list_feedback_issues(status=status, limit=limit)
        if issue_type == "suspicious":
            return self.database.list_suspicious_issues(
                status=status,
                limit=limit,
                min_severity=min_severity,
            )
        return (
            self.database.list_feedback_issues(status=status, limit=limit)
            + self.database.list_suspicious_issues(
                status=status,
                limit=limit,
                min_severity=min_severity,
            )
        )[:limit]

    def get_issue(self, issue_id: int, *, issue_type: str | None) -> IssueRecord | None:
        return self.database.get_issue(issue_id, issue_type=issue_type)

    def update_issue(
        self,
        issue_id: int,
        *,
        issue_type: str | None,
        status: str,
        notes: str | None = None,
    ) -> IssueRecord | None:
        return self.database.mark_issue_status(
            issue_id,
            issue_type=issue_type,
            status=status,
            notes=notes,
        )

    def summarize_suspicious(self, *, limit: int) -> list[SuspiciousIssueSummary]:
        return self.database.summarize_open_suspicious_issues(limit=limit)
