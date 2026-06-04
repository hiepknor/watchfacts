from __future__ import annotations

from argparse import Namespace
import asyncio
import json

from app.config import load_search_settings
from app.search_result import SearchResult
from scripts.diagnostics import audit_quality
from scripts.diagnostics.audit_quality import (
    DEFAULT_AUDIT_QUERIES,
    build_query_report,
    format_json_report,
    format_text_report,
    load_queries,
)


def test_load_queries_uses_default_query_set_when_no_input() -> None:
    args = Namespace(queries=[], queries_file=None)

    assert load_queries(args) == list(DEFAULT_AUDIT_QUERIES)


def test_load_queries_dedupes_and_normalizes_cli_queries() -> None:
    args = Namespace(
        queries=["  5712r  ", "5712R", "5205r   green"],
        queries_file=None,
    )

    assert load_queries(args) == ["5712r", "5205r green"]


def test_run_audit_uses_search_settings_without_telegram_token(
    tmp_path,
    monkeypatch,
) -> None:
    seen_settings = []

    class FakeWorkflow:
        def __init__(self, settings, *, database, refine_results=None) -> None:
            seen_settings.append(settings)

        async def search(self, query: str) -> list[SearchResult]:
            return [SearchResult(f"{query} HKD 100000")]

    monkeypatch.setattr(
        audit_quality,
        "load_search_settings",
        lambda: load_search_settings(env={}, project_root=tmp_path),
    )
    monkeypatch.setattr(audit_quality, "WatchFactsSearchWorkflow", FakeWorkflow)

    reports = asyncio.run(audit_quality.run_audit(["5712g"], limit=1))

    assert seen_settings[0].runtime_mode == "search"
    assert seen_settings[0].telegram_bot_token == ""
    assert reports[0].query == "5712g"
    assert reports[0].result_count == 1


def test_format_text_report_includes_bounded_score_summary() -> None:
    result = SearchResult(
        listing_text=" ".join(["5712R"] + ["long"] * 80 + ["HKD", "820000"]),
        posted_date="May 18, 2026",
        seller="seller name",
        source_url="https://example.test/listing/123",
    )
    report = build_query_report("5712r", [result], limit=1, snippet_chars=60)

    output = format_text_report([report])

    assert "=== 5712r count=1 top_qg=[0] ===" in output
    assert "#1 qg=0 sev=0 date='May 18, 2026' ref=1 desc=0 price=1" in output
    assert "quality.clean" in output
    assert "text=5712R long long" in output
    assert "..." in output
    assert "seller=seller name" in output
    assert "source=https://example.test/listing/123" in output
    assert "raw_listing_text" not in output


def test_build_query_report_marks_karat_gold_without_price_as_missing_price() -> None:
    result = SearchResult(
        "5712R Patek original movement customized 18k rose gold case reservation",
        posted_date="May 17, 2026",
    )

    report = build_query_report("5712r", [result], limit=1)

    row = report.rows[0]
    assert row.quality_group == 1
    assert row.price_evidence_score == 0
    assert row.suspicious_reasons == ("missing_price_evidence",)


def test_format_json_report_is_machine_readable() -> None:
    report = build_query_report(
        "5205r green",
        [SearchResult("5205R green New 4/2026 425.000 Hkd", posted_date="May 18, 2026")],
        limit=1,
    )

    payload = json.loads(format_json_report([report]))

    assert payload[0]["query"] == "5205r green"
    assert payload[0]["result_count"] == 1
    assert payload[0]["rows"][0]["quality_group"] == 0
    assert payload[0]["rows"][0]["score_reasons"]
