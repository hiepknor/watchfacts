from __future__ import annotations

import asyncio
import json
import urllib.request

from app.config import Settings
from app.db import Database
from app.integrations.ai_refiner import (
    deterministic_refine_listing_text,
    evaluate_refinement_suggestion,
    refine_search_results,
    refine_listing_text,
    should_refine_listing_text,
    should_refine_search_result,
)
from app.runtime.telegram_bot import SearchResult


def test_refine_listing_text_accepts_relevant_substring() -> None:
    raw_text = (
        "FPJ quantieme perpetuel platinum 2022 used Fullset $298,500USD - [ ] "
        "FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd - [ ] "
        "FPJ Rose Gold CS opendate watch with card $130,000USD - [ ] Greubel Forsey"
    )

    async def complete(_: str) -> str:
        return json.dumps(
            {
                "relevant": True,
                "index": 0,
                "selected_text": (
                    "FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd"
                ),
                "confidence": 0.92,
                "reasons": ["match"],
                "risk_flags": [],
            }
        )

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
        return json.dumps(
            {
                "relevant": True,
                "index": 2,
                "selected_text": "",
                "confidence": 0.92,
                "reasons": ["match"],
                "risk_flags": [],
            }
        )

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


def test_refine_listing_text_strips_trailing_unrelated_token_after_llm_success() -> None:
    raw_text = "FPJ Elegante titanium 48mm 2019 fullset HKD785K tudor"

    async def complete(_: str) -> str:
        return json.dumps(
            {
                "relevant": True,
                "index": 1,
                "selected_text": "",
                "confidence": 0.92,
                "reasons": ["match"],
                "risk_flags": [],
            }
        )

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
        return json.dumps(
            {
                "relevant": True,
                "index": 0,
                "selected_text": "FPJ Elegante Titanium invented price",
                "confidence": 0.92,
                "reasons": ["match"],
                "risk_flags": [],
            }
        )

    refined = asyncio.run(
        refine_listing_text(
            "Fpj Elegante Titanium",
            raw_text,
            complete=complete,
        )
    )

    assert refined == raw_text


def test_refine_listing_text_rejects_low_confidence_output() -> None:
    raw_text = "FPJ Elegante Titanium 48mm 2019 fullset 780000 hkd"

    async def complete(_: str) -> str:
        return (
            '{"relevant": true, "index": 1, '
            '"selected_text": "FPJ Elegante Titanium 48mm 2019 fullset 780000 hkd", '
            '"confidence": 0.2, "reasons": ["weak"], "risk_flags": []}'
        )

    refined = asyncio.run(
        refine_listing_text(
            "Fpj Elegante Titanium",
            raw_text,
            complete=complete,
        )
    )

    assert refined == raw_text


def test_refine_listing_text_rejects_risk_flags() -> None:
    raw_text = "FPJ Elegante Titanium 48mm 2019 fullset 780000 hkd"

    async def complete(_: str) -> str:
        return (
            '{"relevant": true, "index": 1, '
            '"selected_text": "FPJ Elegante Titanium 48mm 2019 fullset 780000 hkd", '
            '"confidence": 0.9, "reasons": ["match"], "risk_flags": ["cross_item"]}'
        )

    refined = asyncio.run(
        refine_listing_text(
            "Fpj Elegante Titanium",
            raw_text,
            complete=complete,
        )
    )

    assert refined == raw_text


def test_refine_listing_text_rejects_cross_item_separator() -> None:
    raw_text = (
        "FPJ Elegante Titanium - [ ] "
        "FPJ Elegante Titanium Rolex Daytona"
    )

    async def complete(_: str) -> str:
            return (
                '{"relevant": true, "index": 0, '
                '"selected_text": "FPJ Elegante Titanium - [ ] FPJ Elegante Titanium Rolex Daytona", '
                '"confidence": 0.9, "reasons": ["match"], "risk_flags": []}'
            )

    refined = asyncio.run(
        refine_listing_text(
            "Fpj Elegante Titanium",
            raw_text,
            complete=complete,
        )
    )

    assert refined == "FPJ Elegante Titanium"


