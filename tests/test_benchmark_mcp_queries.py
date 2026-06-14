from __future__ import annotations

import asyncio
import json
import sqlite3

import scripts.diagnostics.benchmark_mcp_queries as benchmark_module
from scripts.diagnostics.benchmark_mcp_queries import (
    DEFAULT_ALIAS_TOTAL_DELTA_RATIO,
    BenchmarkRow,
    DEFAULT_BENCHMARK_QUERIES,
    RetrievalTimingRow,
    _clear_search_cache,
    _dedupe_queries,
    _load_query_file,
    alias_recall_passed,
    evaluate_alias_recall,
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
            "query_plan": {
                "original_query": "5205r green",
                "canonical_query": "5205r green",
                "brand_candidates": [
                    {
                        "brand": "patek_philippe",
                        "confidence": "reference",
                        "source_terms": ["5205r"],
                    },
                ],
                "references": [["5205r"]],
                "collections": ["complications"],
                "nicknames": [],
                "required_descriptors": ["green"],
                "optional_descriptors": ["2026"],
                "conflict_descriptors": ["blue"],
                "intent_kind": "reference_with_descriptor",
                "reason_codes": ["brand.reference:patek_philippe"],
            },
            "retrieval_query_count": 2,
            "retrieval_queries": ["5205r green", "5205r"],
            "retrieval_reason_codes": [
                "retrieval.reference_with_descriptors",
                "retrieval.expand_reference",
            ],
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
            "retrieval_timings": [
                {
                    "query": "5205r green",
                    "cache_status": "miss",
                    "fetch_ms": 5100,
                    "parse_ms": 40,
                    "match_ms": 15,
                    "total_ms": 5155,
                    "parsed_count": 12,
                    "matched_count": 10,
                    "empty": False,
                    "server_filtered": True,
                    "playwright_fallback": False,
                    "dominant": True,
                    "reason_codes": ["retrieval.reference_with_descriptors"],
                }
            ],
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
    assert row.run_number == 1
    assert row.total_count == 26
    assert row.query_intent == "reference_with_descriptor"
    assert row.canonical_query == "5205r green"
    assert row.brand_candidates == ("patek_philippe:reference",)
    assert row.references == ("5205r",)
    assert row.collections == ("complications",)
    assert row.required_descriptors == ("green",)
    assert row.optional_descriptors == ("2026",)
    assert row.conflict_descriptors == ("blue",)
    assert row.retrieval_query_count == 2
    assert row.retrieval_queries == ("5205r green", "5205r")
    assert row.retrieval_reason_codes == (
        "retrieval.reference_with_descriptors",
        "retrieval.expand_reference",
    )
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
    assert row.retrieval_timings == (
        RetrievalTimingRow(
            query="5205r green",
            cache_status="miss",
            fetch_ms=5100,
            parse_ms=40,
            match_ms=15,
            total_ms=5155,
            parsed_count=12,
            matched_count=10,
            empty=False,
            server_filtered=True,
            playwright_fallback=False,
            dominant=True,
            reason_codes=("retrieval.reference_with_descriptors",),
        ),
    )
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
            run_number=2,
            result_count=3,
            total_count=26,
            query_intent="reference_with_descriptor",
            canonical_query="5205r green",
            brand_candidates=("patek_philippe:reference",),
            references=("5205r",),
            required_descriptors=("green",),
            optional_descriptors=("2026",),
            conflict_descriptors=("blue",),
            retrieval_query_count=2,
            retrieval_queries=("5205r green", "5205r"),
            retrieval_reason_codes=(
                "retrieval.reference_with_descriptors",
                "retrieval.expand_reference",
            ),
            cache_hit=True,
            stage_timings_ms={
                "cache_read": 1,
                "watchfacts_fetch": 0,
                "total": 80,
            },
            retrieval_timings=(
                RetrievalTimingRow(
                    query="5205r green",
                    cache_status="hit",
                    fetch_ms=0,
                    parse_ms=0,
                    match_ms=0,
                    total_ms=0,
                    parsed_count=0,
                    matched_count=0,
                    empty=True,
                    server_filtered=False,
                    playwright_fallback=False,
                    dominant=True,
                    reason_codes=("retrieval.reference_with_descriptors",),
                ),
            ),
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

    assert "MCP_BENCH query='5205r green' run=2 ok=true" in text
    assert "canonical='5205r green'" in text
    assert "brands=patek_philippe:reference" in text
    assert "descriptors=required:green;optional:2026;conflict:blue" in text
    assert "retrieval_queries='5205r green','5205r'" in text
    assert "retrieval_reasons=retrieval.reference_with_descriptors,retrieval.expand_reference" in text
    assert "retrieval_timings='5205r green:total=0,fetch=0,parse=0,match=0,matched=0,cache=hit,dominant=yes'" in text
    assert "stages=cache_read:1,watchfacts_fetch:0,total:80" in text
    assert "SUMMARY" in text
    assert "| 5205r green | 2 | yes | 100 | 26 |" in markdown
    assert "patek_philippe:reference" in markdown
    assert "retrieval.reference_with_descriptors,retrieval.expand_reference" in markdown
    assert "5205r green:total=0,fetch=0,parse=0,match=0,matched=0,cache=hit,dominant=yes" in markdown
    assert "cache hits: 1" in markdown
    assert "cache_read:1,watchfacts_fetch:0,total:80" in markdown
    decoded = [json.loads(line) for line in jsonl.splitlines()]
    assert decoded[0]["query"] == "5205r green"
    assert decoded[0]["run_number"] == 2
    assert decoded[0]["canonical_query"] == "5205r green"
    assert decoded[0]["brand_candidates"] == ["patek_philippe:reference"]
    assert decoded[0]["optional_descriptors"] == ["2026"]
    assert decoded[0]["conflict_descriptors"] == ["blue"]
    assert decoded[0]["retrieval_queries"] == ["5205r green", "5205r"]
    assert decoded[0]["stage_timings_ms"]["total"] == 80
    assert decoded[0]["retrieval_timings"][0]["query"] == "5205r green"
    assert decoded[0]["retrieval_timings"][0]["cache_status"] == "hit"
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
    assert summary["cache_hits"] == 0
    assert summary["cache_misses"] == 0
    assert summary["avg_ms"] == 200
    assert summary["median_ms"] == 200
    assert summary["min_ms"] == 100
    assert summary["max_ms"] == 300


def test_evaluate_alias_recall_compares_canonical_query_groups() -> None:
    checks = evaluate_alias_recall(
        [
            BenchmarkRow(
                query="rm07-01 rg",
                ok=True,
                elapsed_ms=100,
                total_count=29,
                canonical_query="rm07-01 rg",
            ),
            BenchmarkRow(
                query="rm07-01 rose gold",
                ok=True,
                elapsed_ms=120,
                total_count=27,
                canonical_query="rm07-01 rg",
            ),
            BenchmarkRow(
                query="5711 blue",
                ok=True,
                elapsed_ms=80,
                total_count=11,
                canonical_query="5711 blue",
            ),
        ],
        max_delta_ratio=DEFAULT_ALIAS_TOTAL_DELTA_RATIO,
    )

    assert len(checks) == 1
    assert checks[0].ok is True
    assert checks[0].canonical_query == "rm07-01 rg"
    assert checks[0].min_total == 27
    assert checks[0].max_total == 29
    assert checks[0].delta == 2
    assert checks[0].query_totals == (
        "rm07-01 rg:29",
        "rm07-01 rose gold:27",
    )
    assert alias_recall_passed(checks, require_evaluation=True) is True


def test_evaluate_alias_recall_fails_large_total_delta() -> None:
    checks = evaluate_alias_recall(
        [
            BenchmarkRow(
                query="rm07-01 rg",
                ok=True,
                elapsed_ms=100,
                total_count=29,
                canonical_query="rm07-01 rg",
            ),
            BenchmarkRow(
                query="rm07-01 rose gold",
                ok=True,
                elapsed_ms=120,
                total_count=3,
                canonical_query="rm07-01 rg",
            ),
        ],
        max_delta_ratio=DEFAULT_ALIAS_TOTAL_DELTA_RATIO,
    )

    assert len(checks) == 1
    assert checks[0].ok is False
    assert checks[0].delta == 26
    assert checks[0].delta_ratio == 0.897
    assert alias_recall_passed(checks, require_evaluation=True) is False


def test_renderers_emit_alias_recall_comparison() -> None:
    rows = [
        BenchmarkRow(
            query="rm07-01 rg",
            ok=True,
            elapsed_ms=100,
            total_count=29,
            canonical_query="rm07-01 rg",
        ),
        BenchmarkRow(
            query="rm07-01 rose gold",
            ok=True,
            elapsed_ms=120,
            total_count=3,
            canonical_query="rm07-01 rg",
        ),
    ]

    text = render_text(rows, require_alias_recall=True)
    markdown = render_markdown(rows, require_alias_recall=True)

    assert "ALIAS_RECALL canonical='rm07-01 rg' run=1 ok=false" in text
    assert "delta_ratio=0.897" in text
    assert "## Alias Recall" in markdown
    assert "| rm07-01 rg | 1 | no | 3 | 29 | 26 | 0.897 | 0.100 |" in markdown


def test_alias_recall_requires_evaluation_for_default_benchmark_gate() -> None:
    assert alias_recall_passed((), require_evaluation=False) is True
    assert alias_recall_passed((), require_evaluation=True) is False


def test_run_benchmark_repeats_each_deduped_query(monkeypatch) -> None:
    async def fake_benchmark_query(**kwargs) -> BenchmarkRow:
        return BenchmarkRow(
            query=kwargs["query"],
            ok=True,
            elapsed_ms=kwargs["run_number"],
            run_number=kwargs["run_number"],
        )

    monkeypatch.setattr(benchmark_module, "_benchmark_query", fake_benchmark_query)

    rows = asyncio.run(
        benchmark_module.run_benchmark(
            url="http://127.0.0.1:8765/mcp",
            queries=["5205r green", "5205R GREEN", "Lange 1"],
            limit=3,
            timeout_seconds=1,
            include_similar=False,
            allow_empty=True,
            repeat=2,
        )
    )

    assert [(row.query, row.run_number) for row in rows] == [
        ("5205r green", 1),
        ("5205r green", 2),
        ("Lange 1", 1),
        ("Lange 1", 2),
    ]


def test_run_benchmark_can_clear_search_cache_before_each_query(
    monkeypatch,
    tmp_path,
) -> None:
    clear_calls: list[object] = []

    async def fake_benchmark_query(**kwargs) -> BenchmarkRow:
        return BenchmarkRow(
            query=kwargs["query"],
            ok=True,
            elapsed_ms=kwargs["run_number"],
            run_number=kwargs["run_number"],
        )

    def fake_clear_search_cache(db_path) -> int:
        clear_calls.append(db_path)
        return len(clear_calls)

    monkeypatch.setattr(benchmark_module, "_benchmark_query", fake_benchmark_query)
    monkeypatch.setattr(benchmark_module, "_clear_search_cache", fake_clear_search_cache)

    db_path = tmp_path / "bot.db"
    rows = asyncio.run(
        benchmark_module.run_benchmark(
            url="http://127.0.0.1:8765/mcp",
            queries=["5205r green", "Lange 1"],
            limit=3,
            timeout_seconds=1,
            include_similar=False,
            allow_empty=True,
            repeat=2,
            clear_search_cache=True,
            db_path=db_path,
        )
    )

    assert [(row.query, row.run_number) for row in rows] == [
        ("5205r green", 1),
        ("5205r green", 2),
        ("Lange 1", 1),
        ("Lange 1", 2),
    ]
    assert clear_calls == [db_path, db_path, db_path, db_path]


def test_clear_search_cache_removes_search_and_reference_cache_rows(tmp_path) -> None:
    db_path = tmp_path / "bot.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE search_cache (cache_key TEXT PRIMARY KEY)")
        connection.execute(
            "CREATE TABLE result_reference_cache (search_cache_key TEXT, result_id TEXT)"
        )
        connection.execute("INSERT INTO search_cache (cache_key) VALUES ('a')")
        connection.execute(
            "INSERT INTO result_reference_cache (search_cache_key, result_id) VALUES ('a', 'r')"
        )

    assert _clear_search_cache(db_path) == 2

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0] == 0
        assert (
            connection.execute("SELECT COUNT(*) FROM result_reference_cache").fetchone()[0]
            == 0
        )


def test_clear_search_cache_ignores_missing_database(tmp_path) -> None:
    db_path = tmp_path / "missing.db"

    assert _clear_search_cache(db_path) == 0
    assert not db_path.exists()


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


def test_default_benchmark_queries_cover_alias_pairs_and_multi_brand_set() -> None:
    assert DEFAULT_BENCHMARK_QUERIES == (
        "rm07-01 rg",
        "rm07-01 rosegold",
        "rm07-01 rose gold",
        "rm07-01 wg",
        "rm07-01 white gold",
        "rm07-01 mop",
        "rm07-01 mother of pearl",
        "rm07-01 rg snow",
        "rm07-01 rose gold snow",
        "126500ln white",
        "daytona panda",
        "5711 blue",
        "15500st blue",
    )
