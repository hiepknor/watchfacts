from __future__ import annotations

import json

from scripts.diagnostics.prewarm_mcp_cache import (
    PrewarmRow,
    _row_from_payload,
    render_jsonl,
    render_text,
    summarize_rows,
)


def test_row_from_payload_extracts_cache_status_and_counts() -> None:
    row = _row_from_payload(
        query="5205r green",
        payload={
            "total_count": 26,
            "result_count": 5,
            "search_diagnostics": {"cache_hit": True},
        },
        elapsed_ms=42,
        pass_name="verify",
    )

    assert row.query == "5205r green"
    assert row.ok is True
    assert row.elapsed_ms == 42
    assert row.cache_hit is True
    assert row.total_count == 26
    assert row.result_count == 5
    assert row.pass_name == "verify"


def test_renderers_emit_text_and_jsonl() -> None:
    rows = [
        PrewarmRow(
            query="5205r green",
            ok=True,
            elapsed_ms=100,
            cache_hit=False,
            total_count=26,
            result_count=5,
        ),
        PrewarmRow(
            query="5712r",
            ok=True,
            elapsed_ms=20,
            cache_hit=True,
            total_count=124,
            result_count=5,
            pass_name="verify",
        ),
    ]

    text = render_text(rows)
    decoded = [json.loads(line) for line in render_jsonl(rows).splitlines()]

    assert "MCP_PREWARM pass=warm query='5205r green' ok=true" in text
    assert "SUMMARY" in text
    assert decoded[1]["query"] == "5712r"
    assert decoded[1]["cache_hit"] is True


def test_summarize_rows_counts_cache_hits_and_misses() -> None:
    summary = summarize_rows(
        [
            PrewarmRow(query="a", ok=True, elapsed_ms=100, cache_hit=False),
            PrewarmRow(query="b", ok=True, elapsed_ms=20, cache_hit=True),
            PrewarmRow(query="c", ok=False, elapsed_ms=5, error_type="RuntimeError"),
        ]
    )

    assert summary["passed"] == 2
    assert summary["total"] == 3
    assert summary["cache_hits"] == 1
    assert summary["cache_misses"] == 1
    assert summary["avg_ms"] == 60
    assert summary["min_ms"] == 20
    assert summary["max_ms"] == 100
