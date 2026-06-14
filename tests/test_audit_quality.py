from __future__ import annotations

from argparse import Namespace
import asyncio
import importlib.util
import json

import pytest

from app.config import load_search_settings
from app.search_result import SearchResult
from scripts.diagnostics import audit_quality
from scripts.diagnostics.audit_quality import (
    DEFAULT_AUDIT_QUERIES,
    build_query_report,
    compare_jsonl_reports,
    format_json_report,
    format_jsonl_report,
    format_text_report,
    load_queries,
    summarize_jsonl_report,
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
        image_url="https://example.test/image.jpg",
    )
    report = build_query_report("5712r", [result], limit=1, snippet_chars=60)

    output = format_text_report([report])

    assert "=== 5712r count=1 top_qg=[0] ===" in output
    assert "summary=audited_result_count:1 image_missing_count:0 image_missing_rate:0.0000" in output
    assert "#1 qg=0 sev=0 date='May 18, 2026' ref=1 desc=0 price=1" in output
    assert "fuzzy=" in output
    assert "image=True" in output
    assert "diagnostics=image_reason:image.direct scope_reason:scope.full_listing" in output
    assert "stable_listing_id:watchfacts-listing:" in output
    assert "quality.clean" in output
    assert "text=5712R long long" in output
    assert "..." in output
    assert "seller=seller name" in output
    assert "source=https://example.test/listing/123" in output
    assert "raw_listing_text" not in output


def test_format_text_report_includes_validation_errors() -> None:
    report = build_query_report(
        "5712r",
        [SearchResult("5712R HKD 100000")],
        limit=1,
        validation_errors=("results[0].result_id must be unique",),
    )

    output = format_text_report([report])

    assert "validation_errors=" in output
    assert "results[0].result_id must be unique" in output
    assert report.summary.validation_error_count == 1


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
    assert row.has_image is False
    assert row.image_reason == "image.missing_source"
    assert report.summary.image_missing_count == 1
    assert report.summary.image_missing_rate == 1.0


def test_format_json_report_is_machine_readable() -> None:
    report = build_query_report(
        "5205r green",
        [SearchResult("5205R green New 4/2026 425.000 Hkd", posted_date="May 18, 2026")],
        limit=1,
    )

    payload = json.loads(format_json_report([report]))

    assert payload[0]["query"] == "5205r green"
    assert payload[0]["result_count"] == 1
    assert payload[0]["summary"]["audited_result_count"] == 1
    assert payload[0]["summary"]["image_reason_counts"] == {"image.missing_source": 1}
    assert payload[0]["rows"][0]["quality_group"] == 0
    assert payload[0]["rows"][0]["fuzzy_score"] >= 0
    assert payload[0]["rows"][0]["fuzzy_reference_score"] == 100
    assert payload[0]["rows"][0]["query_intent"] == "reference_with_descriptor"
    assert payload[0]["rows"][0]["guardrail_action"] == "none"
    assert payload[0]["rows"][0]["has_image"] is False
    assert payload[0]["rows"][0]["image_reason"] == "image.missing_source"
    assert payload[0]["rows"][0]["scope_reason"] == "scope.full_listing"
    assert payload[0]["rows"][0]["stable_listing_id"].startswith("watchfacts-listing:")
    assert payload[0]["rows"][0]["score_reasons"]
    assert "audit_events" not in payload[0]


def test_format_jsonl_report_includes_stage_events_and_redacts_text() -> None:
    report = build_query_report(
        "5712g",
        [SearchResult("5712G Used 2015 - 76k usdt")],
        limit=1,
        audit_events=(
            audit_quality.SearchAuditEvent(
                query="5712g",
                stage="raw",
                candidate_id="raw:1",
                source_url="https://watchfacts.example/result",
                text="html_chars=100 cookie=session data/watchfacts_state.json",
                reason_codes=("client_filtered",),
                decision="include",
                query_intent="reference_only",
                fuzzy_score=100,
                guardrail_action="none",
                stable_audit_id="audit-1",
            ),
        ),
    )

    lines = [json.loads(line) for line in format_jsonl_report([report]).splitlines()]

    assert lines[0]["type"] == "query_summary"
    assert lines[0]["query_plan"] == {
        "original_query": "5712g",
        "canonical_query": "5712g",
        "brand_candidates": [
            {
                "brand": "patek_philippe",
                "confidence": "reference",
                "source_terms": ["5712g"],
            },
        ],
        "references": [["5712g"]],
        "collections": ["nautilus"],
        "nicknames": [],
        "required_descriptors": [],
        "optional_descriptors": [],
        "conflict_descriptors": [],
        "intent_kind": "reference_only",
        "reason_codes": [
            "reference.present",
            "descriptor.absent",
            "brand.reference:patek_philippe",
            "collection.reference:nautilus",
        ],
    }
    assert lines[1]["type"] == "audit_event"
    assert lines[1]["stage"] == "raw"
    assert lines[1]["decision"] == "include"
    assert lines[1]["query_intent"] == "reference_only"
    assert lines[1]["fuzzy_score"] == 100
    assert lines[1]["guardrail_action"] == "none"
    assert lines[1]["stable_audit_id"] == "audit-1"
    assert "cookie=session" not in lines[1]["text_snippet"]
    assert "watchfacts_state.json" not in lines[1]["text_snippet"]
    assert lines[2]["type"] == "final_result"
    assert lines[2]["decision"] == "include"
    assert lines[2]["query_intent"] == "reference_only"


