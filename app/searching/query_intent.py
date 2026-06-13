from __future__ import annotations

from dataclasses import dataclass
import re

from app.searching.matcher_aliases import conflict_descriptor_tokens
from app.searching.matcher_normalization import tokenize_query
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
BRAND_ALIAS_PHRASES = (
    (("rolex",), "rolex"),
    (("patek", "philippe"), "patek_philippe"),
    (("patek",), "patek_philippe"),
    (("ap",), "audemars_piguet"),
    (("audemars", "piguet"), "audemars_piguet"),
    (("rm",), "richard_mille"),
    (("richard", "mille"), "richard_mille"),
)
COLLECTION_PHRASES = (
    (("daytona",), "daytona", "rolex"),
    (("submariner",), "submariner", "rolex"),
    (("gmt",), "gmt", "rolex"),
    (("gmt", "master"), "gmt_master", "rolex"),
    (("nautilus",), "nautilus", "patek_philippe"),
    (("aquanaut",), "aquanaut", "patek_philippe"),
    (("royal", "oak"), "royal_oak", "audemars_piguet"),
    (("offshore",), "offshore", "audemars_piguet"),
)
NICKNAME_PHRASES = (
    (("panda",), "panda", "rolex"),
    (("pepsi",), "pepsi", "rolex"),
    (("batman",), "batman", "rolex"),
    (("batgirl",), "batgirl", "rolex"),
    (("sprite",), "sprite", "rolex"),
    (("hulk",), "hulk", "rolex"),
    (("starbucks",), "starbucks", "rolex"),
    (("root", "beer"), "root_beer", "rolex"),
)
REFERENCE_GRAMMAR = (
    (re.compile(r"^12[68]500ln$|^116500ln$"), "rolex", "daytona"),
    (re.compile(r"^571[12]"), "patek_philippe", "nautilus"),
    (re.compile(r"^5167a?$"), "patek_philippe", "aquanaut"),
    (re.compile(r"^155(?:00|10)st$"), "audemars_piguet", "royal_oak"),
    (re.compile(r"^rm\d"), "richard_mille", None),
)


BrandCandidate = dict[str, object]


@dataclass(frozen=True)
class QueryIntentMetadata:
    kind: QueryIntentKind
    required_descriptor_tokens: tuple[str, ...]
    optional_descriptor_tokens: tuple[str, ...]
    reason_codes: tuple[str, ...]
    policy: dict[str, str]


@dataclass(frozen=True)
class QueryPlan:
    original_query: str
    canonical_query: str
    brand_candidates: tuple[BrandCandidate, ...]
    references: tuple[tuple[str, ...], ...]
    collections: tuple[str, ...]
    nicknames: tuple[str, ...]
    required_descriptors: tuple[str, ...]
    optional_descriptors: tuple[str, ...]
    conflict_descriptors: tuple[str, ...]
    intent_kind: QueryIntentKind
    reason_codes: tuple[str, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "original_query": self.original_query,
            "canonical_query": self.canonical_query,
            "brand_candidates": [
                {
                    "brand": candidate["brand"],
                    "confidence": candidate["confidence"],
                    "source_terms": list(candidate["source_terms"]),
                }
                for candidate in self.brand_candidates
            ],
            "references": [list(reference) for reference in self.references],
            "collections": list(self.collections),
            "nicknames": list(self.nicknames),
            "required_descriptors": list(self.required_descriptors),
            "optional_descriptors": list(self.optional_descriptors),
            "conflict_descriptors": list(self.conflict_descriptors),
            "intent_kind": self.intent_kind,
            "reason_codes": list(self.reason_codes),
        }