def test_refine_listing_text_rejects_overlong_output() -> None:
    fallback_text = "FPJ Elegante Titanium base"
    long_text = "FPJ Elegante Titanium " + ("detail " * 220)
    raw_text = f"{fallback_text} - [ ] {long_text}"

    async def complete(_: str) -> str:
        return json.dumps(
            {
                "relevant": True,
                "index": 0,
                "selected_text": long_text,
                "confidence": 0.9,
                "reasons": ["match"],
                "risk_flags": [],
            }
        )

    refined = asyncio.run(
        refine_listing_text(
            "Fpj Elegante Titanium",
            raw_text,
            complete=complete,
        )
    )

    assert refined == fallback_text


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


def test_refine_listing_text_rejects_missing_required_schema_fields() -> None:
    raw_text = "FPJ Elegante Titanium 48mm 2019 fullset 780000 hkd"

    async def complete(_: str) -> str:
        return '{"relevant": true, "selected_text": "FPJ Elegante Titanium 48mm 2019 fullset 780000 hkd"}'

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


def test_should_refine_search_result_detects_suspicious_missing_price() -> None:
    assert should_refine_search_result(
        SearchResult(
            "116500 panda Daytona 2017 full link retail ready",
            raw_listing_text="116500 panda Daytona 2017 full link retail ready 31750",
        )
    )


def test_deterministic_refine_listing_text_selects_clear_candidate_without_llm() -> None:
    raw_text = (
        "FPJ quantieme perpetuel platinum 2022 used Fullset $298,500USD - [ ] "
        "FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd - [ ] "
        "FPJ Rose Gold CS opendate watch with card $130,000USD"
    )

    refined = deterministic_refine_listing_text("Fpj Elegante Titanium", raw_text)

    assert refined == "FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd"


def test_evaluate_refinement_suggestion_accepts_traceable_query_match() -> None:
    gate = evaluate_refinement_suggestion(
        "Fpj Elegante Titanium",
        SearchResult(
            "FPJ quantieme - [ ] FPJ Elegante Titanium 120k",
            raw_listing_text="FPJ quantieme - [ ] FPJ Elegante Titanium 120k",
        ),
        SearchResult("FPJ Elegante Titanium 120k"),
    )

    assert gate.status == "accepted"
    assert gate.reasons == ("matches_query", "raw_substring")


def test_evaluate_refinement_suggestion_rejects_invented_or_mismatched_text() -> None:
    gate = evaluate_refinement_suggestion(
        "Fpj Elegante Titanium",
        SearchResult(
            "FPJ Elegante Titanium 120k",
            raw_listing_text="FPJ Elegante Titanium 120k",
        ),
        SearchResult("Patek Nautilus invented price"),
    )

    assert gate.status == "rejected"
    assert "query_mismatch" in gate.reasons
    assert "not_raw_substring" in gate.reasons


def test_evaluate_refinement_suggestion_rejects_cross_item_or_overlong_text() -> None:
    raw_text = (
        "FPJ Elegante Titanium 48mm 2019 fullset 780000 hkd - [ ] "
        f"FPJ Elegante Titanium {'detail ' * 220}"
    )
    separator_gate = evaluate_refinement_suggestion(
        "Fpj Elegante Titanium",
        SearchResult(raw_text, raw_listing_text=raw_text),
        SearchResult("FPJ Elegante Titanium 48mm 2019 fullset 780000 hkd - [ ] Rolex"),
    )
    long_text = "FPJ Elegante Titanium " + ("detail " * 220)
    length_gate = evaluate_refinement_suggestion(
        "Fpj Elegante Titanium",
        SearchResult(raw_text, raw_listing_text=raw_text),
        SearchResult(long_text),
    )

    assert separator_gate.status == "rejected"
    assert "crosses_item_separator" in separator_gate.reasons
    assert length_gate.status == "rejected"
    assert "exceeds_length" in length_gate.reasons


