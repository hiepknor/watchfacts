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


def test_score_result_demotes_reference_only_fragment_as_suspicious() -> None:
    result = SearchResult("Patek ref 5712G")

    score = score_result(result, original_rank=0, query="5712g")

    assert score.quality_group == 2
    assert "quality.suspicious" in score.reasons
    assert "suspicious.reference_only_fragment" in score.reasons


def test_score_result_demotes_reference_descriptor_conflict_with_guardrail_reason() -> None:
    result = SearchResult(
        "Patek 5205R black dial 2026 $428000",
        posted_date="May 17, 2026",
    )

    score = score_result(result, original_rank=0, query="5205r green")

    assert score.quality_group == 1
    assert "guardrail.descriptor_conflict" in score.reasons


def test_score_result_does_not_demote_missing_descriptor_as_conflict() -> None:
    result = SearchResult(
        "Patek 5205R 2026 $428000",
        posted_date="May 17, 2026",
    )

    score = score_result(result, original_rank=0, query="5205r green")

    assert score.quality_group == 0
    assert "guardrail.descriptor_conflict" not in score.reasons


def test_score_result_does_not_treat_karat_gold_as_price_evidence() -> None:
    cases = (
        "5712R Patek original movement customized 18k rose gold case reservation",
        "5712R Patek original movement customized 22k gold case reservation",
        "5712R Patek original movement customized 24k yellow gold case reservation",
    )

    for listing_text in cases:
        score = score_result(
            SearchResult(listing_text, posted_date="May 17, 2026"),
            original_rank=0,
            query="5712r",
        )

        assert score.quality_group == 1
        assert score.price_evidence_score == 0
        assert "price.missing_visible" in score.reasons


def test_score_result_counts_documented_dealer_shorthand_as_price_evidence() -> None:
    cases = (
        "5712R full set 465k",
        "FPJ Elegante Titanium fullset HKD785K",
        "5712R full set $36k",
        "116500 panda 30+lbl",
        "116500 panda 26299 + lab",
        "RM65-01 Lebron USDT 485",
        "5712R full set 110k€",
        "5990/1R 2026 248 € inc shipment",
    )

    for listing_text in cases:
        score = score_result(SearchResult(listing_text), original_rank=0)

        assert score.quality_group == 0
        assert score.price_evidence_score == 1
        assert "price.visible" in score.reasons


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


def test_rank_results_by_quality_demotes_incomplete_scoped_stock_list_segment() -> None:
    incomplete = SearchResult(
        "5712g new 2024",
        raw_listing_text=(
            "HK STOCK LIST 116505 rainbow 284k "
            "5712g new 2024 -> 115k 5726/1A used 2022 68k"
        ),
        posted_date="May 18, 2026",
    )
    clean = SearchResult(
        "5712g new 2024 -> 115k",
        raw_listing_text=(
            "HK STOCK LIST 116505 rainbow 284k "
            "5712g new 2024 -> 115k 5726/1A used 2022 68k"
        ),
        posted_date="May 17, 2026",
    )

    incomplete_score = score_result(incomplete, original_rank=0, query="5712g")
    ranked = rank_results_by_quality([incomplete, clean], query="5712g")

    assert incomplete_score.quality_group == 2
    assert "suspicious.scoped_stock_list_missing_price" in incomplete_score.reasons
    assert ranked == [clean, incomplete]


def test_score_result_marks_parent_only_price_as_ambiguous_neighbor() -> None:
    result = SearchResult(
        "5712g new 2024",
        raw_listing_text=(
            "HK STOCK LIST 116505 rainbow 284k "
            "5712g new 2024 -> 115k 5726/1A used 2022 68k"
        ),
        posted_date="May 18, 2026",
    )

    score = score_result(result, original_rank=0, query="5712g")

    assert score.price_evidence_score == 0
    assert "price.ambiguous_neighbor" in score.reasons


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


