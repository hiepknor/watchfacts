from __future__ import annotations

from dataclasses import dataclass

from app.matcher_token_classification import looks_like_year_token, parse_query_terms


QueryIntentKind = str
BRAND_MODEL_DESCRIPTOR_TOKENS = {
    "ap",
    "audemars",
    "cartier",
    "daytona",
    "elegante",
    "fpj",
    "iwc",
    "journe",
    "lange",
    "mille",
    "omega",
    "panerai",
    "panda",
    "patek",
    "richard",
    "rm",
    "rolex",
    "titanium",
    "tudor",
    "vacheron",
    "vc",
}


@dataclass(frozen=True)
class QueryIntentMetadata:
    kind: QueryIntentKind
    required_descriptor_tokens: tuple[str, ...]
    optional_descriptor_tokens: tuple[str, ...]
    reason_codes: tuple[str, ...]
    policy: dict[str, str]


def classify_query_intent(query: str) -> QueryIntentMetadata:
    reference_terms, descriptor_tokens = parse_query_terms(query)
    year_tokens = tuple(token for token in descriptor_tokens if looks_like_year_token(token))
    non_year_descriptors = tuple(
        token for token in descriptor_tokens if token not in set(year_tokens)
    )
    has_reference = bool(reference_terms)
    reasons: list[str] = ["reference.present" if has_reference else "reference.absent"]
    if descriptor_tokens:
        reasons.append("descriptor.present")
    else:
        reasons.append("descriptor.absent")
    if year_tokens:
        reasons.append("year.present")

    if has_reference and not descriptor_tokens:
        return QueryIntentMetadata(
            kind="reference_only",
            required_descriptor_tokens=(),
            optional_descriptor_tokens=(),
            reason_codes=tuple(reasons),
            policy={
                "descriptor": "optional",
                "year": "ignore",
                "fuzzy": "diagnostic",
            },
        )

    if has_reference and year_tokens:
        return QueryIntentMetadata(
            kind="reference_with_year",
            required_descriptor_tokens=non_year_descriptors,
            optional_descriptor_tokens=year_tokens,
            reason_codes=tuple(reasons),
            policy={
                "descriptor": "required" if non_year_descriptors else "optional",
                "year": "soft_demote",
                "fuzzy": "warn_or_demote",
            },
        )

    if has_reference:
        return QueryIntentMetadata(
            kind="reference_with_descriptor",
            required_descriptor_tokens=tuple(descriptor_tokens),
            optional_descriptor_tokens=(),
            reason_codes=tuple(reasons),
            policy={
                "descriptor": "required",
                "year": "ignore",
                "fuzzy": "warn_or_demote",
            },
        )

    if _looks_like_brand_model_descriptor(descriptor_tokens):
        return QueryIntentMetadata(
            kind="brand_model_descriptor",
            required_descriptor_tokens=tuple(descriptor_tokens),
            optional_descriptor_tokens=(),
            reason_codes=tuple(reasons),
            policy={
                "descriptor": "required",
                "year": "ignore",
                "fuzzy": "strong_diagnostic",
            },
        )

    return QueryIntentMetadata(
        kind="free_text",
        required_descriptor_tokens=(),
        optional_descriptor_tokens=tuple(descriptor_tokens),
        reason_codes=tuple(reasons),
        policy={
            "descriptor": "optional",
            "year": "ignore",
            "fuzzy": "diagnostic",
        },
    )


def _looks_like_brand_model_descriptor(descriptor_tokens: list[str]) -> bool:
    return len(descriptor_tokens) >= 2 and any(
        token in BRAND_MODEL_DESCRIPTOR_TOKENS
        for token in descriptor_tokens
    )
