from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace
from difflib import SequenceMatcher

from app.llm_matcher import deterministic_refine_listing_text
from app.matcher import normalize_text
from app.telegram_bot import SearchResult


SIMILARITY_THRESHOLD = 0.78
REFERENCE_RE = re.compile(
    r"\b(?=[A-Za-z0-9/.-]*\d)[A-Za-z0-9]+(?:[./-][A-Za-z0-9]+)*\b",
    re.IGNORECASE,
)
PRICE_RE = re.compile(
    r"""
    (?:
        (?:hkd|usd|usdt|eur|aed|chf)\s*
        (?:\d{1,3}(?:[,\s.]\d{3})+|\d+(?:\.\d+)?[km]?)
        |
        [$€£¥]\s*(?:\d{1,3}(?:[,\s.]\d{3})+|\d+(?:\.\d+)?[km]?)
        |
        \b(?:\d{1,3}(?:[,\s.]\d{3})+|\d+(?:\.\d+)?[km])\s*
        (?:hkd|usd|usdt|eur|aed|chf)?\b
        |
        \b\d{5,8}\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
CONDITION_GROUPS = {
    "new": {"new", "brandnew", "nos"},
    "used": {"used", "likenew"},
    "full_set": {"fullset", "full set"},
    "watch_only": {"watchonly", "watch only"},
}
STOP_TOKENS = {
    "and",
    "brand",
    "card",
    "cert",
    "full",
    "hkd",
    "set",
    "usd",
    "used",
    "watch",
    "with",
}


def group_similar_results(
    results: Iterable[SearchResult],
    *,
    query: str | None = None,
) -> list[SearchResult]:
    grouped: list[SearchResult] = []

    for result in results:
        for index, existing in enumerate(grouped):
            if not _looks_similar(existing.listing_text, result.listing_text, query=query):
                continue
            if _is_better_primary(result, existing):
                grouped[index] = replace(
                    result,
                    similar_results=(
                        _without_similar_results(existing),
                        *existing.similar_results,
                    ),
                )
            else:
                grouped[index] = replace(
                    existing,
                    similar_results=(*existing.similar_results, result),
                )
            break
        else:
            grouped.append(result)

    return grouped


def _looks_similar(left: str, right: str, *, query: str | None = None) -> bool:
    left_profile = _profile(left, query=query)
    right_profile = _profile(right, query=query)

    if _has_conflict(left_profile["references"], right_profile["references"]):
        return False
    if _has_conflict(left_profile["years"], right_profile["years"]):
        return False
    if _has_condition_conflict(left_profile["conditions"], right_profile["conditions"]):
        return False
    if not _prices_compatible(left_profile["prices"], right_profile["prices"]):
        return False
    if not _product_tokens_compatible(left_profile["product_tokens"], right_profile["product_tokens"]):
        return False

    return _token_similarity(left_profile["tokens"], right_profile["tokens"]) >= SIMILARITY_THRESHOLD


def _is_better_primary(candidate: SearchResult, current: SearchResult) -> bool:
    return _quality_score(candidate.listing_text) > _quality_score(current.listing_text)


def _quality_score(value: str) -> tuple[int, int]:
    multi_markers = sum(value.count(marker) for marker in ("- [ ]", " • ", " | "))
    return (-multi_markers, 0)


def _without_similar_results(result: SearchResult) -> SearchResult:
    return replace(result, similar_results=())


def _profile(value: str, *, query: str | None = None) -> dict[str, set[str]]:
    if query:
        value = deterministic_refine_listing_text(query, value)
    normalized = _similarity_text(value)
    tokens = set(normalized.split())
    return {
        "tokens": tokens,
        "references": _references(normalized),
        "years": set(YEAR_RE.findall(normalized)),
        "conditions": _conditions(normalized),
        "prices": _prices(value),
        "product_tokens": {
            token for token in tokens if token.isalpha() and token not in STOP_TOKENS
        },
    }


def _similarity_text(value: str) -> str:
    value = re.sub(r"(\d{2,3}mm)(\d{4})", r"\1 \2", value, flags=re.IGNORECASE)
    value = re.sub(r"\bfull\s*set\b", "fullset", value, flags=re.IGNORECASE)
    return normalize_text(value)


def _references(normalized: str) -> set[str]:
    references: set[str] = set()
    for match in REFERENCE_RE.finditer(normalized):
        token = normalize_text(match.group(0))
        if not token or not _looks_like_reference(token):
            continue
        references.add(token)
    return references


def _looks_like_reference(token: str) -> bool:
    if re.fullmatch(r"\d{2,3}mm", token):
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?[km]", token):
        return False
    if token.isdigit():
        if len(token) == 4 and 1900 <= int(token) <= 2099:
            return False
        return False
    if any(currency in token for currency in ("hkd", "usd", "usdt", "eur", "aed", "chf")):
        return False
    return len(token) >= 4 or "/" in token


def _conditions(normalized: str) -> set[str]:
    found: set[str] = set()
    padded = f" {normalized} "
    for bucket, markers in CONDITION_GROUPS.items():
        if any(f" {marker} " in padded for marker in markers):
            found.add(bucket)
    return found


def _prices(value: str) -> set[str]:
    prices: set[str] = set()
    for match in PRICE_RE.finditer(value):
        parsed = _parse_price(match.group(0))
        if parsed is not None:
            prices.add(str(parsed))
    return prices


def _parse_price(value: str) -> int | None:
    normalized = value.casefold()
    multiplier = 1
    if "m" in normalized:
        multiplier = 1_000_000
    elif "k" in normalized:
        multiplier = 1_000

    number_match = re.search(r"\d+(?:[,\s.]\d+)*(?:\.\d+)?", normalized)
    if number_match is None:
        return None
    raw_number = number_match.group(0)
    if multiplier == 1 and re.search(r"\d{1,3}(?:[,\s.]\d{3})+", raw_number):
        raw_number = re.sub(r"[\s,.]", "", raw_number)
    else:
        raw_number = raw_number.replace(",", "").replace(" ", "")
    try:
        return int(float(raw_number) * multiplier)
    except ValueError:
        return None


def _has_conflict(left: set[str], right: set[str]) -> bool:
    return bool(left and right and left.isdisjoint(right))


def _has_condition_conflict(left: set[str], right: set[str]) -> bool:
    if not left or not right:
        return False
    if ("new" in left and "used" in right) or ("used" in left and "new" in right):
        return True
    if ("full_set" in left and "watch_only" in right) or (
        "watch_only" in left and "full_set" in right
    ):
        return True
    return False


def _prices_compatible(left: set[str], right: set[str]) -> bool:
    if not left or not right:
        return False
    return not left.isdisjoint(right)


def _product_tokens_compatible(left: set[str], right: set[str]) -> bool:
    if not left or not right:
        return True
    return len(left & right) >= min(2, min(len(left), len(right)))


def _token_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    jaccard = len(left & right) / len(left | right)
    sequence = SequenceMatcher(None, " ".join(sorted(left)), " ".join(sorted(right))).ratio()
    return max(jaccard, sequence)
