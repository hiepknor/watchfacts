from __future__ import annotations

from app.result_scoring import (
    parse_posted_date,
    rank_results_by_quality,
    score_result,
)
from app.telegram_bot import SearchResult


def test_score_result_marks_clean_result_and_parses_date() -> None:
    result = SearchResult(
        "5205R 2026-04 $428000",
        posted_date="May 17, 2026",
    )

    score = score_result(result, original_rank=0)

    assert score.quality_group == 0
    assert score.quality_severity == 0
    assert score.posted_date_group == 0
    assert "quality.clean" in score.reasons
    assert "date.parsed" in score.reasons


def test_score_result_demotes_missing_price_evidence() -> None:
    result = SearchResult("5205r 2026")

    score = score_result(result, original_rank=0)

    assert score.quality_group == 1
    assert "quality.missing_price" in score.reasons
    assert "suspicious.missing_price_evidence" in score.reasons


def test_score_result_demotes_stronger_suspicious_cases_after_missing_price() -> None:
    missing_price = SearchResult("5205r 2026")
    truncated = SearchResult(
        "5712R 2016/ HKD",
        raw_listing_text="5712R 2016/ HKD 450000",
    )

    missing_price_score = score_result(missing_price, original_rank=0)
    truncated_score = score_result(truncated, original_rank=1)

    assert missing_price_score.quality_group == 1
    assert truncated_score.quality_group == 2
    assert "quality.suspicious" in truncated_score.reasons


def test_rank_results_by_quality_sorts_date_desc_inside_clean_group() -> None:
    older = SearchResult("5205r 2026/3 $435,000", posted_date="March 16, 2026")
    newer = SearchResult("5205R 2026-04 $428000", posted_date="May 17, 2026")

    ranked = rank_results_by_quality([older, newer])

    assert ranked == [newer, older]


def test_rank_results_by_quality_keeps_missing_price_after_clean_even_when_newer() -> None:
    missing_price_newer = SearchResult("5205r 2026", posted_date="May 18, 2026")
    priced_older = SearchResult("5205r 2026/3 $435,000", posted_date="March 16, 2026")

    ranked = rank_results_by_quality([missing_price_newer, priced_older])

    assert ranked == [priced_older, missing_price_newer]


def test_rank_results_by_quality_preserves_original_order_when_scores_tie() -> None:
    first = SearchResult("5205R 2026-04 $428000", posted_date="May 17, 2026")
    second = SearchResult("5205R 2026-04 $429000", posted_date="May 17, 2026")

    ranked = rank_results_by_quality([first, second])

    assert ranked == [first, second]


def test_parse_posted_date_accepts_watchfacts_formats() -> None:
    assert parse_posted_date("May 17, 2026") is not None
    assert parse_posted_date("May 17, 2026 · reposted") is not None
    assert parse_posted_date("2026-05-17 10:00:00") is not None
    assert parse_posted_date("not a date") is None
