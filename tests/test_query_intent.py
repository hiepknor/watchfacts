from __future__ import annotations

import pytest

from app.searching.query_intent import classify_query_intent


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
