from __future__ import annotations

from app.search_contracts import validate_search_diagnostics
from scripts.diagnostics.mcp_smoke import validate_search_payload


def test_validate_search_payload_accepts_required_search_shape() -> None:
    payload = {
        "query": "5712g",
        "total_count": 1,
        "offset": 0,
        "limit": 1,
        "result_count": 1,
        "has_more": False,
        "next_offset": None,
        "results": [
            {
                "result_id": "watchfacts:abc",
                "stable_listing_id": "watchfacts-listing:def",
                "rank": 1,
                "listing_text": "5712G Used 2015 76k usdt",
                "seller": "Issac",
                "posted_date": "June 2, 2026",
                "source_url": "/flash-sales/1",
                "image_url": "/image.jpg",
            }
        ],
    }

    assert validate_search_payload(payload) == []


def test_validate_search_payload_reports_missing_fields() -> None:
    payload = {
        "query": "5712g",
        "total_count": 1,
        "offset": 0,
        "limit": 1,
        "result_count": 1,
        "has_more": False,
        "next_offset": None,
        "results": [{"rank": 1, "listing_text": "5712G"}],
    }

    errors = validate_search_payload(payload)

    assert "results[0].result_id is required" in errors
    assert "results[0].stable_listing_id is required" in errors
    assert "results[0].seller is required" in errors
    assert "results[0].posted_date is required" in errors
    assert "results[0].source_url is required" in errors
    assert "results[0].image_url is required" in errors


def test_validate_search_payload_allows_nullable_optional_result_fields() -> None:
    payload = {
        "query": "5712g",
        "total_count": 1,
        "offset": 0,
        "limit": 1,
        "result_count": 1,
        "has_more": False,
        "next_offset": None,
        "results": [
            {
                "result_id": "watchfacts:abc",
                "stable_listing_id": "watchfacts-listing:def",
                "rank": 1,
                "listing_text": "5712G Used 2015 76k usdt",
                "seller": None,
                "posted_date": None,
                "source_url": None,
                "image_url": None,
            }
        ],
    }

    assert validate_search_payload(payload) == []


def test_validate_search_payload_reports_invalid_types_and_pagination() -> None:
    payload = {
        "query": " ",
        "total_count": True,
        "offset": -1,
        "limit": 0,
        "result_count": 2,
        "has_more": False,
        "next_offset": 2,
        "results": [
            {
                "result_id": "",
                "stable_listing_id": "",
                "rank": 0,
                "listing_text": "",
                "seller": 123,
                "posted_date": [],
                "source_url": {},
                "image_url": 456,
            }
        ],
    }

    errors = validate_search_payload(payload)

    assert "query must be a non-empty string" in errors
    assert "total_count must be a non-negative integer" in errors
    assert "offset must be a non-negative integer" in errors
    assert "limit must be a positive integer or null" in errors
    assert "result_count must equal the number of results" in errors
    assert "next_offset must be null when has_more is false" in errors
    assert "results[0].result_id must be a non-empty string" in errors
    assert "results[0].stable_listing_id must be a non-empty string" in errors
    assert "results[0].rank must be a positive integer" in errors
    assert "results[0].listing_text must be a non-empty string" in errors
    assert "results[0].seller must be a string or null" in errors
    assert "results[0].posted_date must be a string or null" in errors
    assert "results[0].source_url must be a string or null" in errors
    assert "results[0].image_url must be a string or null" in errors


def test_validate_search_payload_reports_duplicate_result_ids_and_bad_source_url() -> None:
    payload = {
        "query": "5712g",
        "total_count": 2,
        "offset": 0,
        "limit": 2,
        "result_count": 2,
        "has_more": False,
        "next_offset": None,
        "results": [
            {
                "result_id": "watchfacts:abc",
                "stable_listing_id": "watchfacts-listing:def",
                "rank": 1,
                "listing_text": "5712G Used 2015 76k usdt",
                "seller": None,
                "posted_date": None,
                "source_url": "javascript:alert(1)",
                "image_url": None,
            },
            {
                "result_id": "watchfacts:abc",
                "stable_listing_id": "watchfacts-listing:ghi",
                "rank": 2,
                "listing_text": "5712G Used 2016 80k usdt",
                "seller": None,
                "posted_date": None,
                "source_url": "/flash-sales/1",
                "image_url": None,
            },
        ],
    }

    errors = validate_search_payload(payload)

    assert (
        "results[0].source_url must be an http(s), relative, or root-relative URL"
        in errors
    )
    assert "results[1].result_id must be unique" in errors


