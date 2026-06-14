from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
import re

from app.searching.issues import detect_suspicious_result
from app.searching.fuzzy_diagnostics import score_fuzzy_match
from app.searching.matcher import explain_extraction
from app.searching.matcher_aliases import canonicalize_descriptor_tokens_as_set
from app.searching.matcher_normalization import normalize_text
from app.searching.query_intent import build_query_plan, classify_query_intent
from app.searching.search_result import SearchResult


PRICE_EVIDENCE_RE = re.compile(
    r"""
    (?:
        [$€£¥]\s*\d
        |
        \b(?:hkd|usd|usdt|eur|aed|chf)\s*\d
        |
        \b\d{1,3}(?:[,\s.]\d{3})+\s*(?:hkd|usd|usdt|eur|aed|chf)?\b
        |
        \b\d+(?:[,.]\d+)?[km]\b
        |
        \b\d+(?:[,.]\d+)?[km]?\s*[$€£¥](?=\W|$)
        |
        \b\d{5,8}\s*(?:hkd|usd|usdt|eur|aed|chf)?\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
KARAT_GOLD_RE = re.compile(
    r"\b(?:9|10|14|18|19|20|21|22|24)k\s+(?:(?:rose|yellow|white|pink)\s+)?gold\b",
    re.IGNORECASE,
)
COLOR_DESCRIPTOR_GROUP = {
    "black",
    "blue",
    "champ",
    "champagne",
    "cho",
    "choco",
    "chocolate",
    "gray",
    "green",
    "grey",
    "purple",
    "red",
    "silver",
    "white",
}
CANONICAL_COLOR_DESCRIPTOR_GROUP = canonicalize_descriptor_tokens_as_set(
    COLOR_DESCRIPTOR_GROUP,
)
PRICE_REASON_CODES = {
    "price.visible",
    "price.missing_visible",
    "price.ambiguous_neighbor",
}
ALIAS_CONFIDENCE_SCORES = {
    "reference": 3,
    "explicit": 2,
    "collection": 1,
    "nickname": 1,
}
SCOPE_CONFIDENCE_SCORES = {
    "scope.full_listing": 2,
    "scope.scoped": 1,
    "scope.stock_list": 0,
}
IMAGE_CONFIDENCE_SCORES = {
    "image.direct": 2,
    "image.inherited_parent_first_item": 1,
    "image.inherited_parent_reference": 1,
    "image.omitted_bundle_ambiguous": 0,
    "image.missing_source": 0,
}
STOCK_LIST_MARKER_RE = re.compile(
    r"\b(?:hk\s+)?stock\s+list\b|\bstocklist\b",
    re.IGNORECASE,
)
SCOPE_REFERENCE_RE = re.compile(
    r"\b(?=[A-Za-z0-9/.-]*\d)[A-Za-z0-9]+(?:[./-][A-Za-z0-9]+)*\b",
    re.IGNORECASE,
)
SCOPE_CONDITION_DATE_RE = re.compile(
    r"(?:n?\d{1,2}|\d{1,2}n)[/-]\d{2,4}y?"
    r"|\d{2,4}[/-](?:n?\d{1,2}|\d{1,2}n)y?"
    r"|\d{4}\.\d{1,2}y?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ResultScore:
    quality_group: int
    quality_severity: int
    conflict_penalty_score: int
    posted_date_group: int
    posted_date_timestamp: float
    alias_confidence_score: int
    exact_reference_score: int
    descriptor_score: int
    price_evidence_score: int
    scope_confidence_score: int
    image_confidence_score: int
    original_rank: int
    reasons: tuple[str, ...]

    def sort_key(
        self,
    ) -> tuple[int, int, int, int, float, int, int, int, int, int, int, int]:
        return (
            self.quality_group,
            self.conflict_penalty_score,
            self.quality_severity,
            self.posted_date_group,
            -self.posted_date_timestamp,
            -self.alias_confidence_score,
            -self.exact_reference_score,
            -self.descriptor_score,
            -self.price_evidence_score,
            -self.scope_confidence_score,
            -self.image_confidence_score,
            self.original_rank,
        )


def rank_results_by_quality(
    results: list[SearchResult],
    *,
    query: str | None = None,
) -> list[SearchResult]:
    if len(results) < 2:
        return results

    scored = [
        (score_result(result, original_rank=index, query=query), result)
        for index, result in enumerate(results)
    ]
    first_key = scored[0][0].sort_key()
    if all(score.sort_key() == first_key for score, _ in scored):
        return results
    return [
        result
        for score, result in sorted(scored, key=lambda item: item[0].sort_key())
    ]


def score_result(
    result: SearchResult,
    *,
    original_rank: int,
    query: str | None = None,
) -> ResultScore:
    quality_group, quality_severity, quality_reasons = _quality_score(result)
    guardrail_group, guardrail_reasons = _guardrail_score(result, query=query)
    quality_group = max(quality_group, guardrail_group)
    conflict_penalty_score, conflict_reasons = _conflict_penalty_score(
        guardrail_reasons
    )
    posted_date_group, posted_date_timestamp, date_reason = _posted_date_score(
        result.posted_date
    )
    alias_confidence_score, alias_reasons = _alias_confidence_score(query)
    (
        exact_reference_score,
        descriptor_score,
        relevance_reasons,
    ) = _relevance_score(result, query=query)
    price_evidence_score, price_reasons = _price_evidence_score(result)
    scope_confidence_score, scope_reasons = _scope_confidence_score(result)
    image_confidence_score, image_reasons = _image_confidence_score(
        result,
        scope_reason=scope_reasons[0],
    )
    reasons = (
        *quality_reasons,
        *guardrail_reasons,
        *conflict_reasons,
        date_reason,
        *alias_reasons,
        *relevance_reasons,
        *price_reasons,
        *scope_reasons,
        *image_reasons,
    )
    return ResultScore(
        quality_group=quality_group,
        quality_severity=quality_severity,
        conflict_penalty_score=conflict_penalty_score,
        posted_date_group=posted_date_group,
        posted_date_timestamp=posted_date_timestamp,
        alias_confidence_score=alias_confidence_score,
        exact_reference_score=exact_reference_score,
        descriptor_score=descriptor_score,
        price_evidence_score=price_evidence_score,
        scope_confidence_score=scope_confidence_score,
        image_confidence_score=image_confidence_score,
        original_rank=original_rank,
        reasons=tuple(reason for reason in reasons if reason),
    )


def _quality_score(result: SearchResult) -> tuple[int, int, tuple[str, ...]]:
    issues = detect_suspicious_result(
        listing_text=result.listing_text,
        raw_listing_text=result.raw_listing_text,
    )
    if not issues:
        return 0, 0, ("quality.clean",)

    severity = max(issue.severity for issue in issues)
    issue_reasons = tuple(f"suspicious.{issue.reason}" for issue in issues)
    if all(issue.reason == "missing_price_evidence" for issue in issues):
        return 1, severity, ("quality.missing_price", *issue_reasons)
    return 2, severity, ("quality.suspicious", *issue_reasons)


def _guardrail_score(
    result: SearchResult,
    *,
    query: str | None,
) -> tuple[int, tuple[str, ...]]:
    if not query:
        return 0, ()
    intent = classify_query_intent(query)
    if _missing_short_model_suffix_phrase(intent.required_descriptor_tokens, result):
        return 1, ("guardrail.brand_model_phrase_missing",)
    if intent.kind not in {"reference_with_descriptor", "reference_with_year"}:
        return 0, ()
    if not intent.required_descriptor_tokens:
        return 0, ()
    fuzzy = score_fuzzy_match(query, result.listing_text)
    if (
        fuzzy.reference_score >= 100
        and fuzzy.descriptor_overlap_score < 50
        and _has_required_descriptor_conflict(intent.required_descriptor_tokens, result)
    ):
        return 1, ("guardrail.descriptor_conflict",)
    return 0, ()


def _conflict_penalty_score(
    guardrail_reasons: tuple[str, ...],
) -> tuple[int, tuple[str, ...]]:
    if "guardrail.descriptor_conflict" in guardrail_reasons:
        return 1, ("conflict.descriptor",)
    return 0, ()


def _alias_confidence_score(query: str | None) -> tuple[int, tuple[str, ...]]:
    if not query:
        return 0, ()

    plan = _cached_query_plan(query)
    best_confidence = ""
    best_score = 0
    for candidate in plan.brand_candidates:
        confidence = candidate.get("confidence")
        if not isinstance(confidence, str):
            continue
        score = ALIAS_CONFIDENCE_SCORES.get(confidence, 0)
        if score > best_score:
            best_confidence = confidence
            best_score = score

    if best_score == 0:
        return 0, ()
    return best_score, (f"alias.{best_confidence}",)


@lru_cache(maxsize=256)
def _cached_query_plan(query: str):
    return build_query_plan(query)


def _missing_short_model_suffix_phrase(
    required_descriptor_tokens: tuple[str, ...],
    result: SearchResult,
) -> bool:
    numeric_suffixes = tuple(
        token for token in required_descriptor_tokens if token.isdigit() and len(token) == 1
    )
    if not numeric_suffixes:
        return False
    model_tokens = tuple(
        token for token in required_descriptor_tokens if not token.isdigit()
    )
    if not model_tokens:
        return False
    normalized_listing = normalize_text(result.listing_text)
    return not any(
        re.search(rf"\b{re.escape(model_token)}\s+{re.escape(suffix)}\b", normalized_listing)
        for model_token in model_tokens
        for suffix in numeric_suffixes
    )


def _has_required_descriptor_conflict(
    required_descriptor_tokens: tuple[str, ...],
    result: SearchResult,
) -> bool:
    required_colors = (
        canonicalize_descriptor_tokens_as_set(required_descriptor_tokens)
        & CANONICAL_COLOR_DESCRIPTOR_GROUP
    )
    if not required_colors:
        return False
    listing_colors = (
        canonicalize_descriptor_tokens_as_set(normalize_text(result.listing_text).split())
        & CANONICAL_COLOR_DESCRIPTOR_GROUP
    )
    return bool(listing_colors and required_colors.isdisjoint(listing_colors))


def _posted_date_score(value: str | None) -> tuple[int, float, str]:
    parsed = parse_posted_date(value)
    if parsed is None:
        return 1, 0.0, "date.missing_or_unparseable"
    return 0, parsed.timestamp(), "date.parsed"


def _relevance_score(
    result: SearchResult,
    *,
    query: str | None,
) -> tuple[int, int, tuple[str, ...]]:
    if not query:
        return 0, 0, ()

    trace = explain_extraction(query, result.listing_text)
    reasons: list[str] = []
    exact_reference_score = 0
    descriptor_score = 0

    if trace.selected_reference is not None:
        exact_reference_score = 1
        reasons.append("reference.selected")
    if trace.intent.descriptor_tokens and "descriptor.require_local_match" in trace.rule_ids:
        descriptor_score = len(trace.intent.descriptor_tokens)
        reasons.append("descriptor.local")

    return exact_reference_score, descriptor_score, tuple(reasons)


def _price_evidence_score(result: SearchResult) -> tuple[int, tuple[str, ...]]:
    reason = price_evidence_reason(result)
    if reason == "price.visible":
        return 1, (reason,)
    return 0, (reason,)


def price_evidence_reason(result: SearchResult) -> str:
    if result.price_reason in PRICE_REASON_CODES:
        return result.price_reason
    scan_text = KARAT_GOLD_RE.sub(" ", result.listing_text)
    if PRICE_EVIDENCE_RE.search(scan_text):
        return "price.visible"
    raw_text = result.raw_listing_text
    if raw_text and normalize_text(raw_text) != normalize_text(result.listing_text):
        raw_scan_text = KARAT_GOLD_RE.sub(" ", raw_text)
        if PRICE_EVIDENCE_RE.search(raw_scan_text):
            return "price.ambiguous_neighbor"
    return "price.missing_visible"


def _scope_confidence_score(result: SearchResult) -> tuple[int, tuple[str, ...]]:
    reason = scope_confidence_reason(result)
    return SCOPE_CONFIDENCE_SCORES[reason], (reason,)


def scope_confidence_reason(result: SearchResult) -> str:
    if result.scope_reason in SCOPE_CONFIDENCE_SCORES:
        return result.scope_reason
    raw_text = " ".join((result.raw_listing_text or "").split())
    listing_text = " ".join(result.listing_text.split())
    if not raw_text or raw_text == listing_text:
        return "scope.full_listing"
    if STOCK_LIST_MARKER_RE.search(raw_text) or _looks_like_unlabeled_stock_list(
        raw_text
    ):
        return "scope.stock_list"
    return "scope.scoped"


def _looks_like_unlabeled_stock_list(value: str) -> bool:
    references = {
        token.casefold()
        for token in SCOPE_REFERENCE_RE.findall(value)
        if _looks_like_scope_product_reference(token)
    }
    return len(references) > 1


def _looks_like_scope_product_reference(value: str) -> bool:
    normalized = value.casefold().strip(":,.;")
    if not normalized or not any(character.isdigit() for character in normalized):
        return False
    if normalized.isdigit():
        return False
    if SCOPE_CONDITION_DATE_RE.fullmatch(normalized):
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?[km]", normalized):
        return False
    if any(
        currency in normalized
        for currency in ("hkd", "usd", "usdt", "eur", "aed", "chf")
    ):
        return False
    if len(normalized) < 4 and "/" not in normalized:
        return False
    return True


def _image_confidence_score(
    result: SearchResult,
    *,
    scope_reason: str,
) -> tuple[int, tuple[str, ...]]:
    reason = image_confidence_reason(result, scope_reason=scope_reason)
    return IMAGE_CONFIDENCE_SCORES[reason], (reason,)


def image_confidence_reason(
    result: SearchResult,
    *,
    scope_reason: str | None = None,
) -> str:
    if result.image_reason in IMAGE_CONFIDENCE_SCORES:
        return result.image_reason
    if result.image_url:
        return "image.direct"
    if scope_reason is None:
        scope_reason = scope_confidence_reason(result)
    if scope_reason == "scope.stock_list":
        return "image.omitted_bundle_ambiguous"
    return "image.missing_source"


def parse_posted_date(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.split("·", maxsplit=1)[0].strip()
    for date_format in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(normalized[:19], date_format)
        except ValueError:
            continue
    return None
