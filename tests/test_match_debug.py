from __future__ import annotations

from app.searching.match_debug import MAX_DEBUG_TEXT_LENGTH, format_match_debug


def test_format_match_debug_includes_trace_and_score_reasons() -> None:
    output = format_match_debug(
        "7118/1200a grey",
        "📣*PP 7118/1200A grey* 💥$790k hkd 💥N1/2026",
        posted_date="May 17, 2026",
    )

    assert "Match debug" in output
    assert "selected_reference: ('7118/1200a',)" in output
    assert "reference.match_exact_or_compact" in output
    assert "quality_group: 0" in output
    assert "exact_reference_score: 1" in output
    assert "descriptor_score: 1" in output
    assert "price_evidence_score: 1" in output
    assert (
        "score_reasons: quality.clean, date.parsed, reference.selected, "
        "descriptor.local, price.visible"
    ) in output
    assert "output_text: PP 7118/1200A grey* 💥$790k hkd 💥N1/2026" in output


def test_format_match_debug_caps_long_output() -> None:
    output = format_match_debug("notpresent", "5205r " + "x " * 3000)

    assert len(output) <= MAX_DEBUG_TEXT_LENGTH
    assert output.endswith("...[truncated]")