def test_validate_search_diagnostics_reports_bad_counts() -> None:
    errors = validate_search_diagnostics(
        {
            "cache_hit": False,
            "matched_count": 1,
            "final_count": 2,
            "deduped_drop_count": -1,
            "retrieval_query_count": -1,
            "retrieval_queries": "rm07-01",
            "retrieval_reason_codes": [""],
        }
    )

    assert (
        "search_diagnostics.deduped_drop_count must be a non-negative integer"
        in errors
    )
    assert (
        "search_diagnostics.retrieval_query_count must be a non-negative integer"
        in errors
    )
    assert "search_diagnostics.retrieval_queries must be a list" in errors
    assert (
        "search_diagnostics.retrieval_reason_codes[0] must be a non-empty string"
        in errors
    )
    assert "search_diagnostics.final_count must not exceed matched_count" in errors


def test_validate_search_diagnostics_reports_bad_retrieval_timing_shape() -> None:
    errors = validate_search_diagnostics(
        {
            "cache_hit": False,
            "final_count": 0,
            "retrieval_timings": [
                {
                    "query": "",
                    "cache_status": "warm",
                    "fetch_ms": -1,
                    "parse_ms": True,
                    "match_ms": 0,
                    "total_ms": 0,
                    "parsed_count": -1,
                    "matched_count": 0,
                    "empty": "no",
                    "server_filtered": False,
                    "playwright_fallback": False,
                    "dominant": False,
                    "reason_codes": [""],
                }
            ],
        }
    )

    assert "search_diagnostics.retrieval_timings[0].query must be a non-empty string" in errors
    assert (
        "search_diagnostics.retrieval_timings[0].cache_status must be one of: hit, miss"
        in errors
    )
    assert (
        "search_diagnostics.retrieval_timings[0].fetch_ms must be a non-negative integer"
        in errors
    )
    assert (
        "search_diagnostics.retrieval_timings[0].parse_ms must be a non-negative integer"
        in errors
    )
    assert (
        "search_diagnostics.retrieval_timings[0].parsed_count must be a non-negative integer"
        in errors
    )
    assert "search_diagnostics.retrieval_timings[0].empty must be a boolean" in errors
    assert (
        "search_diagnostics.retrieval_timings[0].reason_codes[0] must be a non-empty string"
        in errors
    )


def test_validate_search_diagnostics_reports_bad_query_plan_shape() -> None:
    errors = validate_search_diagnostics(
        {
            "cache_hit": False,
            "final_count": 0,
            "query_plan": {
                "original_query": "daytona panda",
                "canonical_query": "",
                "brand_candidates": [{"brand": "rolex", "source_terms": "rolex"}],
                "references": ["126500ln"],
                "collections": "daytona",
                "nicknames": [],
                "required_descriptors": [],
                "optional_descriptors": [],
                "conflict_descriptors": [],
                "intent_kind": "brand_model_descriptor",
                "reason_codes": [],
            },
        }
    )

    assert "search_diagnostics.query_plan.canonical_query must be a non-empty string" in errors
    assert "search_diagnostics.query_plan.collections must be a list" in errors
    assert (
        "search_diagnostics.query_plan.brand_candidates[0].confidence is required"
        in errors
    )
    assert (
        "search_diagnostics.query_plan.brand_candidates[0].source_terms must be a list"
        in errors
    )
    assert "search_diagnostics.query_plan.references[0] must be a list" in errors


def test_validate_search_payload_requires_next_offset_when_has_more() -> None:
    payload = {
        "query": "5712g",
        "total_count": 2,
        "offset": 0,
        "limit": 1,
        "result_count": 1,
        "has_more": True,
        "next_offset": None,
        "results": [
            {
                "result_id": "watchfacts:abc",
                "stable_listing_id": "watchfacts-listing:def",
                "rank": 1,
                "listing_text": "5712G Used 2015 76k usdt",
                "seller": None,
                "posted_date": None,
                "source_url": None,
                "image_url": None,
            }
        ],
    }

    assert validate_search_payload(payload) == [
        "next_offset must be a non-negative integer"
    ]


def test_validate_search_payload_can_allow_empty_results() -> None:
    payload = {
        "query": "rare-query",
        "total_count": 0,
        "offset": 0,
        "limit": 1,
        "result_count": 0,
        "has_more": False,
        "next_offset": None,
        "results": [],
    }

    assert validate_search_payload(payload, allow_empty=True) == []
    assert validate_search_payload(payload, allow_empty=False) == [
        "results must contain at least one result"
    ]