def test_summarize_jsonl_report_uses_duckdb(tmp_path) -> None:
    if importlib.util.find_spec("duckdb") is None:
        pytest.skip("duckdb is not installed in the local test environment")

    jsonl_path = tmp_path / "audit.jsonl"
    jsonl_path.write_text(
        "\n".join(
            [
                json.dumps({"type": "audit_event", "query": "5712g", "stage": "raw"}),
                json.dumps({"type": "audit_event", "query": "5712g", "stage": "parsed"}),
                json.dumps(
                    {
                        "type": "audit_event",
                        "query": "5712g",
                        "stage": "weak_match",
                        "decision": "demote",
                    }
                ),
                json.dumps(
                    {
                        "type": "audit_event",
                        "query": "5712g",
                        "stage": "ambiguous_candidate",
                        "decision": "ambiguous",
                    }
                ),
                json.dumps(
                    {
                        "type": "audit_event",
                        "query": "5712g",
                        "stage": "dedupe_drop",
                        "decision": "deduped",
                    }
                ),
                json.dumps(
                    {
                        "type": "final_result",
                        "query": "5712g",
                        "stage": "final",
                        "has_image": False,
                        "fuzzy_score": 52,
                        "scope_reason": "scope.stock_list",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    output = summarize_jsonl_report(jsonl_path)

    assert "query,stage,row_count" in output
    assert "metrics weak_match_rate=1.0000 ambiguous_candidate_rate=1.0000 dedupe_drop_rate=1.0000 low_fuzzy_included_count=1 missing_image_rate=1.0000 stock_list_scoped_rate=1.0000" in output
    assert "5712g,final,1" in output
    assert "5712g,parsed,1" in output
    assert "5712g,raw,1" in output


def test_compare_jsonl_reports_uses_duckdb(tmp_path) -> None:
    if importlib.util.find_spec("duckdb") is None:
        pytest.skip("duckdb is not installed in the local test environment")

    before_path = tmp_path / "before.jsonl"
    after_path = tmp_path / "after.jsonl"
    before_path.write_text(
        "\n".join(
            [
                json.dumps({"type": "audit_event", "query": "5712g", "stage": "weak_match"}),
                json.dumps({"type": "final_result", "query": "5712g", "stage": "final"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    after_path.write_text(
        "\n".join(
            [
                json.dumps({"type": "audit_event", "query": "5712g", "stage": "weak_match"}),
                json.dumps({"type": "audit_event", "query": "5712g", "stage": "weak_match"}),
                json.dumps(
                    {
                        "type": "audit_event",
                        "query": "5712g",
                        "stage": "ambiguous_candidate",
                    }
                ),
                json.dumps({"type": "final_result", "query": "5712g", "stage": "final"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    output = compare_jsonl_reports(before_path, after_path)

    assert "query,stage,before_count,after_count,delta" in output
    assert "5712g,weak_match,1,2,1" in output
    assert "5712g,ambiguous_candidate,0,1,1" in output


def test_build_query_report_marks_stock_list_scope_and_redacts_raw_preview() -> None:
    result = SearchResult(
        "5712g new 2024 -> 115k",
        raw_listing_text=(
            "HK STOCK LIST 116505 rainbow 284k 5712g new 2024 -> 115k "
            "cookie=session data/watchfacts_state.json"
        ),
    )

    report = build_query_report("5712g", [result], limit=1, server_filtered=True)
    row = report.rows[0]

    assert row.scope_reason == "scope.stock_list"
    assert row.image_reason == "image.omitted_bundle_ambiguous"
    assert row.server_filtered is True
    assert row.raw_listing_preview is not None
    assert "cookie=session" not in row.raw_listing_preview
    assert "watchfacts_state.json" not in row.raw_listing_preview
    assert "[REDACTED]" in row.raw_listing_preview
    assert "[REDACTED_PATH]" in row.raw_listing_preview
    assert report.summary.server_filtered_result_count == 1
    assert report.summary.scoped_stock_list_count == 1


def test_build_query_report_emits_descriptor_conflict_reason_codes() -> None:
    result = SearchResult(
        "RM07-01 WG Snow Onyx N4-26 360000 USDT",
        posted_date="May 17, 2026",
    )

    report = build_query_report("rm07-01 rg snow", [result], limit=1)
    row = report.rows[0]
    events = [
        json.loads(line)
        for line in format_jsonl_report([report]).splitlines()
    ]
    final_event = next(event for event in events if event["type"] == "final_result")

    assert row.guardrail_action == "demote"
    assert "guardrail.descriptor_conflict" in row.score_reasons
    assert "conflict.local_descriptor:wg" in row.score_reasons
    assert final_event["guardrail_action"] == "demote"
    assert "guardrail.descriptor_conflict" in final_event["reason_codes"]
    assert "conflict.local_descriptor:wg" in final_event["reason_codes"]


def test_build_query_report_emits_segment_reason_codes_without_raw_payload() -> None:
    result = SearchResult(
        "REF: 126500LN YEAR: 2026 CONDITION: UNWORN W&C + WHITE TAG PRICE: 27350 USD",
        raw_listing_text=(
            "MODEL: PANDA DAYTONA REF: 126500LN YEAR: 2026 CONDITION: UNWORN "
            "COMES AS: W&C + WHITE TAG PRICE: 27350 USD cookie=session"
        ),
        segment_reason_codes=("raw_context.used:panda",),
    )

    report = build_query_report("126500ln white 2026", [result], limit=1)
    output = format_text_report([report])
    events = [
        json.loads(line)
        for line in format_jsonl_report([report]).splitlines()
    ]
    final_event = next(event for event in events if event["type"] == "final_result")

    assert report.rows[0].segment_reason_codes == ("raw_context.used:panda",)
    assert "segment_reasons=raw_context.used:panda" in output
    assert "raw_context.used:panda" in final_event["reason_codes"]
    assert "cookie=session" not in json.dumps(final_event)
