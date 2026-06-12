from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from app.config import Settings
from app.db import Database
from app.searching.issues import detect_suspicious_result
from app.searching.matcher import listing_matches, tokenize_query
from app.searching.search_result import SearchResult


Complete = Callable[[str], Awaitable[str]]
logger = logging.getLogger("app.ai_refiner")
MULTI_ITEM_MARKERS = (" - [ ]", "\n-", "\n•", " • ", " | ")
MIN_REFINEMENT_CONFIDENCE = 0.7
MAX_REFINED_TEXT_CHARS = 1024
AI_REFINEMENT_REASON_ALLOWLIST = {
    "ends_with_currency",
    "ends_with_price_marker",
    "missing_price_after_currency",
    "missing_price_evidence",
    "raw_much_longer",
}
UNRELATED_BRAND_OR_MODEL_TOKENS = {
    "audemars",
    "cartier",
    "greubel",
    "patek",
    "philippe",
    "quantieme",
    "richard",
    "rolex",
    "rose gold cs",
    "tudor",
    "vacheron",
}


@dataclass(frozen=True)
class RefinementGate:
    status: str
    reasons: tuple[str, ...]


async def refine_search_results(
    query: str,
    results: list[SearchResult],
    settings: Settings,
    *,
    database: Database | None = None,
) -> list[SearchResult]:
    if settings.hybrid_ai_mode == "off" or not settings.openai_api_key or not results:
        return results

    complete = _settings_complete(settings)
    refined: list[SearchResult] = []
    refine_count = 0
    for result in results:
        if (
            refine_count >= settings.openai_max_refines
            or not should_refine_search_result(result)
        ):
            refined.append(result)
            continue
        refinement_input = _refinement_input(result)
        refine_count += 1
        cached = (
            database.get_llm_refinement(
                query,
                refinement_input,
                settings.openai_model,
            )
            if database is not None
            else None
        )
        if cached is not None:
            refined.append(replace(result, listing_text=cached))
            continue

        start = time.perf_counter()
        listing_text = await refine_listing_text(query, refinement_input, complete=complete)
        if database is not None:
            database.record_llm_refinement(
                query,
                refinement_input,
                settings.openai_model,
                listing_text,
                latency_ms=int((time.perf_counter() - start) * 1000),
            )
        refined.append(replace(result, listing_text=listing_text))
    return refined


def evaluate_refinement_suggestion(
    query: str,
    deterministic: SearchResult,
    suggested: SearchResult,
) -> RefinementGate:
    reasons: list[str] = []
    suggested_text = " ".join(suggested.listing_text.split())
    raw_text = deterministic.raw_listing_text or deterministic.listing_text

    if not suggested_text:
        reasons.append("empty_suggestion")
    elif listing_matches(query, suggested_text):
        reasons.append("matches_query")
    else:
        reasons.append("query_mismatch")

    if suggested_text and suggested_text in raw_text:
        reasons.append("raw_substring")
    elif suggested_text:
        reasons.append("not_raw_substring")

    if suggested_text and len(suggested_text) > MAX_REFINED_TEXT_CHARS:
        reasons.append("exceeds_length")

    if suggested_text and _crosses_item_separator(suggested_text):
        reasons.append("crosses_item_separator")

    rejected = {
        "crosses_item_separator",
        "empty_suggestion",
        "exceeds_length",
        "query_mismatch",
        "not_raw_substring",
    }
    status = "rejected" if any(reason in rejected for reason in reasons) else "accepted"
    return RefinementGate(status=status, reasons=tuple(reasons))


def should_refine_listing_text(listing_text: str) -> bool:
    if len(_candidate_snippets(listing_text)) > 1:
        return True

    normalized = listing_text.casefold()
    if any(marker in listing_text for marker in MULTI_ITEM_MARKERS):
        return True

    token_hits = sum(
        1 for token in UNRELATED_BRAND_OR_MODEL_TOKENS if token in normalized
    )
    if "elegante" in normalized and token_hits > 0:
        return True
    return False


def should_refine_search_result(result: SearchResult) -> bool:
    if should_refine_listing_text(result.listing_text):
        return True

    return any(
        issue.reason in AI_REFINEMENT_REASON_ALLOWLIST
        for issue in detect_suspicious_result(
            listing_text=result.listing_text,
            raw_listing_text=result.raw_listing_text,
        )
    )