def build_query_plan(query: str) -> QueryPlan:
    intent = classify_query_intent(query)
    canonical_tokens = tuple(tokenize_query(query))
    reference_terms, _ = parse_query_terms(query)
    references = tuple(tuple(reference) for reference in reference_terms)

    brand_candidates: list[BrandCandidate] = []
    collections: list[str] = []
    nicknames: list[str] = []
    reasons: list[str] = list(intent.reason_codes)

    for phrase, brand in _phrase_matches(canonical_tokens, BRAND_ALIAS_PHRASES):
        if _add_brand_candidate(
            brand_candidates,
            brand=brand,
            confidence="explicit",
            source_terms=phrase,
        ):
            reasons.append(f"brand.explicit:{brand}")

    for reference in references:
        reference_text = "".join(reference)
        reference_brand, reference_collection = _brand_collection_for_reference(
            reference_text
        )
        if reference_brand and _add_brand_candidate(
            brand_candidates,
            brand=reference_brand,
            confidence="reference",
            source_terms=(reference_text,),
        ):
            reasons.append(f"brand.reference:{reference_brand}")
        if reference_collection and _append_unique(collections, reference_collection):
            reasons.append(f"collection.reference:{reference_collection}")

    for phrase, collection, brand in _phrase_matches(canonical_tokens, COLLECTION_PHRASES):
        if _append_unique(collections, collection):
            reasons.append(f"collection.present:{collection}")
        elif f"collection.present:{collection}" not in reasons:
            reasons.append(f"collection.present:{collection}")
        if _add_brand_candidate(
            brand_candidates,
            brand=brand,
            confidence="collection",
            source_terms=phrase,
        ):
            reasons.append(f"brand.collection:{brand}")

    for phrase, nickname, brand in _phrase_matches(canonical_tokens, NICKNAME_PHRASES):
        if _append_unique(nicknames, nickname):
            reasons.append(f"nickname.present:{nickname}")
        if _add_brand_candidate(
            brand_candidates,
            brand=brand,
            confidence="nickname",
            source_terms=phrase,
        ):
            reasons.append(f"brand.nickname:{brand}")

    conflicts = conflict_descriptor_tokens(intent.required_descriptor_tokens)
    for token in intent.required_descriptor_tokens:
        if conflict_descriptor_tokens((token,)):
            reasons.append(f"descriptor.conflict_group:{token}")

    return QueryPlan(
        original_query=query,
        canonical_query=" ".join(canonical_tokens),
        brand_candidates=tuple(brand_candidates),
        references=references,
        collections=tuple(collections),
        nicknames=tuple(nicknames),
        required_descriptors=intent.required_descriptor_tokens,
        optional_descriptors=intent.optional_descriptor_tokens,
        conflict_descriptors=conflicts,
        intent_kind=intent.kind,
        reason_codes=tuple(_dedupe_preserving_order(reasons)),
    )


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


def _brand_collection_for_reference(
    reference: str,
) -> tuple[str | None, str | None]:
    for pattern, brand, collection in REFERENCE_GRAMMAR:
        if pattern.search(reference):
            return brand, collection
    return None, None


def _phrase_matches(
    tokens: tuple[str, ...],
    phrases: tuple[tuple[tuple[str, ...], str], ...]
    | tuple[tuple[tuple[str, ...], str, str], ...],
):
    for phrase_data in phrases:
        phrase = phrase_data[0]
        if _contains_phrase(list(tokens), phrase):
            yield phrase_data


def _add_brand_candidate(
    candidates: list[BrandCandidate],
    *,
    brand: str,
    confidence: str,
    source_terms: tuple[str, ...],
) -> bool:
    if any(candidate["brand"] == brand for candidate in candidates):
        return False
    candidates.append(
        {
            "brand": brand,
            "confidence": confidence,
            "source_terms": source_terms,
        }
    )
    return True


def _append_unique(values: list[str], value: str) -> bool:
    if value in values:
        return False
    values.append(value)
    return True


def _dedupe_preserving_order(values: list[str]) -> tuple[str, ...]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        deduped.append(value)
        seen.add(value)
    return tuple(deduped)
