from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Pattern


class RuleGroup(str, Enum):
    QUERY = "query"
    REFERENCE = "reference"
    DESCRIPTOR = "descriptor"
    PRICE = "price"
    DATE_CONDITION = "date_condition"
    PRODUCT_BOUNDARY = "product_boundary"
    METADATA_BOUNDARY = "metadata_boundary"
    NOISE = "noise"
    CLEANUP = "cleanup"


@dataclass(frozen=True)
class RuleSpec:
    group: RuleGroup
    priority: int
    rule_id: str
    description: str


@dataclass(frozen=True)
class BrandAliasRule:
    phrase: tuple[str, ...]
    brand: str


@dataclass(frozen=True)
class CollectionRule:
    phrase: tuple[str, ...]
    collection: str
    brand: str


@dataclass(frozen=True)
class NicknameRule:
    phrase: tuple[str, ...]
    nickname: str
    brand: str


@dataclass(frozen=True)
class ReferenceGrammarRule:
    pattern: Pattern[str]
    brand: str
    collection: str | None


@dataclass(frozen=True)
class RetrievalExpansionRule:
    collection: str
    retrieval_queries: tuple[str, ...]
    local_filter_queries: tuple[str, ...]
    reason_code: str
    nickname: str | None = None
    reference_terms: tuple[str, ...] = ()
    required_descriptors: tuple[str, ...] = ()
    allowed_extra_descriptors: tuple[str, ...] = ()
    requires_reference_absent: bool = False
    requires_optional_descriptor_absent: bool = True


@dataclass(frozen=True)
class QueryIntent:
    reference_terms: tuple[tuple[str, ...], ...]
    descriptor_tokens: tuple[str, ...]


@dataclass(frozen=True)
class ExtractionTrace:
    query: str
    intent: QueryIntent
    selected_reference: tuple[str, ...] | None
    matched_token_span: tuple[int, int] | None
    selected_char_span: tuple[int, int] | None
    rule_ids: tuple[str, ...]
    output_text: str


RULEBOOK: tuple[RuleSpec, ...] = (
    RuleSpec(
        RuleGroup.QUERY,
        10,
        "query.parse_intent",
        "Split query into reference terms and descriptor tokens.",
    ),
    RuleSpec(
        RuleGroup.REFERENCE,
        20,
        "reference.match_exact_or_compact",
        "Match references exactly, with compact punctuation-tolerant fallback.",
    ),
    RuleSpec(
        RuleGroup.DESCRIPTOR,
        30,
        "descriptor.require_local_match",
        "Require query descriptors near the selected reference when present.",
    ),
    RuleSpec(
        RuleGroup.PRICE,
        40,
        "price.keep_prefix",
        "Keep compact/keycap price prefixes immediately before a reference.",
    ),
    RuleSpec(
        RuleGroup.PRODUCT_BOUNDARY,
        50,
        "boundary.stop_next_product",
        "Stop extraction before the next product brand, header, or reference.",
    ),
    RuleSpec(
        RuleGroup.METADATA_BOUNDARY,
        60,
        "boundary.stop_metadata",
        "Stop extraction before seller, member, location, or service metadata.",
    ),
    RuleSpec(
        RuleGroup.DATE_CONDITION,
        70,
        "date_condition.keep_detail",
        "Keep date, year, condition, size, link, caliber, and stock-code details.",
    ),
    RuleSpec(
        RuleGroup.NOISE,
        80,
        "noise.reject_non_sale_request",
        "Reject WTB/looking-for listings from sale results.",
    ),
    RuleSpec(
        RuleGroup.CLEANUP,
        90,
        "cleanup.display_text",
        "Normalize whitespace and trim trailing section markers.",
    ),
)


RULES_BY_ID: dict[str, RuleSpec] = {rule.rule_id: rule for rule in RULEBOOK}


BRAND_ALIAS_RULES: tuple[BrandAliasRule, ...] = (
    BrandAliasRule(("rolex",), "rolex"),
    BrandAliasRule(("patek", "philippe"), "patek_philippe"),
    BrandAliasRule(("patek",), "patek_philippe"),
    BrandAliasRule(("ap",), "audemars_piguet"),
    BrandAliasRule(("audemars", "piguet"), "audemars_piguet"),
    BrandAliasRule(("rm",), "richard_mille"),
    BrandAliasRule(("richard", "mille"), "richard_mille"),
)


COLLECTION_RULES: tuple[CollectionRule, ...] = (
    CollectionRule(("daytona",), "daytona", "rolex"),
    CollectionRule(("submariner",), "submariner", "rolex"),
    CollectionRule(("gmt",), "gmt", "rolex"),
    CollectionRule(("gmt", "master"), "gmt_master", "rolex"),
    CollectionRule(("nautilus",), "nautilus", "patek_philippe"),
    CollectionRule(("aquanaut",), "aquanaut", "patek_philippe"),
    CollectionRule(("royal", "oak"), "royal_oak", "audemars_piguet"),
    CollectionRule(("offshore",), "offshore", "audemars_piguet"),
)


NICKNAME_RULES: tuple[NicknameRule, ...] = (
    NicknameRule(("panda",), "panda", "rolex"),
    NicknameRule(("pepsi",), "pepsi", "rolex"),
    NicknameRule(("batman",), "batman", "rolex"),
    NicknameRule(("batgirl",), "batgirl", "rolex"),
    NicknameRule(("sprite",), "sprite", "rolex"),
    NicknameRule(("hulk",), "hulk", "rolex"),
    NicknameRule(("starbucks",), "starbucks", "rolex"),
    NicknameRule(("root", "beer"), "root_beer", "rolex"),
)


RETRIEVAL_EXPANSION_RULES: tuple[RetrievalExpansionRule, ...] = (
    RetrievalExpansionRule(
        collection="daytona",
        retrieval_queries=(
            "daytona white",
            "126500ln white",
            "116500ln white",
        ),
        local_filter_queries=(
            "daytona white",
            "126500ln white",
            "116500ln white",
        ),
        reason_code="retrieval.nickname_expansion:panda",
        nickname="panda",
        requires_reference_absent=True,
    ),
    RetrievalExpansionRule(
        collection="nautilus",
        retrieval_queries=(
            "5711",
            "nautilus 5711 blue",
        ),
        local_filter_queries=("5711 blue",),
        reason_code="retrieval.collection_expansion:nautilus",
        reference_terms=("5711",),
        required_descriptors=("blue",),
        allowed_extra_descriptors=("nautilus", "patek", "philippe", "pp"),
    ),
)


REFERENCE_GRAMMAR_RULES: tuple[ReferenceGrammarRule, ...] = (
    ReferenceGrammarRule(
        re.compile(r"^12[68]500ln$|^116500ln$"),
        "rolex",
        "daytona",
    ),
    ReferenceGrammarRule(re.compile(r"^571[12]"), "patek_philippe", "nautilus"),
    ReferenceGrammarRule(re.compile(r"^5167a?$"), "patek_philippe", "aquanaut"),
    ReferenceGrammarRule(
        re.compile(r"^155(?:00|10)st$"),
        "audemars_piguet",
        "royal_oak",
    ),
    ReferenceGrammarRule(re.compile(r"^rm\d"), "richard_mille", None),
)
