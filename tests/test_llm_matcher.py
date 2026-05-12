from __future__ import annotations

import asyncio

from app.llm_matcher import refine_listing_text, should_refine_listing_text


def test_refine_listing_text_accepts_relevant_substring() -> None:
    raw_text = (
        "FPJ quantieme perpetuel platinum 2022 used Fullset $298,500USD - [ ] "
        "FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd - [ ] "
        "FPJ Rose Gold CS opendate watch with card $130,000USD - [ ] Greubel Forsey"
    )

    async def complete(_: str) -> str:
        return '{"relevant": true, "listing_text": "FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd"}'

    refined = asyncio.run(
        refine_listing_text(
            "Fpj Elegante Titanium",
            raw_text,
            complete=complete,
        )
    )

    assert refined == "FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd"


def test_refine_listing_text_accepts_candidate_index() -> None:
    raw_text = (
        "FPJ quantieme perpetuel platinum 2022 used Fullset $298,500USD - [ ] "
        "FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd - [ ] "
        "FPJ Rose Gold CS opendate watch with card $130,000USD"
    )

    async def complete(_: str) -> str:
        return '{"relevant": true, "index": 2}'

    refined = asyncio.run(
        refine_listing_text(
            "Fpj Elegante Titanium",
            raw_text,
            complete=complete,
        )
    )

    assert refined == "FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd"


def test_refine_listing_text_falls_back_to_best_candidate_on_llm_failure() -> None:
    raw_text = (
        "FPJ quantieme perpetuel platinum 2022 used Fullset $298,500USD - [ ] "
        "FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd - [ ] "
        "FPJ Rose Gold CS opendate watch with card $130,000USD"
    )

    async def complete(_: str) -> str:
        raise TimeoutError

    refined = asyncio.run(
        refine_listing_text(
            "Fpj Elegante Titanium",
            raw_text,
            complete=complete,
        )
    )

    assert refined == "FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd"


def test_refine_listing_text_strips_trailing_unrelated_token_on_llm_failure() -> None:
    raw_text = "FPJ Elegante titanium 48mm 2019 fullset HKD785K tudor"

    async def complete(_: str) -> str:
        raise TimeoutError

    refined = asyncio.run(
        refine_listing_text(
            "Fpj Elegante Titanium",
            raw_text,
            complete=complete,
        )
    )

    assert refined == "FPJ Elegante titanium 48mm 2019 fullset HKD785K"


def test_refine_listing_text_rejects_non_substring_output() -> None:
    raw_text = "FPJ Elegante Titanium 48mm 2019 fullset 780000 hkd"

    async def complete(_: str) -> str:
        return '{"relevant": true, "listing_text": "FPJ Elegante Titanium invented price"}'

    refined = asyncio.run(
        refine_listing_text(
            "Fpj Elegante Titanium",
            raw_text,
            complete=complete,
        )
    )

    assert refined == raw_text


def test_refine_listing_text_falls_back_on_invalid_json() -> None:
    raw_text = "FPJ Elegante titanium ti 48mm"

    async def complete(_: str) -> str:
        return "not json"

    refined = asyncio.run(
        refine_listing_text(
            "Fpj Elegante Titanium",
            raw_text,
            complete=complete,
        )
    )

    assert refined == raw_text


def test_should_refine_listing_text_detects_multi_item_snippets() -> None:
    assert should_refine_listing_text(
        "FPJ quantieme perpetuel - [ ] FPJ Elegante Titanium - [ ] FPJ Rose Gold CS"
    )
    assert should_refine_listing_text(
        "FPJ Elegante titanium 48mm 2019 fullset HKD785K tudor"
    )
    assert not should_refine_listing_text(
        "FPJ Elegante Titanium 48mm 2019 full set hkd800k"
    )
