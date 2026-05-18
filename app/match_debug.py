from __future__ import annotations

from app.matcher import explain_extraction
from app.result_scoring import score_result
from app.telegram_bot import SearchResult


MAX_DEBUG_TEXT_LENGTH = 2000


def format_match_debug(
    query: str,
    listing_text: str,
    *,
    posted_date: str | None = None,
    raw_listing_text: str | None = None,
    original_rank: int = 0,
) -> str:
    trace = explain_extraction(query, listing_text)
    score = score_result(
        SearchResult(
            listing_text=trace.output_text,
            posted_date=posted_date,
            raw_listing_text=raw_listing_text or listing_text,
        ),
        original_rank=original_rank,
        query=query,
    )
    lines = [
        "Match debug",
        f"query: {trace.query}",
        f"reference_terms: {trace.intent.reference_terms}",
        f"descriptor_tokens: {trace.intent.descriptor_tokens}",
        f"selected_reference: {trace.selected_reference}",
        f"matched_token_span: {trace.matched_token_span}",
        f"selected_char_span: {trace.selected_char_span}",
        f"rule_ids: {', '.join(trace.rule_ids)}",
        f"quality_group: {score.quality_group}",
        f"quality_severity: {score.quality_severity}",
        f"posted_date_group: {score.posted_date_group}",
        f"exact_reference_score: {score.exact_reference_score}",
        f"descriptor_score: {score.descriptor_score}",
        f"price_evidence_score: {score.price_evidence_score}",
        f"score_reasons: {', '.join(score.reasons)}",
        f"output_text: {_single_line(trace.output_text)}",
    ]
    return _cap_debug_text("\n".join(lines))


def _single_line(value: str) -> str:
    return " ".join(value.split())


def _cap_debug_text(value: str) -> str:
    if len(value) <= MAX_DEBUG_TEXT_LENGTH:
        return value
    return value[: MAX_DEBUG_TEXT_LENGTH - 15].rstrip() + "\n...[truncated]"
