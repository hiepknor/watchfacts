from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.issues import detect_suspicious_result
from app.telegram_bot import SearchResult


@dataclass(frozen=True)
class ResultScore:
    quality_group: int
    quality_severity: int
    posted_date_group: int
    posted_date_timestamp: float
    exact_reference_score: int
    descriptor_score: int
    price_evidence_score: int
    original_rank: int
    reasons: tuple[str, ...]

    def sort_key(self) -> tuple[int, int, int, float, int, int, int, int]:
        return (
            self.quality_group,
            self.quality_severity,
            self.posted_date_group,
            -self.posted_date_timestamp,
            -self.exact_reference_score,
            -self.descriptor_score,
            -self.price_evidence_score,
            self.original_rank,
        )


def rank_results_by_quality(results: list[SearchResult]) -> list[SearchResult]:
    if len(results) < 2:
        return results

    scored = [
        (score_result(result, original_rank=index), result)
        for index, result in enumerate(results)
    ]
    first_key = scored[0][0].sort_key()
    if all(score.sort_key() == first_key for score, _ in scored):
        return results
    return [
        result
        for score, result in sorted(scored, key=lambda item: item[0].sort_key())
    ]


def score_result(result: SearchResult, *, original_rank: int) -> ResultScore:
    quality_group, quality_severity, quality_reasons = _quality_score(result)
    posted_date_group, posted_date_timestamp, date_reason = _posted_date_score(
        result.posted_date
    )
    reasons = (*quality_reasons, date_reason)
    return ResultScore(
        quality_group=quality_group,
        quality_severity=quality_severity,
        posted_date_group=posted_date_group,
        posted_date_timestamp=posted_date_timestamp,
        exact_reference_score=0,
        descriptor_score=0,
        price_evidence_score=0,
        original_rank=original_rank,
        reasons=tuple(reason for reason in reasons if reason),
    )


def _quality_score(result: SearchResult) -> tuple[int, int, tuple[str, ...]]:
    issues = detect_suspicious_result(
        listing_text=result.listing_text,
        raw_listing_text=result.raw_listing_text,
    )
    if not issues:
        return 0, 0, ("quality.clean",)

    severity = max(issue.severity for issue in issues)
    issue_reasons = tuple(f"suspicious.{issue.reason}" for issue in issues)
    if all(issue.reason == "missing_price_evidence" for issue in issues):
        return 1, severity, ("quality.missing_price", *issue_reasons)
    return 2, severity, ("quality.suspicious", *issue_reasons)


def _posted_date_score(value: str | None) -> tuple[int, float, str]:
    parsed = parse_posted_date(value)
    if parsed is None:
        return 1, 0.0, "date.missing_or_unparseable"
    return 0, parsed.timestamp(), "date.parsed"


def parse_posted_date(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.split("·", maxsplit=1)[0].strip()
    for date_format in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(normalized[:19], date_format)
        except ValueError:
            continue
    return None
