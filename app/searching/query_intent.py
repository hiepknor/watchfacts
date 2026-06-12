from __future__ import annotations

from dataclasses import dataclass

from app.searching.matcher_token_classification import looks_like_year_token, parse_query_terms


QueryIntentKind = str
BRAND_DESCRIPTOR_TOKENS = {
    "ap",
    "audemars",
    "cartier",
    "fpj",
    "iwc",
    "jaeger",
    "journe",
    "lange",
    "lecoultre",
    "mille",
    "omega",
    "panerai",
    "patek",
    "richard",
    "rm",
    "rolex",
    "tudor",
    "vacheron",
    "vc",
}
MODEL_FAMILY_DESCRIPTOR_TOKENS = {
    "1815",
    "aquanaut",
    "aquatimer",
    "baignoire",
    "ballon",
    "bay",
    "bleu",
    "calatrava",
    "clair",
    "code",
    "constellation",
    "control",
    "datejust",
    "daydate",
    "daytona",
    "deepsea",
    "deville",
    "duometre",
    "egerie",
    "elegante",
    "explorer",
    "fiftysix",
    "gmt",
    "historiques",
    "luminor",
    "malte",
    "master",
    "metiers",
    "nautilus",
    "oak",
    "odysseus",
    "offshore",
    "overseas",
    "panda",
    "panthere",
    "patrimony",
    "pelagos",
    "pilot",
    "polaris",
    "portofino",
    "portugieser",
    "radiomir",
    "ranger",
    "rendez",
    "reverso",
    "royal",
    "santos",
    "saxonia",
    "seamaster",
    "speedmaster",
    "submariner",
    "submersible",
    "tank",
    "titanium",
    "traditionnelle",
    "vous",
    "yacht",
    "zeitwerk",
}
HIGH_CONFIDENCE_MODEL_TOKENS = {
    "aquanaut",
    "calatrava",
    "daydate",
    "datejust",
    "daytona",
    "luminor",
    "nautilus",
    "overseas",
    "pelagos",
    "portugieser",
    "radiomir",
    "reverso",
    "seamaster",
    "speedmaster",
    "submariner",
    "submersible",
}
MODEL_FAMILY_PHRASES = {
    ("ballon", "bleu"),
    ("black", "bay"),
    ("code", "1159"),
    ("gmt", "master"),
    ("master", "control"),
    ("pilot", "watch"),
    ("royal", "oak"),
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

    if (
        has_reference
        and set(descriptor_tokens) & BRAND_DESCRIPTOR_TOKENS
        and _reference_terms_are_short_model_suffixes(reference_terms)
    ):
        return QueryIntentMetadata(
            kind="brand_model_descriptor",
            required_descriptor_tokens=(
                *descriptor_tokens,
                *_flatten_reference_terms(reference_terms),
            ),
            optional_descriptor_tokens=(),
            reason_codes=tuple((*reasons, "reference.short_model_suffix")),
            policy={
                "descriptor": "required",
                "year": "ignore",
                "fuzzy": "strong_diagnostic",
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
    if len(descriptor_tokens) < 2:
        return False
    token_set = set(descriptor_tokens)
    if token_set & BRAND_DESCRIPTOR_TOKENS:
        return True
    if token_set & HIGH_CONFIDENCE_MODEL_TOKENS:
        return True
    if any(_contains_phrase(descriptor_tokens, phrase) for phrase in MODEL_FAMILY_PHRASES):
        return True
    return len(token_set & MODEL_FAMILY_DESCRIPTOR_TOKENS) >= 2


def _contains_phrase(tokens: list[str], phrase: tuple[str, ...]) -> bool:
    if len(tokens) < len(phrase):
        return False
    return any(
        tuple(tokens[index : index + len(phrase)]) == phrase
        for index in range(len(tokens) - len(phrase) + 1)
    )


def _reference_terms_are_short_model_suffixes(
    reference_terms: list[list[str]],
) -> bool:
    return all(
        all(part.isdigit() and len(part) <= 4 for part in reference_term)
        for reference_term in reference_terms
    )


def _flatten_reference_terms(reference_terms: list[list[str]]) -> tuple[str, ...]:
    return tuple(part for reference_term in reference_terms for part in reference_term)
