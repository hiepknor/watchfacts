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

    score = score_result(result, original_rank=0, query="5205r")

    assert score.quality_group == 0
    assert score.quality_severity == 0
    assert score.posted_date_group == 0
    assert score.exact_reference_score == 1
    assert score.price_evidence_score == 1
    assert "quality.clean" in score.reasons
    assert "date.parsed" in score.reasons
    assert "reference.selected" in score.reasons
    assert "price.visible" in score.reasons


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


def test_rank_results_by_quality_prefers_reference_match_after_quality_and_date() -> None:
    weak = SearchResult("Patek green full set $428000", posted_date="May 17, 2026")
    exact = SearchResult("Patek 5205R green full set $428000", posted_date="May 17, 2026")

    ranked = rank_results_by_quality([weak, exact], query="5205r green")

    assert ranked == [exact, weak]


def test_rank_results_by_quality_prefers_local_descriptor_after_quality_date_reference() -> None:
    weak = SearchResult(
        "Patek 5205R blue full set $428000 green strap",
        posted_date="May 17, 2026",
    )
    local = SearchResult(
        "Patek 5205R green full set $428000",
        posted_date="May 17, 2026",
    )

    ranked = rank_results_by_quality([weak, local], query="5205r green")

    assert ranked == [local, weak]


def test_rank_results_by_quality_prefers_visible_price_after_quality_date_and_relevance() -> None:
    without_price = SearchResult("Patek 5205R green full set", posted_date="May 17, 2026")
    with_price = SearchResult("Patek 5205R green full set $428000", posted_date="May 17, 2026")

    ranked = rank_results_by_quality([without_price, with_price], query="5205r green")

    assert ranked == [with_price, without_price]


def test_rank_results_by_quality_keeps_date_ahead_of_relevance_signals() -> None:
    older_exact = SearchResult(
        "Patek 5205R green full set $428000",
        posted_date="May 16, 2026",
    )
    newer_weak = SearchResult(
        "Patek green full set $428000",
        posted_date="May 17, 2026",
    )

    ranked = rank_results_by_quality([older_exact, newer_weak], query="5205r green")

    assert ranked == [newer_weak, older_exact]


def test_parse_posted_date_accepts_watchfacts_formats() -> None:
    assert parse_posted_date("May 17, 2026") is not None
    assert parse_posted_date("May 17, 2026 · reposted") is not None
    assert parse_posted_date("2026-05-17 10:00:00") is not None
    assert parse_posted_date("not a date") is None