def test_refine_search_results_uses_database_cache(tmp_path) -> None:
    settings = Settings(
        telegram_bot_token="token",
        telegram_allowed_user_ids=(),
        telegram_result_limit=5,
        watchfacts_url="https://watchfacts.example/simon-match-making",
        headless=True,
        enable_crawl4ai=True,
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        logs_dir=tmp_path / "logs",
        db_path=tmp_path / "data" / "bot.db",
        browser_state_path=tmp_path / "data" / "watchfacts_state.json",
        hybrid_ai_mode="shadow",
        openai_api_key="sk-test",
        openai_model="test-model",
    )
    database = Database(settings.db_path)
    result = SearchResult(
        "FPJ quantieme perpetuel - [ ] FPJ Elegante Titanium - [ ] FPJ Rose Gold CS"
    )
    database.record_llm_refinement(
        "Fpj Elegante Titanium",
        result.listing_text,
        settings.openai_model,
        "FPJ Elegante Titanium",
    )

    refined = asyncio.run(
        refine_search_results(
            "Fpj Elegante Titanium",
            [result],
            settings,
            database=database,
        )
    )

    assert refined == [SearchResult("FPJ Elegante Titanium")]


def test_refine_search_results_uses_raw_text_for_suspicious_result(tmp_path, monkeypatch) -> None:
    settings = Settings(
        telegram_bot_token="token",
        telegram_allowed_user_ids=(),
        telegram_result_limit=5,
        watchfacts_url="https://watchfacts.example/simon-match-making",
        headless=True,
        enable_crawl4ai=True,
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        logs_dir=tmp_path / "logs",
        db_path=tmp_path / "data" / "bot.db",
        browser_state_path=tmp_path / "data" / "watchfacts_state.json",
        hybrid_ai_mode="guarded",
        openai_api_key="sk-test",
        openai_model="test-model",
    )
    result = SearchResult(
        "116500 panda Daytona 2017 full link retail ready",
        raw_listing_text="116500 panda Daytona 2017 full link retail ready 31750",
    )

    async def complete(prompt: str) -> str:
        assert "31750" in prompt
        return json.dumps(
            {
                "relevant": True,
                "index": 0,
                "selected_text": "116500 panda Daytona 2017 full link retail ready 31750",
                "confidence": 0.92,
                "reasons": ["adds traceable price"],
                "risk_flags": [],
            }
        )

    monkeypatch.setattr("app.integrations.ai_refiner._settings_complete", lambda _: complete)

    refined = asyncio.run(refine_search_results("116500 panda", [result], settings))

    assert refined == [
        SearchResult(
            "116500 panda Daytona 2017 full link retail ready 31750",
            raw_listing_text=result.raw_listing_text,
        )
    ]


def test_refine_search_results_falls_back_on_openai_timeout(tmp_path, monkeypatch) -> None:
    settings = Settings(
        telegram_bot_token="token",
        telegram_allowed_user_ids=(),
        telegram_result_limit=5,
        watchfacts_url="https://watchfacts.example/simon-match-making",
        headless=True,
        enable_crawl4ai=True,
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        logs_dir=tmp_path / "logs",
        db_path=tmp_path / "data" / "bot.db",
        browser_state_path=tmp_path / "data" / "watchfacts_state.json",
        hybrid_ai_mode="shadow",
        openai_api_key="sk-test",
        openai_model="test-model",
        openai_timeout_seconds=1,
    )
    result = SearchResult("FPJ Elegante titanium 48mm 2019 fullset HKD785K tudor")

    def fail_timeout(*args, **kwargs):
        raise TimeoutError

    monkeypatch.setattr(urllib.request, "urlopen", fail_timeout)

    refined = asyncio.run(refine_search_results("Fpj Elegante Titanium", [result], settings))

    assert refined == [SearchResult("FPJ Elegante titanium 48mm 2019 fullset HKD785K")]
