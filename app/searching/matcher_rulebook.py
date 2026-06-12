from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


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
