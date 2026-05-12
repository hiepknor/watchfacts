from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import replace

from app.config import Settings
from app.matcher import tokenize_query
from app.telegram_bot import SearchResult


Complete = Callable[[str], Awaitable[str]]
logger = logging.getLogger(__name__)
MULTI_ITEM_MARKERS = (" - [ ]", "\n-", "\n•", " | ")
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


async def refine_search_results(
    query: str,
    results: list[SearchResult],
    settings: Settings,
) -> list[SearchResult]:
    if not settings.local_llm_enabled or not results:
        return results

    complete = _settings_complete(settings)
    refined: list[SearchResult] = []
    refine_count = 0
    for result in results:
        if (
            refine_count >= settings.local_llm_max_refines
            or not should_refine_listing_text(result.listing_text)
        ):
            refined.append(result)
            continue
        refine_count += 1
        listing_text = await refine_listing_text(
            query,
            result.listing_text,
            complete=complete,
        )
        refined.append(replace(result, listing_text=listing_text))
    return refined


def should_refine_listing_text(listing_text: str) -> bool:
    normalized = listing_text.casefold()
    if any(marker in listing_text for marker in MULTI_ITEM_MARKERS):
        return True

    token_hits = sum(
        1 for token in UNRELATED_BRAND_OR_MODEL_TOKENS if token in normalized
    )
    if "elegante" in normalized and token_hits > 0:
        return True
    return False


async def refine_listing_text(
    query: str,
    listing_text: str,
    *,
    complete: Complete,
) -> str:
    candidates = _candidate_snippets(listing_text)
    fallback_text = (
        _best_candidate(query, candidates)
        if len(candidates) > 1
        else _strip_trailing_unrelated_token(query, listing_text)
    )
    prompt = _refine_prompt(query, listing_text, candidates)
    try:
        response_text = await complete(prompt)
        payload = _extract_json_object(response_text)
    except Exception as exc:
        logger.info("event=llm.refine_fallback error_type=%s", exc.__class__.__name__)
        return fallback_text

    if payload.get("relevant") is False:
        return fallback_text

    index = payload.get("index")
    if isinstance(index, int) and 1 <= index <= len(candidates):
        return candidates[index - 1]

    refined_text = payload.get("listing_text")
    if not isinstance(refined_text, str):
        return fallback_text
    refined_text = " ".join(refined_text.split())
    if not refined_text:
        return fallback_text
    if refined_text not in listing_text:
        logger.info("event=llm.refine_rejected reason=not_substring")
        return fallback_text
    return refined_text


def _settings_complete(settings: Settings) -> Complete:
    async def complete(prompt: str) -> str:
        return await asyncio.to_thread(_complete_sync, prompt, settings)

    return complete


def _complete_sync(prompt: str, settings: Settings) -> str:
    payload = {
        "model": settings.local_llm_model,
        "messages": [
            {
                "role": "system",
                "content": "Return concise JSON only. Do not invent text.",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "temperature": 0,
        "max_tokens": 256,
    }
    request = urllib.request.Request(
        f"{settings.local_llm_base_url.rstrip('/')}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=settings.local_llm_timeout_seconds,
        ) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError("local LLM request failed") from exc

    message = data["choices"][0]["message"]
    return message.get("content") or message.get("reasoning_content", "")


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

Return only JSON:
{{"relevant": true, "index": 1}}
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


def _clean_candidate(value: str) -> str:
    cleaned = " ".join(value.split())
    cleaned = re.sub(r"\s*-\s+\[\s*\]\s*$", "", cleaned)
    return cleaned.strip(" -•|")


def _best_candidate(query: str, candidates: list[str]) -> str:
    query_tokens = set(tokenize_query(query))
    if not query_tokens:
        return candidates[0]

    def score(candidate: str) -> tuple[int, int]:
        candidate_tokens = set(tokenize_query(candidate))
        return (
            sum(1 for token in query_tokens if token in candidate_tokens),
            -len(candidate),
        )

    return max(candidates, key=score)


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
