from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re

from app.searching.matcher_normalization import normalize_text
from app.searching.matcher_aliases import canonicalize_descriptor_tokens_as_set
from app.searching.matcher_token_classification import parse_query_terms

try:
    from rapidfuzz import fuzz
except ImportError:  # pragma: no cover - local env may not have new dependency yet.
    fuzz = None


REFERENCE_TOKEN_RE = re.compile(
    r"\b(?=[a-z0-9/.-]*\d)[a-z0-9]+(?:[./-][a-z0-9]+)*\b"
)


@dataclass(frozen=True)
class FuzzyDiagnostics:
    query_text_score: int
    reference_score: int
    descriptor_overlap_score: int
    overall_score: int
    reason_codes: tuple[str, ...]


def score_fuzzy_match(query: str, listing_text: str) -> FuzzyDiagnostics:
    normalized_query = normalize_text(query)
    normalized_listing = normalize_text(listing_text)
    reference_terms, descriptor_tokens = parse_query_terms(query)
    reference_score = _reference_score(reference_terms, normalized_listing)
    descriptor_overlap_score = _descriptor_overlap_score(
        descriptor_tokens,
        normalized_listing,
    )
    query_text_score = _ratio(normalized_query, normalized_listing)
    weighted_scores = [query_text_score, reference_score]
    if descriptor_tokens:
        weighted_scores.append(descriptor_overlap_score)
    overall_score = round(sum(weighted_scores) / len(weighted_scores))
    return FuzzyDiagnostics(
        query_text_score=query_text_score,
        reference_score=reference_score,
        descriptor_overlap_score=descriptor_overlap_score,
        overall_score=overall_score,
        reason_codes=_reason_codes(
            reference_terms=reference_terms,
            descriptor_tokens=descriptor_tokens,
            reference_score=reference_score,
            descriptor_overlap_score=descriptor_overlap_score,
            query_text_score=query_text_score,
        ),
    )


def _reference_score(
    reference_terms: list[list[str]],
    normalized_listing: str,
) -> int:
    if not reference_terms:
        return 0
    listing_references = REFERENCE_TOKEN_RE.findall(normalized_listing)
    if not listing_references:
        return 0
    scores: list[int] = []
    for parts in reference_terms:
        reference = " ".join(parts)
        compact_reference = _compact(reference)
        for candidate in listing_references:
            compact_candidate = _compact(candidate)
            if compact_reference == compact_candidate:
                scores.append(100)
            else:
                scores.append(_ratio(compact_reference, compact_candidate))
    return max(scores) if scores else 0


def _descriptor_overlap_score(
    descriptor_tokens: list[str],
    normalized_listing: str,
) -> int:
    if not descriptor_tokens:
        return 100
    listing_tokens = canonicalize_descriptor_tokens_as_set(normalized_listing.split())
    query_tokens = canonicalize_descriptor_tokens_as_set(descriptor_tokens)
    matched = sum(1 for token in query_tokens if token in listing_tokens)
    if not query_tokens:
        return 100
    return round((matched / len(query_tokens)) * 100)


def _reason_codes(
    *,
    reference_terms: list[list[str]],
    descriptor_tokens: list[str],
    reference_score: int,
    descriptor_overlap_score: int,
    query_text_score: int,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if reference_terms:
        if reference_score == 100:
            reasons.append("exact_reference_match")
        elif reference_score >= 80:
            reasons.append("near_reference_match")
        elif reference_score < 60:
            reasons.append("reference_score_low")
    if descriptor_tokens and descriptor_overlap_score < 100:
        reasons.append("descriptor_overlap_low")
    if query_text_score < 45:
        reasons.append("query_text_score_low")
    return tuple(reasons)


def _ratio(left: str, right: str) -> int:
    if not left or not right:
        return 0
    if fuzz is not None:
        return round(fuzz.token_set_ratio(left, right))
    return round(SequenceMatcher(None, left, right).ratio() * 100)


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())
