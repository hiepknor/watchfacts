from __future__ import annotations

import pytest

from app.query_intent import build_query_plan, classify_query_intent


def test_classify_reference_only_query() -> None:
    intent = classify_query_intent("5712r")

    assert intent.kind == "reference_only"
    assert intent.required_descriptor_tokens == ()
    assert intent.optional_descriptor_tokens == ()
    assert "reference.present" in intent.reason_codes
    assert intent.policy["fuzzy"] == "diagnostic"


def test_classify_reference_with_descriptor_query() -> None:
    intent = classify_query_intent("5205r green")

    assert intent.kind == "reference_with_descriptor"
    assert intent.required_descriptor_tokens == ("green",)
    assert intent.optional_descriptor_tokens == ()
    assert intent.policy["descriptor"] == "required"
    assert intent.policy["fuzzy"] == "warn_or_demote"


def test_classify_reference_with_year_query() -> None:
    intent = classify_query_intent("126500ln white 2026")

    assert intent.kind == "reference_with_year"
    assert intent.required_descriptor_tokens == ("white",)
    assert intent.optional_descriptor_tokens == ("2026",)
    assert intent.policy["year"] == "soft_demote"
    assert "year.present" in intent.reason_codes


def test_classify_brand_model_descriptor_query() -> None:
    intent = classify_query_intent("FPJ Elegante Titanium")

    assert intent.kind == "brand_model_descriptor"
    assert intent.required_descriptor_tokens == ("fpj", "elegante", "titanium")
    assert intent.policy["fuzzy"] == "strong_diagnostic"
    assert "reference.absent" in intent.reason_codes


def test_classify_common_brand_model_descriptor_query() -> None:
    intent = classify_query_intent("Rolex Daytona Panda")

    assert intent.kind == "brand_model_descriptor"
    assert intent.required_descriptor_tokens == ("rolex", "daytona", "panda")


@pytest.mark.parametrize(
    "query",
    [
        "Nautilus Tiffany",
        "Aquanaut green",
        "Royal Oak Offshore",
        "RM Rafael Nadal",
        "Ballon Bleu Cartier",
        "Overseas blue",
        "IWC Portugieser",
        "Omega Speedmaster",
        "Black Bay chrono",
        "Panerai Luminor",
        "Lange 1",
        "Reverso tribute",
    ],
)
def test_classify_popular_brand_model_descriptor_queries(query: str) -> None:
    intent = classify_query_intent(query)

    assert intent.kind == "brand_model_descriptor"
    assert intent.required_descriptor_tokens


@pytest.mark.parametrize(
    "query",
    [
        "black strap",
        "master condition",
        "pilot seller",
    ],
)
def test_classify_generic_two_word_queries_as_free_text(query: str) -> None:
    intent = classify_query_intent(query)

    assert intent.kind == "free_text"


def test_classify_free_text_query() -> None:
    intent = classify_query_intent("looking for something interesting")

    assert intent.kind == "free_text"
    assert intent.required_descriptor_tokens == ()
    assert intent.optional_descriptor_tokens == (
        "looking",
        "for",
        "something",
        "interesting",
    )


def test_build_query_plan_exposes_canonical_brand_reference_metadata() -> None:
    plan = build_query_plan("Rolex Daytona Panda 126500ln white 2026")

    assert plan.original_query == "Rolex Daytona Panda 126500ln white 2026"
    assert plan.canonical_query == "rolex daytona panda 126500ln white 2026"
    assert plan.intent_kind == "reference_with_year"
    assert plan.brand_candidates == (
        {
            "brand": "rolex",
            "confidence": "explicit",
            "source_terms": ("rolex",),
        },
    )
    assert plan.references == (("126500ln",),)
    assert plan.collections == ("daytona",)
    assert plan.nicknames == ("panda",)
    assert plan.required_descriptors == ("rolex", "daytona", "panda", "white")
    assert plan.optional_descriptors == ("2026",)
    assert plan.conflict_descriptors == ()
    assert "brand.explicit:rolex" in plan.reason_codes
    assert "collection.present:daytona" in plan.reason_codes
    assert "nickname.present:panda" in plan.reason_codes


def test_build_query_plan_serializes_to_safe_payload() -> None:
    payload = build_query_plan("rm07-01 rose gold").to_payload()

    assert payload == {
        "original_query": "rm07-01 rose gold",
        "canonical_query": "rm07-01 rg",
        "brand_candidates": [
            {
                "brand": "richard_mille",
                "confidence": "reference",
                "source_terms": ["rm07-01"],
            },
        ],
        "references": [["rm07-01"]],
        "collections": [],
        "nicknames": [],
        "required_descriptors": ["rg"],
        "optional_descriptors": [],
        "conflict_descriptors": ["wg"],
        "intent_kind": "reference_with_descriptor",
        "reason_codes": [
            "reference.present",
            "descriptor.present",
            "brand.reference:richard_mille",
            "descriptor.conflict_group:rg",
        ],
    }


def test_build_query_plan_infers_brand_collection_and_nickname() -> None:
    plan = build_query_plan("daytona panda")

    assert plan.intent_kind == "brand_model_descriptor"
    assert plan.brand_candidates == (
        {
            "brand": "rolex",
            "confidence": "collection",
            "source_terms": ("daytona",),
        },
    )
    assert plan.references == ()
    assert plan.collections == ("daytona",)
    assert plan.nicknames == ("panda",)
    assert plan.required_descriptors == ("daytona", "panda")


@pytest.mark.parametrize(
    ("query", "brand", "collection", "reference"),
    [
        ("5711 blue", "patek_philippe", "nautilus", "5711"),
        ("15500st blue", "audemars_piguet", "royal_oak", "15500st"),
    ],
)
def test_build_query_plan_infers_brand_from_reference_grammar(
    query: str,
    brand: str,
    collection: str,
    reference: str,
) -> None:
    plan = build_query_plan(query)

    assert plan.brand_candidates == (
        {
            "brand": brand,
            "confidence": "reference",
            "source_terms": (reference,),
        },
    )
    assert plan.references == ((reference,),)
    assert plan.collections == (collection,)