def _refinement_input(result: SearchResult) -> str:
    raw_text = result.raw_listing_text or ""
    if raw_text and result.listing_text in raw_text:
        return raw_text
    return result.listing_text


async def refine_listing_text(
    query: str,
    listing_text: str,
    *,
    complete: Complete,
) -> str:
    candidates = _candidate_snippets(listing_text)
    fallback_text = deterministic_refine_listing_text(query, listing_text)
    if _has_confident_candidate(query, candidates):
        return fallback_text

    prompt = _refine_prompt(query, listing_text, candidates)
    try:
        response_text = await complete(prompt)
        payload = _extract_json_object(response_text)
    except Exception as exc:
        logger.info("event=ai.refine_fallback error_type=%s", exc.__class__.__name__)
        return fallback_text

    if _payload_is_rejected(payload):
        return fallback_text

    index = payload.get("index")
    if isinstance(index, int) and 1 <= index <= len(candidates):
        return _post_process_refined_text(query, candidates[index - 1])

    refined_text = payload.get("selected_text") or payload.get("listing_text")
    if not isinstance(refined_text, str):
        return fallback_text
    refined_text = " ".join(refined_text.split())
    if not refined_text:
        return fallback_text
    if len(refined_text) > MAX_REFINED_TEXT_CHARS:
        logger.info("event=ai.refine_rejected reason=exceeds_length")
        return fallback_text
    if _crosses_item_separator(refined_text):
        logger.info("event=ai.refine_rejected reason=crosses_item_separator")
        return fallback_text
    if refined_text not in listing_text:
        logger.info("event=ai.refine_rejected reason=not_substring")
        return fallback_text
    return _post_process_refined_text(query, refined_text)


def _payload_is_rejected(payload: dict[str, object]) -> bool:
    if not isinstance(payload.get("relevant"), bool):
        logger.info("event=ai.refine_rejected reason=invalid_relevant")
        return True
    if payload.get("relevant") is False:
        return True

    if not isinstance(payload.get("index"), int):
        logger.info("event=ai.refine_rejected reason=invalid_index")
        return True

    if not isinstance(payload.get("selected_text"), str):
        logger.info("event=ai.refine_rejected reason=invalid_selected_text")
        return True

    confidence = payload.get("confidence")
    if not isinstance(confidence, int | float):
        logger.info("event=ai.refine_rejected reason=invalid_confidence")
        return True
    if confidence < MIN_REFINEMENT_CONFIDENCE:
        logger.info("event=ai.refine_rejected reason=low_confidence")
        return True

    reasons = payload.get("reasons")
    if not isinstance(reasons, list) or not all(
        isinstance(reason, str) for reason in reasons
    ):
        logger.info("event=ai.refine_rejected reason=invalid_reasons")
        return True

    risk_flags = payload.get("risk_flags")
    if not isinstance(risk_flags, list) or not all(
        isinstance(flag, str) for flag in risk_flags
    ):
        logger.info("event=ai.refine_rejected reason=invalid_risk_flags")
        return True
    if risk_flags:
        logger.info("event=ai.refine_rejected reason=risk_flags")
        return True

    return False


def deterministic_refine_listing_text(query: str, listing_text: str) -> str:
    candidates = _candidate_snippets(listing_text)
    if len(candidates) > 1:
        return _post_process_refined_text(query, _best_candidate(query, candidates))
    return _post_process_refined_text(query, listing_text)


def _settings_complete(settings: Settings) -> Complete:
    async def complete(prompt: str) -> str:
        return await asyncio.to_thread(_complete_sync, prompt, settings)

    return complete


