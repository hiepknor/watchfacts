from __future__ import annotations

import json

from scripts.diagnostics.benchmark_mcp_queries import (
    BenchmarkRow,
    _dedupe_queries,
    _load_query_file,
    _row_from_payload,
    render_jsonl,
    render_markdown,
    render_text,
    summarize_rows,
)


def test_row_from_payload_extracts_latency_quality_and_diagnostics() -> None:
    payload = {
        "query": "5205r green",
        "total_count": 26,
        "offset": 0,
        "limit": 3,
        "result_count": 2,
        "has_more": True,
        "next_offset": 2,
        "search_diagnostics": {
            "query_intent": "reference_with_descriptor",
            "cache_hit": False,
            "server_filtered": True,
            "parsed_count": 12,
            "matched_count": 10,
            "weak_match_count": 1,
            "ambiguous_candidate_count": 2,
            "final_count": 2,
            "stage_timings_ms": {
                "cache_read": 1,
                "watchfacts_fetch": 5100,
                "parse": 40,
                "match": 15,
                "result_pipeline": 30,
                "persist": 20,
                "total": 5206,
            },
        },
        "results": [
            {
                "result_id": "watchfacts:a",
                "stable_listing_id": "watchfacts-listing:a",
                "rank": 1,
                "listing_text": "5205R Green New 2/2026 $417,000 HKD",
                "seller": "Mr Dain",
                "posted_date": "June 11, 2026",
                "source_url": "/flash-sales/1",
                "image_url": "/images/1.jpg",
            },
            {
                "result_id": "watchfacts:b",
                "stable_listing_id": "watchfacts-listing:b",
                "rank": 2,
                "listing_text": "5205R green 2017 used fullset HK$360k",
                "seller": None,
                "posted_date": None,
                "source_url": None,
                "image_url": None,
            },
        ],
    }

    row = _row_from_payload(
        query="5205r green",
        payload=payload,
        elapsed_ms=123,
        validation_errors=(),
    )

    assert row.ok is True
    assert row.elapsed_ms == 123
    assert row.total_count == 26
    assert row.query_intent == "reference_with_descriptor"
    assert row.cache_hit is False
    assert row.server_filtered is True
    assert row.parsed_count == 12
    assert row.matched_count == 10
    assert row.weak_match_count == 1
    assert row.ambiguous_candidate_count == 2
    assert row.stage_timings_ms == {
        "cache_read": 1,
        "watchfacts_fetch": 5100,
        "parse": 40,
        "match": 15,
        "result_pipeline": 30,
        "persist": 20,
        "total": 5206,
    }
    assert row.image_missing_count == 1
    assert row.source_missing_count == 1
    assert row.warning_count == 5
    assert row.top_results[0] == "5205R Green New 2/2026 $417,000 HKD"


def test_renderers_emit_terminal_markdown_and_jsonl_reports() -> None:
    rows = [
        BenchmarkRow(
            query="5205r green",
            ok=True,
            elapsed_ms=100,
            result_count=3,
            total_count=26,
            query_intent="reference_with_descriptor",
            cache_hit=True,
            stage_timings_ms={
                "cache_read": 1,
                "watchfacts_fetch": 0,
                "total": 80,
            },
            top_results=("5205R Green",),
        ),
        BenchmarkRow(
            query="Panerai Luminor",
            ok=False,
            elapsed_ms=9000,
            error_type="RuntimeError",
            error="WatchFacts HTTP search failed",
        ),
    ]

    text = render_text(rows)
    markdown = render_markdown(rows)
    jsonl = render_jsonl(rows)

    assert "MCP_BENCH query='5205r green' ok=true" in text
    assert "stages=cache_read:1,watchfacts_fetch:0,total:80" in text
    assert "SUMMARY" in text
    assert "| 5205r green | yes | 100 | 26 |" in markdown
    assert "cache_read:1,watchfacts_fetch:0,total:80" in markdown
    decoded = [json.loads(line) for line in jsonl.splitlines()]
    assert decoded[0]["query"] == "5205r green"
    assert decoded[0]["stage_timings_ms"]["total"] == 80
    assert decoded[1]["error_type"] == "RuntimeError"


def test_summarize_rows_uses_successful_queries_only() -> None:
    summary = summarize_rows(
        [
            BenchmarkRow(query="a", ok=True, elapsed_ms=100),
            BenchmarkRow(query="b", ok=True, elapsed_ms=300),
            BenchmarkRow(query="c", ok=False, elapsed_ms=10),
        ]
    )

    assert summary["passed"] == 2
    assert summary["total"] == 3
    assert summary["avg_ms"] == 200
    assert summary["median_ms"] == 200
    assert summary["min_ms"] == 100
    assert summary["max_ms"] == 300


def test_query_helpers_dedupe_and_ignore_comments(tmp_path) -> None:
    query_file = tmp_path / "queries.txt"
    query_file.write_text(
        "\n".join(
            [
                "# default set",
                " 5205r green ",
                "",
                "Panerai Luminor",
            ]
        )
    )

    assert _load_query_file(str(query_file)) == ["5205r green", "Panerai Luminor"]
    assert _dedupe_queries(["5205r green", "  ", "5205R GREEN", "Lange 1"]) == [
        "5205r green",
        "Lange 1",
    ]
