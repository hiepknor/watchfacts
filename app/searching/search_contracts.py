from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit


REQUIRED_TOP_LEVEL_FIELDS = {
    "query",
    "total_count",
    "offset",
    "limit",
    "result_count",
    "has_more",
    "next_offset",
    "results",
}
REQUIRED_RESULT_FIELDS = {
    "result_id",
    "stable_listing_id",
    "rank",
    "listing_text",
    "seller",
    "posted_date",
    "source_url",
    "image_url",
}


def validate_search_payload(
    payload: dict[str, Any],
    *,
    allow_empty: bool = False,
) -> list[str]:
    errors: list[str] = []
    for field in sorted(REQUIRED_TOP_LEVEL_FIELDS):
        if field not in payload:
            errors.append(f"{field} is required")

    _validate_required_text(payload, "query", errors)
    _validate_non_negative_int(payload, "total_count", errors)
    _validate_non_negative_int(payload, "offset", errors)
    _validate_optional_positive_int(payload, "limit", errors)
    _validate_non_negative_int(payload, "result_count", errors)
    if "has_more" in payload and not isinstance(payload["has_more"], bool):
        errors.append("has_more must be a boolean")
    if payload.get("has_more") is True:
        _validate_non_negative_int(payload, "next_offset", errors)
    elif "next_offset" in payload and payload["next_offset"] is not None:
        errors.append("next_offset must be null when has_more is false")

    results = payload.get("results")
    if not isinstance(results, list):
        errors.append("results must be a list")
        return errors
    if "result_count" in payload and isinstance(payload["result_count"], int):
        if payload["result_count"] != len(results):
            errors.append("result_count must equal the number of results")
    if not results and not allow_empty:
        errors.append("results must contain at least one result")
        return errors

    seen_result_ids: set[str] = set()
    for index, result in enumerate(results):
        if not isinstance(result, dict):
            errors.append(f"results[{index}] must be an object")
            continue
        for field in sorted(REQUIRED_RESULT_FIELDS):
            if field not in result:
                errors.append(f"results[{index}].{field} is required")
        _validate_required_text(result, "result_id", errors, prefix=f"results[{index}].")
        _validate_required_text(
            result,
            "stable_listing_id",
            errors,
            prefix=f"results[{index}].",
        )
        _validate_positive_int(result, "rank", errors, prefix=f"results[{index}].")
        _validate_required_text(
            result,
            "listing_text",
            errors,
            prefix=f"results[{index}].",
        )
        for field in ("seller", "posted_date", "source_url", "image_url"):
            _validate_optional_text(result, field, errors, prefix=f"results[{index}].")
        _validate_optional_url(result, "source_url", errors, prefix=f"results[{index}].")
        result_id = result.get("result_id")
        if isinstance(result_id, str) and result_id.strip():
            if result_id in seen_result_ids:
                errors.append(f"results[{index}].result_id must be unique")
            seen_result_ids.add(result_id)
    return errors