def _complete_sync(prompt: str, settings: Settings) -> str:
    payload = {
        "model": settings.openai_model,
        "input": [
            {
                "role": "system",
                "content": (
                    "You refine WatchFacts listing snippets. Return only schema-valid "
                    "JSON. Do not invent text; selected_text must be copied from the "
                    "provided raw listing text."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "max_output_tokens": 256,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "watchfacts_refinement",
                "strict": True,
                "schema": _refinement_schema(),
            }
        },
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=settings.openai_timeout_seconds,
        ) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (TimeoutError, urllib.error.URLError) as exc:
        raise RuntimeError("OpenAI request failed") from exc

    return _extract_response_text(data)


def _extract_response_text(data: dict[str, object]) -> str:
    output_text = data.get("output_text")
    if isinstance(output_text, str):
        return output_text

    output = data.get("output")
    if not isinstance(output, list):
        raise ValueError("OpenAI response missing output")
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for content_item in content:
            if not isinstance(content_item, dict):
                continue
            text = content_item.get("text")
            if isinstance(text, str):
                return text
    raise ValueError("OpenAI response missing output text")


def _refinement_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "relevant": {"type": "boolean"},
            "index": {"type": "integer"},
            "selected_text": {"type": "string"},
            "confidence": {"type": "number"},
            "reasons": {"type": "array", "items": {"type": "string"}},
            "risk_flags": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "relevant",
            "index",
            "selected_text",
            "confidence",
            "reasons",
            "risk_flags",
        ],
    }


def _refine_prompt(query: str, listing_text: str, candidates: list[str]) -> str:
    candidate_lines = "\n".join(
        f"{index}. {candidate}" for index, candidate in enumerate(candidates, 1)
    )
    return f"""
You are cleaning a WatchFacts Telegram search result.

Query:
{query}

Candidate snippets from the raw listing text:
{candidate_lines}

Choose the one candidate that best matches the query. Exclude unrelated watch
models before or after the matching item.

Return JSON matching the required schema:
{{
  "relevant": true,
  "index": 1,
  "selected_text": "exact substring copied from the best candidate",
  "confidence": 0.9,
  "reasons": ["contains query reference"],
  "risk_flags": []
}}
""".strip()


def _extract_json_object(value: str) -> dict[str, object]:
    stripped = value.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end < start:
        raise ValueError("missing JSON object")
    parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("JSON response must be an object")
    return parsed


def _candidate_snippets(listing_text: str) -> list[str]:
    parts = [
        _clean_candidate(part)
        for part in re.split(r"\s+-\s+\[\s*\]\s+|\s+[•|]\s+", listing_text)
    ]
    candidates = [part for part in parts if part]
    return candidates or [listing_text]


def _crosses_item_separator(value: str) -> bool:
    return bool(re.search(r"\s+-\s+\[\s*\]\s+|\s+[•|]\s+", value))


def _clean_candidate(value: str) -> str:
    cleaned = " ".join(value.split())
    cleaned = re.sub(r"\s*-\s+\[\s*\]\s*$", "", cleaned)
    return cleaned.strip(" -•|")


def _best_candidate(query: str, candidates: list[str]) -> str:
    query_tokens = set(tokenize_query(query))
    if not query_tokens:
        return candidates[0]

    return max(candidates, key=lambda candidate: _candidate_score(query_tokens, candidate))


def _has_confident_candidate(query: str, candidates: list[str]) -> bool:
    if len(candidates) < 2:
        return False

    query_tokens = set(tokenize_query(query))
    if not query_tokens:
        return False

    scores = sorted(_candidate_score(query_tokens, candidate) for candidate in candidates)
    top_score = scores[-1]
    runner_up = scores[-2]
    return top_score[0] > 0 and top_score[0] > runner_up[0]


def _candidate_score(query_tokens: set[str], candidate: str) -> tuple[int, int]:
    candidate_tokens = set(tokenize_query(candidate))
    return (
        sum(1 for token in query_tokens if token in candidate_tokens),
        -len(candidate),
    )


def _post_process_refined_text(query: str, listing_text: str) -> str:
    return _strip_trailing_unrelated_token(query, listing_text)


def _strip_trailing_unrelated_token(query: str, listing_text: str) -> str:
    query_tokens = set(tokenize_query(query))
    words = listing_text.split()
    if len(words) < 2:
        return listing_text

    last_token = words[-1].strip(".,;:()[]{}").casefold()
    if last_token in query_tokens:
        return listing_text
    if last_token not in UNRELATED_BRAND_OR_MODEL_TOKENS:
        return listing_text
    return " ".join(words[:-1])