def test_rank_results_by_quality_prefers_full_listing_scope_when_other_features_tie() -> None:
    stock_list = SearchResult(
        "5712G new 2024 115k",
        posted_date="May 17, 2026",
        raw_listing_text="HK STOCK LIST 116505 284k 5712G new 2024 115k",
        scope_reason="scope.stock_list",
        image_reason="image.omitted_bundle_ambiguous",
    )
    full_listing = SearchResult(
        "5712G new 2024 115k",
        posted_date="May 17, 2026",
        scope_reason="scope.full_listing",
        image_reason="image.missing_source",
    )

    stock_score = score_result(stock_list, original_rank=0, query="5712g")
    full_score = score_result(full_listing, original_rank=1, query="5712g")
    ranked = rank_results_by_quality([stock_list, full_listing], query="5712g")

    assert stock_score.scope_confidence_score < full_score.scope_confidence_score
    assert "scope.stock_list" in stock_score.reasons
    assert "scope.full_listing" in full_score.reasons
    assert ranked == [full_listing, stock_list]


def test_score_result_marks_unlabeled_multi_reference_parent_as_stock_list_scope() -> None:
    result = SearchResult(
        "5726/1A 2021 full set 115k",
        raw_listing_text=(
            "PP 7130G-016 Paper of 2022 USD31000 "
            "PP7010G-013 2025 full set US$63,000 "
            "5726/1A 2021 full set 115k"
        ),
    )

    score = score_result(result, original_rank=0, query="5726/1a")

    assert score.scope_confidence_score == 0
    assert "scope.stock_list" in score.reasons


def test_rank_results_by_quality_prefers_direct_image_when_other_features_tie() -> None:
    missing_image = SearchResult(
        "5712G new 2024 115k",
        posted_date="May 17, 2026",
        image_reason="image.missing_source",
    )
    direct_image = SearchResult(
        "5712G new 2024 115k",
        posted_date="May 17, 2026",
        image_url="https://watchfacts.example/5712g.jpg",
        image_reason="image.direct",
    )

    missing_score = score_result(missing_image, original_rank=0, query="5712g")
    direct_score = score_result(direct_image, original_rank=1, query="5712g")
    ranked = rank_results_by_quality([missing_image, direct_image], query="5712g")

    assert missing_score.image_confidence_score < direct_score.image_confidence_score
    assert "image.missing_source" in missing_score.reasons
    assert "image.direct" in direct_score.reasons
    assert ranked == [direct_image, missing_image]


def test_score_result_exposes_alias_feature() -> None:
    result = SearchResult(
        "Patek 5712G new 2024 115k",
        posted_date="May 17, 2026",
    )

    score = score_result(result, original_rank=0, query="patek 5712g")

    assert score.alias_confidence_score > 0
    assert "alias.explicit" in score.reasons


def test_score_result_exposes_conflict_penalty_feature() -> None:
    result = SearchResult(
        "Patek 5205R black dial 2026 $428000",
        posted_date="May 17, 2026",
    )

    score = score_result(result, original_rank=0, query="5205r green")

    assert score.conflict_penalty_score == 1
    assert "conflict.descriptor" in score.reasons


def test_rank_results_by_quality_demotes_short_model_suffix_phrase_miss() -> None:
    broad_stock_list = SearchResult(
        "1,163,000 145.032 Zeitwerk, Used Full set | HKD 821,000 Lange Zeitwerk",
        posted_date="May 28, 2026",
        raw_listing_text=(
            "Rolex and others 336938 green Jub 540000 hkd "
            "1,163,000 145.032 Zeitwerk, Used Full set | HKD 821,000 Lange Zeitwerk"
        ),
    )
    clear_lange_1 = SearchResult(
        "A. Lange & Söhne LANGE 1 Series 139.032 watch only 28900usd",
        posted_date="May 14, 2026",
    )

    broad_score = score_result(broad_stock_list, original_rank=0, query="Lange 1")
    ranked = rank_results_by_quality([broad_stock_list, clear_lange_1], query="Lange 1")

    assert broad_score.quality_group == 1
    assert "guardrail.brand_model_phrase_missing" in broad_score.reasons
    assert ranked == [clear_lange_1, broad_stock_list]


def test_score_result_detects_split_thousands_price_evidence() -> None:
    result = SearchResult(
        "PP 7118/1200A grey N1/2026 790 000HKD",
        posted_date="May 17, 2026",
    )

    score = score_result(result, original_rank=0, query="7118/1200a grey")

    assert score.price_evidence_score == 1
    assert "price.visible" in score.reasons


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