def validate_search_diagnostics(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        json.dumps(payload)
    except (TypeError, ValueError):
        errors.append("search_diagnostics must be JSON-serializable")

    cache_hit = payload.get("cache_hit")
    if "cache_hit" in payload and not isinstance(cache_hit, bool):
        errors.append("search_diagnostics.cache_hit must be a boolean")

    for field in (
        "raw_candidate_count",
        "parsed_count",
        "matched_count",
        "search_result_count",
        "unique_latest_count",
        "unique_text_count",
        "deduped_drop_count",
        "weak_match_count",
        "ambiguous_candidate_count",
        "retrieval_query_count",
        "final_count",
    ):
        _validate_optional_non_negative_int(
            payload,
            field,
            errors,
            prefix="search_diagnostics.",
        )

    if cache_hit is not True:
        matched_count = payload.get("matched_count")
        final_count = payload.get("final_count")
        if (
            isinstance(matched_count, int)
            and not isinstance(matched_count, bool)
            and isinstance(final_count, int)
            and not isinstance(final_count, bool)
            and final_count > matched_count
        ):
            errors.append("search_diagnostics.final_count must not exceed matched_count")
    for field in ("retrieval_queries", "retrieval_reason_codes"):
        if field in payload:
            _validate_string_list(
                payload.get(field),
                errors,
                prefix=f"search_diagnostics.{field}",
            )
    _validate_query_plan(payload.get("query_plan"), errors)
    return errors


def _validate_query_plan(value: Any, errors: list[str]) -> None:
    if value is None:
        return
    prefix = "search_diagnostics.query_plan."
    if not isinstance(value, dict):
        errors.append("search_diagnostics.query_plan must be an object or null")
        return

    for field in ("original_query", "canonical_query", "intent_kind"):
        if field not in value:
            errors.append(f"{prefix}{field} is required")
        else:
            _validate_required_text(value, field, errors, prefix=prefix)

    for field in (
        "brand_candidates",
        "references",
        "collections",
        "nicknames",
        "required_descriptors",
        "optional_descriptors",
        "conflict_descriptors",
        "reason_codes",
    ):
        if field not in value:
            errors.append(f"{prefix}{field} is required")
        elif not isinstance(value[field], list):
            errors.append(f"{prefix}{field} must be a list")

    brand_candidates = value.get("brand_candidates")
    if isinstance(brand_candidates, list):
        for index, candidate in enumerate(brand_candidates):
            candidate_prefix = f"{prefix}brand_candidates[{index}]."
            if not isinstance(candidate, dict):
                errors.append(f"{prefix}brand_candidates[{index}] must be an object")
                continue
            for field in ("brand", "confidence", "source_terms"):
                if field not in candidate:
                    errors.append(f"{candidate_prefix}{field} is required")
            _validate_required_text(candidate, "brand", errors, prefix=candidate_prefix)
            _validate_required_text(
                candidate,
                "confidence",
                errors,
                prefix=candidate_prefix,
            )
            _validate_string_list(
                candidate.get("source_terms"),
                errors,
                prefix=f"{candidate_prefix}source_terms",
            )

    references = value.get("references")
    if isinstance(references, list):
        for index, reference in enumerate(references):
            _validate_string_list(
                reference,
                errors,
                prefix=f"{prefix}references[{index}]",
            )

    for field in (
        "collections",
        "nicknames",
        "required_descriptors",
        "optional_descriptors",
        "conflict_descriptors",
        "reason_codes",
    ):
        if isinstance(value.get(field), list):
            _validate_string_list(value[field], errors, prefix=f"{prefix}{field}")


def _validate_string_list(value: Any, errors: list[str], *, prefix: str) -> None:
    if not isinstance(value, list):
        errors.append(f"{prefix} must be a list")
        return
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{prefix}[{index}] must be a non-empty string")


def _validate_required_text(
    payload: dict[str, Any],
    field: str,
    errors: list[str],
    *,
    prefix: str = "",
) -> None:
    if field not in payload:
        return
    value = payload[field]
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{prefix}{field} must be a non-empty string")


def _validate_optional_text(
    payload: dict[str, Any],
    field: str,
    errors: list[str],
    *,
    prefix: str = "",
) -> None:
    if field not in payload:
        return
    value = payload[field]
    if value is not None and not isinstance(value, str):
        errors.append(f"{prefix}{field} must be a string or null")


def _validate_optional_url(
    payload: dict[str, Any],
    field: str,
    errors: list[str],
    *,
    prefix: str = "",
) -> None:
    value = payload.get(field)
    if value is None or not isinstance(value, str) or not value.strip():
        return
    parsed = urlsplit(value)
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        errors.append(
            f"{prefix}{field} must be an http(s), relative, or root-relative URL"
        )


def _validate_positive_int(
    payload: dict[str, Any],
    field: str,
    errors: list[str],
    *,
    prefix: str = "",
) -> None:
    if field not in payload:
        return
    value = payload[field]
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        errors.append(f"{prefix}{field} must be a positive integer")


def _validate_non_negative_int(
    payload: dict[str, Any],
    field: str,
    errors: list[str],
    *,
    prefix: str = "",
) -> None:
    if field not in payload:
        return
    value = payload[field]
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        errors.append(f"{prefix}{field} must be a non-negative integer")


def _validate_optional_non_negative_int(
    payload: dict[str, Any],
    field: str,
    errors: list[str],
    *,
    prefix: str = "",
) -> None:
    if field not in payload or payload[field] is None:
        return
    _validate_non_negative_int(payload, field, errors, prefix=prefix)


def _validate_optional_positive_int(
    payload: dict[str, Any],
    field: str,
    errors: list[str],
    *,
    prefix: str = "",
) -> None:
    if field not in payload:
        return
    value = payload[field]
    if value is not None and (
        not isinstance(value, int) or isinstance(value, bool) or value <= 0
    ):
        errors.append(f"{prefix}{field} must be a positive integer or null")
