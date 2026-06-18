from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

import app.search as search_module
from app.config import Settings
from app.db import Database
from app.parser import ListingCandidate
from app.scraper import ScrapeResult
from app.search import SEARCH_CACHE_VERSION, WatchFactsSearchWorkflow
from app.telegram_bot import SearchResult


FIXTURE = Path(__file__).parent / "fixtures" / "watchfacts_listing.html"


def make_settings(tmp_path) -> Settings:
    return Settings(
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
    )


def test_search_workflow_scrapes_parses_matches_dedupes_and_persists(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = FIXTURE.read_text()
    fetch_calls: list[tuple[Settings, str | None]] = []

    async def fetch_html(received_settings: Settings, *, query: str | None = None) -> ScrapeResult:
        fetch_calls.append((received_settings, query))
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )

    results = asyncio.run(workflow.search("228253a choco"))

    assert fetch_calls == [(settings, "228253a")]
    assert len(results) == 1
    assert results[0].listing_text == "Rolex 228253A choco N2 467000hkd"
    assert results[0].seller == "HK STOCKS"
    assert results[0].image_url == "https://watchfacts.example/images/228253a.jpg"
    assert workflow.last_search_diagnostics is not None
    diagnostics_payload = workflow.last_search_diagnostics.to_payload()
    assert isinstance(diagnostics_payload["fuzzy_score_min"], int)
    assert 0 <= diagnostics_payload["fuzzy_score_min"] <= 100
    assert isinstance(diagnostics_payload["fuzzy_score_avg"], float)
    assert 0 <= diagnostics_payload["fuzzy_score_avg"] <= 100
    assert diagnostics_payload["stage_timings_ms"].keys() >= {
        "cache_read",
        "watchfacts_fetch",
        "parse",
        "match",
        "result_pipeline",
        "result_convert",
        "result_audit_converted",
        "result_dedupe_latest",
        "result_dedupe_text",
        "result_rank",
        "result_filter_blocked",
        "result_group_similar",
        "result_fuzzy_scores",
        "result_audit_final",
        "result_retrieval_contributions",
        "persist",
        "total",
    }
    assert all(
        isinstance(value, int) and value >= 0
        for value in diagnostics_payload["stage_timings_ms"].values()
    )
    diagnostics_payload = {
        key: value
        for key, value in diagnostics_payload.items()
        if key
        not in {
            "fuzzy_score_min",
            "fuzzy_score_avg",
            "stage_timings_ms",
            "retrieval_timings",
        }
    }
    assert diagnostics_payload == {
        "raw_candidate_count": 1,
        "parsed_count": 2,
        "matched_count": 1,
        "search_result_count": 1,
        "unique_latest_count": 1,
        "unique_text_count": 1,
        "deduped_drop_count": 0,
        "weak_match_count": 0,
        "ambiguous_candidate_count": 0,
        "query_intent": "reference_with_descriptor",
        "query_plan": {
            "original_query": "228253a choco",
            "canonical_query": "228253a choco",
            "brand_candidates": [],
            "references": [["228253a"]],
            "collections": [],
            "nicknames": [],
            "required_descriptors": ["choco"],
            "optional_descriptors": [],
            "conflict_descriptors": [],
            "intent_kind": "reference_with_descriptor",
            "reason_codes": [
                "reference.present",
                "descriptor.present",
            ],
        },
        "retrieval_query_count": 1,
        "retrieval_queries": ["228253a"],
        "retrieval_reason_codes": ["retrieval.reference_with_descriptors"],
        "required_descriptor_tokens": ["choco"],
        "optional_descriptor_tokens": [],
        "intent_reason_codes": [
            "reference.present",
            "descriptor.present",
        ],
        "guardrail_action_counts": {},
        "final_count": 1,
        "server_filtered": False,
        "playwright_fallback": False,
        "cache_hit": False,
        "source_truncation_suspected": False,
        "rejection_reasons": {
            "dedupe.latest_listing": 0,
            "dedupe.text": 0,
            "guardrail.blocked_final": 0,
        },
    }
    assert [event.stage for event in workflow.last_search_audit_events] == [
        "raw",
        "parsed",
        "parsed",
        "matched",
        "converted",
        "final",
    ]

    with sqlite3.connect(settings.db_path) as connection:
        query_row = connection.execute(
            "SELECT query_text, normalized_query, result_count FROM queries"
        ).fetchone()
        listing_count = connection.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        result_count = connection.execute(
            "SELECT COUNT(*) FROM query_results"
        ).fetchone()[0]

    assert query_row == ("228253a choco", "228253a choco", 1)
    assert listing_count == 1
    assert result_count == 1


def test_search_workflow_records_quality_metrics(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = '''
    {
      "listings": [
        {
          "title": "15510OR black",
          "companyName": "Seller Black",
          "number": 111,
          "frontImage": "https://watchfacts.example/15510or-black.jpg"
        },
        {
          "title": "15510OR blue",
          "companyName": "Seller Blue",
          "number": 222
        }
      ]
    }
    '''

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url=settings.watchfacts_url,
            server_filtered=True,
            used_playwright_fallback=True,
        )

    workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )

    workflow_results = asyncio.run(workflow.search("15510or"))

    assert len(workflow_results) == 2

    with sqlite3.connect(settings.db_path) as connection:
        query_row = connection.execute(
            """
            SELECT
              query_text,
              result_count,
              image_missing_count,
              server_filtered_hit_count,
              playwright_fallback_count
            FROM queries
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert query_row == ("15510or", 2, 1, 1, 1)


def test_search_workflow_audits_weak_and_ambiguous_match_candidates() -> None:
    events: list[search_module.SearchAuditEvent] = []
    weak_listing = ListingCandidate("Patek 5205R black dial 2026 415.000 HKD")
    ambiguous_listing = ListingCandidate("Patek 5205R green New 2026 415.000 HKD")

    weak_count, ambiguous_count = WatchFactsSearchWorkflow._audit_match_confidence(
        events,
        query="5205r green",
        parsed=[weak_listing, ambiguous_listing],
        matched=[weak_listing],
    )

    assert weak_count == 1
    assert ambiguous_count == 1
    assert [event.stage for event in events] == ["weak_match", "ambiguous_candidate"]
    assert "weak.descriptor_overlap_low" in events[0].reason_codes
    assert events[0].decision == "demote"
    assert events[0].query_intent == "reference_with_descriptor"
    assert events[0].fuzzy_score is not None
    assert events[0].guardrail_action == "demote"
    assert "ambiguous.not_deterministic_match" in events[1].reason_codes
    assert events[1].decision == "ambiguous"
    assert events[1].guardrail_action == "warn"


def test_search_workflow_audits_dedupe_drop_keep_reference() -> None:
    events: list[search_module.SearchAuditEvent] = []
    older = SearchResult(
        "Patek 5712G Used 2015 76k usdt",
        seller="Issac",
        posted_date="May 1, 2026",
    )
    newer = SearchResult(
        "Patek 5712G Used 2015 76k usdt",
        seller="Issac",
        posted_date="June 1, 2026",
    )

    dropped = WatchFactsSearchWorkflow._audit_dedupe_drops(
        events,
        query="5712g",
        before=[older, newer],
        after=[newer],
        key_for_result=lambda result: result.listing_text,
        reason_code="dedupe.text",
    )

    assert dropped == 1
    assert events[0].stage == "dedupe_drop"
    assert events[0].decision == "deduped"
    assert events[0].kept_audit_id is not None
    assert "dedupe.text" in events[0].reason_codes
    assert any(reason.startswith("kept_audit_id:") for reason in events[0].reason_codes)


def test_search_workflow_preserves_seller_phone_from_watchfacts_json(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "5712G Used 2015 - 76k usdt",
          "companyName": "Issac",
          "companyWhatsapp": "17826241887",
          "number": 3074930,
          "createdOn": "2026-06-02 03:18:08",
          "listings": [
            {
              "title": "5712G Used 2015 - 76k usdt",
              "frontImage": "https://watchfacts.example/5712g.jpg"
            }
          ]
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )

    results = asyncio.run(workflow.search("5712g"))

    assert len(results) == 1
    assert results[0].seller == "Issac"
    assert results[0].seller_phone == "17826241887"


def test_search_result_cache_roundtrip_preserves_evidence_metadata() -> None:
    result = SearchResult(
        "5712g new 2024 -> 115k",
        raw_listing_text=(
            "HK STOCK LIST 116505 rainbow 284k "
            "5712g new 2024 -> 115k 5726/1A used 2022 68k"
        ),
        scope_reason="scope.stock_list",
        image_reason="image.omitted_bundle_ambiguous",
        price_reason="price.visible",
        segment_reason_codes=(
            "segment.stock_list_marker",
            "segment.reference_boundary",
        ),
    )

    roundtripped = search_module._deserialize_results(
        search_module._serialize_results([result])
    )

    assert roundtripped == [result]


def test_search_workflow_serves_repeated_query_from_cache(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = FIXTURE.read_text()
    fetch_count = 0

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        nonlocal fetch_count
        fetch_count += 1
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )

    first = asyncio.run(workflow.search("228253a choco"))
    second = asyncio.run(workflow.search("  228253A   CHOCO "))

    assert fetch_count == 1
    assert second == first
    assert workflow.last_search_diagnostics is not None
    assert workflow.last_search_diagnostics.cache_hit is True
    assert workflow.last_search_diagnostics.final_count == len(second)
    assert workflow.last_search_diagnostics.parsed_count is None
    assert workflow.last_search_diagnostics.source_truncation_suspected is None
    assert workflow.last_search_diagnostics.stage_timings_ms is not None
    assert workflow.last_search_diagnostics.stage_timings_ms.keys() >= {
        "cache_read",
        "persist",
        "total",
    }
    assert workflow.last_search_audit_events == ()
    with sqlite3.connect(settings.db_path) as connection:
        query_count = connection.execute("SELECT COUNT(*) FROM queries").fetchone()[0]
        cache_count = connection.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0]

    assert query_count == 2
    assert cache_count == 1


def test_search_workflow_serves_descriptor_alias_query_from_cache(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "RM07-01 RG Snow used fullset",
          "companyName": "HK STOCKS",
          "number": 701
        }
      ]
    }
    """
    fetch_queries: list[str | None] = []

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        fetch_queries.append(query)
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )

    first = asyncio.run(workflow.search("rm07-01 rose gold"))
    second = asyncio.run(workflow.search("rm07-01 rg"))

    assert fetch_queries == ["rm07-01"]
    assert second == first
    assert workflow.last_search_diagnostics is not None
    assert workflow.last_search_diagnostics.cache_hit is True
    assert workflow.last_search_diagnostics.query_plan is not None
    assert workflow.last_search_diagnostics.query_plan.original_query == "rm07-01 rg"
    assert workflow.last_search_diagnostics.query_plan.canonical_query == "rm07-01 rg"
    with sqlite3.connect(settings.db_path) as connection:
        query_rows = connection.execute(
            "SELECT query_text, normalized_query, result_count FROM queries "
            "ORDER BY id"
        ).fetchall()
        cache_count = connection.execute(
            "SELECT COUNT(*) FROM search_cache"
        ).fetchone()[0]

    assert query_rows == [
        ("rm07-01 rose gold", "rm07-01 rose gold", 1),
        ("rm07-01 rg", "rm07-01 rg", 1),
    ]
    assert cache_count == 1


def test_search_workflow_refetches_after_cache_expiry(tmp_path) -> None:
    settings = replace(make_settings(tmp_path), search_retrieval_branch_cache_ttl_seconds=0)
    html = FIXTURE.read_text()
    fetch_count = 0

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        nonlocal fetch_count
        fetch_count += 1
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )

    asyncio.run(workflow.search("228253a choco"))
    with sqlite3.connect(settings.db_path) as connection:
        connection.execute(
            "UPDATE search_cache SET expires_at = '2000-01-01T00:00:00+00:00'"
        )
    asyncio.run(workflow.search("228253a choco"))

    assert fetch_count == 2


def test_search_cache_key_includes_search_cache_version(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path)

    first_key = search_module._search_cache_key("5712g", settings)
    monkeypatch.setattr(search_module, "SEARCH_CACHE_VERSION", f"{SEARCH_CACHE_VERSION}-test")
    second_key = search_module._search_cache_key("5712g", settings)

    assert first_key != second_key


def test_search_cache_key_uses_canonical_descriptor_aliases(tmp_path) -> None:
    settings = make_settings(tmp_path)

    rg_key = search_module._search_cache_key("rm07-01 rg", settings)

    assert search_module._search_cache_key("rm07-01 rose gold", settings) == rg_key
    assert search_module._search_cache_key("rm07-01 rosegold", settings) == rg_key
    assert (
        search_module._search_cache_key("rm07-01 mother of pearl", settings)
        == search_module._search_cache_key("rm07-01 mop", settings)
    )
    assert search_module._search_cache_key("rm07-01 wg", settings) != rg_key


def test_attribute_product_image_marks_direct_listing_image() -> None:
    attribution = search_module.attribute_product_image(
        ListingCandidate(
            listing_text="Patek 5712G New 2024 115k",
            image_url="https://watchfacts.example/5712g.jpg",
        ),
        query="5712g",
    )

    assert attribution.image_url == "https://watchfacts.example/5712g.jpg"
    assert attribution.reason == "image.direct"


def test_attribute_product_image_marks_missing_source() -> None:
    attribution = search_module.attribute_product_image(
        ListingCandidate(listing_text="Patek 5712G New 2024 115k"),
        query="5712g",
    )

    assert attribution.image_url is None
    assert attribution.reason == "image.missing_source"


def test_attribute_product_image_inherits_parent_image_for_first_color_scoped_item() -> None:
    attribution = search_module.attribute_product_image(
        ListingCandidate(
            listing_text=(
                "7118/1200A blue fullset 7118/1200A grey fullset "
                "5712G new 2024"
            ),
            image_url="https://watchfacts.example/7118-parent.jpg",
        ),
        listing_text="7118/1200A blue fullset",
        query="7118/1200a blue",
    )

    assert attribution.image_url == "https://watchfacts.example/7118-parent.jpg"
    assert attribution.reason == "image.inherited_parent_first_item"


def test_attribute_product_image_inherits_parent_image_for_first_scoped_item() -> None:
    attribution = search_module.attribute_product_image(
        ListingCandidate(
            listing_text=(
                "Panerai Luminor PAM01033 2023 Fullset 38100HKD "
                "Panerai Luminor PAM01312 2018 Fullset 27900HKD"
            ),
            image_url="https://watchfacts.example/panerai-first.jpg",
        ),
        listing_text="Panerai Luminor PAM01033 2023 Fullset 38100HKD",
        query="Panerai Luminor",
    )

    assert attribution.image_url == "https://watchfacts.example/panerai-first.jpg"
    assert attribution.reason == "image.inherited_parent_first_item"


def test_attribute_product_image_omits_color_scoped_bundle_when_reference_precedes() -> None:
    attribution = search_module.attribute_product_image(
        ListingCandidate(
            listing_text=(
                "126710BLRO Jub N4 hkd235K "
                "5205R Green N9/2025 New hkd410K "
                "5227G Salmon N5 hkd335K"
            ),
            image_url="https://watchfacts.example/rolex-parent.jpg",
        ),
        listing_text="5205R Green N9/2025 New hkd410K",
        query="5205r green",
    )

    assert attribution.image_url is None
    assert attribution.reason == "image.omitted_bundle_ambiguous"


def test_attribute_product_image_omits_repeated_reference_variant_after_first_item() -> None:
    attribution = search_module.attribute_product_image(
        ListingCandidate(
            listing_text=(
                "126500LN Black N2/2026 HK$235,000 "
                "126500LN White N3/2026 HK$279,000"
            ),
            image_url="https://watchfacts.example/daytona-parent.jpg",
        ),
        listing_text="126500LN White N3/2026 HK$279,000",
        query="126500ln white 2026",
    )

    assert attribution.image_url is None
    assert attribution.reason == "image.omitted_bundle_ambiguous"


def test_attribute_product_image_keeps_full_listing_with_repeated_reference() -> None:
    attribution = search_module.attribute_product_image(
        ListingCandidate(
            listing_text=(
                "LANGE 1 Series 101.031 Watch LANGE 1 101.031 "
                "38.5 mm watch only 24500usd"
            ),
            image_url="https://watchfacts.example/lange.jpg",
        ),
        query="Lange 1",
    )

    assert attribution.image_url == "https://watchfacts.example/lange.jpg"
    assert attribution.reason == "image.direct"


def test_attribute_product_image_omits_bundle_when_other_reference_precedes_item() -> None:
    attribution = search_module.attribute_product_image(
        ListingCandidate(
            listing_text=(
                "5712/1r 2025 1,975m 5990/1 2021 1,98m "
                "FPJ Elegante titanium 48mm 2019 fullset HKD785K"
            ),
            image_url="https://watchfacts.example/bundle-cover.jpg",
        ),
        listing_text="FPJ Elegante titanium 48mm 2019 fullset HKD785K",
        query="FPJ Elegante Titanium",
    )

    assert attribution.image_url is None
    assert attribution.reason == "image.omitted_bundle_ambiguous"


def test_attribute_product_image_omits_ambiguous_bundle_image() -> None:
    attribution = search_module.attribute_product_image(
        ListingCandidate(
            listing_text="5712G new 2024 115k 5726/1A used 2022 68k",
            image_url="https://watchfacts.example/bundle.jpg",
        ),
        listing_text="5726/1A used 2022 68k",
        query="5726/1a",
    )

    assert attribution.image_url is None
    assert attribution.reason == "image.omitted_bundle_ambiguous"


def test_attribute_product_image_omits_ambiguous_repeated_reference_bundle_for_rm65() -> None:
    attribution = search_module.attribute_product_image(
        ListingCandidate(
            listing_text=(
                "💯RM037ce white 2/26 male HKD2.18m usdt280k "
                "💯RM65-01 Lebron Jamew 12/25 usdt485k "
                "💯RM65-01 Mclaren 12/25 usdt475k "
                "💯RM30-01 white ceramic 2/26 usdt369k "
            ),
            image_url="https://watchfacts.example/rm65-bundle.jpg",
        ),
        listing_text="💯RM65-01 Lebron Jamew 12/25 usdt485k",
        query="RM65-01 Lebron",
    )

    assert attribution.image_url is None
    assert attribution.reason == "image.omitted_bundle_ambiguous"


def test_search_workflow_coalesces_concurrent_same_query_fetches(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = FIXTURE.read_text()
    fetch_count = 0

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        nonlocal fetch_count
        fetch_count += 1
        await asyncio.sleep(0.01)
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )

    async def run_searches() -> tuple[list[SearchResult], list[SearchResult]]:
        first, second = await asyncio.gather(
            workflow.search("228253a choco"),
            workflow.search("228253a choco"),
        )
        return first, second

    first, second = asyncio.run(run_searches())

    assert fetch_count == 1
    assert first == second
    assert workflow.last_search_diagnostics is not None
    assert workflow.last_search_diagnostics.cache_hit is False
    assert workflow.last_search_diagnostics.parsed_count == 2
    with sqlite3.connect(settings.db_path) as connection:
        query_count = connection.execute("SELECT COUNT(*) FROM queries").fetchone()[0]

    assert query_count == 2


def test_search_workflow_reports_in_flight_wait_for_coalesced_workflows(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = FIXTURE.read_text()
    fetch_count = 0

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        nonlocal fetch_count
        fetch_count += 1
        await asyncio.sleep(0.01)
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    owner_workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )
    coalesced_workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )

    async def run_searches() -> tuple[list[SearchResult], list[SearchResult]]:
        owner_task = asyncio.create_task(owner_workflow.search("228253a choco"))
        await asyncio.sleep(0)
        coalesced = await coalesced_workflow.search("228253a choco")
        owner = await owner_task
        return owner, coalesced

    owner, coalesced = asyncio.run(run_searches())

    assert fetch_count == 1
    assert owner == coalesced
    assert coalesced_workflow.last_search_diagnostics is not None
    assert coalesced_workflow.last_search_diagnostics.cache_hit is True
    assert coalesced_workflow.last_search_diagnostics.stage_timings_ms is not None
    assert coalesced_workflow.last_search_diagnostics.stage_timings_ms.keys() >= {
        "cache_read",
        "in_flight_wait",
        "persist",
        "total",
    }


def test_search_workflow_coalesces_in_flight_descriptor_alias_queries(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "RM07-01 RG Snow used fullset",
          "companyName": "HK STOCKS",
          "number": 701
        }
      ]
    }
    """
    fetch_count = 0

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        nonlocal fetch_count
        fetch_count += 1
        await asyncio.sleep(0.01)
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    owner_workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )
    coalesced_workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )

    async def run_searches() -> tuple[list[SearchResult], list[SearchResult]]:
        owner_task = asyncio.create_task(owner_workflow.search("rm07-01 rose gold"))
        await asyncio.sleep(0)
        coalesced = await coalesced_workflow.search("rm07-01 rg")
        owner = await owner_task
        return owner, coalesced

    owner, coalesced = asyncio.run(run_searches())

    assert fetch_count == 1
    assert owner == coalesced
    assert coalesced_workflow.last_search_diagnostics is not None
    assert coalesced_workflow.last_search_diagnostics.cache_hit is True
    assert coalesced_workflow.last_search_diagnostics.query_plan is not None
    assert coalesced_workflow.last_search_diagnostics.query_plan.original_query == (
        "rm07-01 rg"
    )


def test_search_workflow_coalesces_in_flight_retrieval_branch_across_queries(
    tmp_path,
) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "RM07-01 RG Snow used fullset",
          "companyName": "Dealer RG",
          "number": 701
        },
        {
          "title": "RM07-01 WG Snow used fullset",
          "companyName": "Dealer WG",
          "number": 702
        }
      ]
    }
    """
    fetch_count = 0
    fetch_queries: list[str | None] = []

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        nonlocal fetch_count
        fetch_count += 1
        fetch_queries.append(query)
        await asyncio.sleep(0.01)
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    rg_workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )
    wg_workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )

    async def run_searches() -> tuple[list[SearchResult], list[SearchResult]]:
        rg_task = asyncio.create_task(rg_workflow.search("rm07-01 rg"))
        await asyncio.sleep(0)
        wg = await wg_workflow.search("rm07-01 wg")
        rg = await rg_task
        return rg, wg

    rg_results, wg_results = asyncio.run(run_searches())

    assert fetch_count == 1
    assert fetch_queries == ["rm07-01"]
    assert [result.listing_text for result in rg_results] == [
        "RM07-01 RG Snow used fullset"
    ]
    assert [result.listing_text for result in wg_results] == [
        "RM07-01 WG Snow used fullset"
    ]
    assert rg_workflow.last_search_diagnostics is not None
    assert wg_workflow.last_search_diagnostics is not None
    assert rg_workflow.last_search_diagnostics.retrieval_queries == ("rm07-01",)
    assert wg_workflow.last_search_diagnostics.retrieval_queries == ("rm07-01",)
    reason_codes = [
        reason
        for workflow in (rg_workflow, wg_workflow)
        for timing in workflow.last_search_diagnostics.retrieval_timings
        for reason in timing.reason_codes
    ]
    assert "retrieval.branch_coalesced" in reason_codes


def test_search_workflow_reuses_fresh_retrieval_branch_cache_across_queries(
    tmp_path,
) -> None:
    search_module._RETRIEVAL_BRANCH_CACHE.clear()
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "RM07-01 RG Snow used fullset",
          "companyName": "Dealer RG",
          "number": 701
        },
        {
          "title": "RM07-01 WG Snow used fullset",
          "companyName": "Dealer WG",
          "number": 702
        }
      ]
    }
    """
    fetch_count = 0
    fetch_queries: list[str | None] = []

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        nonlocal fetch_count
        fetch_count += 1
        fetch_queries.append(query)
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    rg_workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )
    wg_workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )

    rg_results = asyncio.run(rg_workflow.search("rm07-01 rg"))
    wg_results = asyncio.run(wg_workflow.search("rm07-01 wg"))

    assert fetch_count == 1
    assert fetch_queries == ["rm07-01"]
    assert [result.listing_text for result in rg_results] == [
        "RM07-01 RG Snow used fullset"
    ]
    assert [result.listing_text for result in wg_results] == [
        "RM07-01 WG Snow used fullset"
    ]
    assert rg_workflow.last_search_diagnostics is not None
    assert wg_workflow.last_search_diagnostics is not None
    assert (
        rg_workflow.last_search_diagnostics.retrieval_timings[0].cache_status
        == "miss"
    )
    assert (
        wg_workflow.last_search_diagnostics.retrieval_timings[0].cache_status
        == "hit"
    )
    assert "retrieval.branch_cache_miss" in (
        rg_workflow.last_search_diagnostics.retrieval_timings[0].reason_codes
    )
    assert "retrieval.branch_cache_hit" in (
        wg_workflow.last_search_diagnostics.retrieval_timings[0].reason_codes
    )


def test_search_workflow_refreshes_stale_retrieval_branch_cache(tmp_path) -> None:
    search_module._RETRIEVAL_BRANCH_CACHE.clear()
    settings = replace(make_settings(tmp_path), search_retrieval_branch_cache_ttl_seconds=1)
    html = """
    {
      "listings": [
        {
          "title": "RM07-01 RG Snow used fullset",
          "companyName": "Dealer RG",
          "number": 701
        },
        {
          "title": "RM07-01 WG Snow used fullset",
          "companyName": "Dealer WG",
          "number": 702
        }
      ]
    }
    """
    fetch_count = 0

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        nonlocal fetch_count
        fetch_count += 1
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    rg_workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )
    wg_workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )

    asyncio.run(rg_workflow.search("rm07-01 rg"))
    assert len(search_module._RETRIEVAL_BRANCH_CACHE) == 1
    cache_key, entry = next(iter(search_module._RETRIEVAL_BRANCH_CACHE.items()))
    search_module._RETRIEVAL_BRANCH_CACHE[cache_key] = replace(
        entry,
        cached_at=entry.cached_at - 2,
    )

    asyncio.run(wg_workflow.search("rm07-01 wg"))

    assert fetch_count == 2
    assert wg_workflow.last_search_diagnostics is not None
    assert (
        wg_workflow.last_search_diagnostics.retrieval_timings[0].cache_status
        == "stale"
    )
    assert "retrieval.branch_cache_stale" in (
        wg_workflow.last_search_diagnostics.retrieval_timings[0].reason_codes
    )


def test_search_workflow_limits_retrieval_branch_cache_entries(tmp_path) -> None:
    search_module._RETRIEVAL_BRANCH_CACHE.clear()
    first = ScrapeResult(
        html='{"listings":[{"title":"first","number":701}]}',
        final_url="https://watchfacts.example/first",
    )
    second = ScrapeResult(
        html='{"listings":[{"title":"second","number":702}]}',
        final_url="https://watchfacts.example/second",
    )

    search_module._record_retrieval_branch_cache_result(
        "first",
        first,
        ttl_seconds=90,
        max_entries=1,
    )
    search_module._record_retrieval_branch_cache_result(
        "second",
        second,
        ttl_seconds=90,
        max_entries=1,
    )

    assert len(search_module._RETRIEVAL_BRANCH_CACHE) == 1
    assert "second" in search_module._RETRIEVAL_BRANCH_CACHE
    assert "first" not in search_module._RETRIEVAL_BRANCH_CACHE


def test_search_workflow_does_not_cache_failed_retrieval_branch(tmp_path) -> None:
    search_module._RETRIEVAL_BRANCH_CACHE.clear()
    settings = make_settings(tmp_path)
    fetch_count = 0

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        nonlocal fetch_count
        fetch_count += 1
        if fetch_count == 1:
            raise RuntimeError("temporary failure")
        return ScrapeResult(
            html='{"listings":[{"title":"RM07-01 RG Snow","number":701}]}',
            final_url=settings.watchfacts_url,
        )

    failing_workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )
    successful_workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )

    with pytest.raises(RuntimeError, match="temporary failure"):
        asyncio.run(failing_workflow.search("rm07-01 rg"))
    results = asyncio.run(successful_workflow.search("rm07-01 rg"))

    assert fetch_count == 2
    assert [result.listing_text for result in results] == ["RM07-01 RG Snow"]
    assert successful_workflow.last_search_diagnostics is not None
    assert (
        successful_workflow.last_search_diagnostics.retrieval_timings[0].cache_status
        == "miss"
    )


def test_search_workflow_limits_search_runtime_concurrent_distinct_queries(tmp_path) -> None:
    settings = Settings(
        telegram_bot_token="",
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
        runtime_mode="search",
        search_max_concurrent_searches=1,
    )
    active_fetches = 0
    max_active_fetches = 0

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        nonlocal active_fetches, max_active_fetches
        active_fetches += 1
        max_active_fetches = max(max_active_fetches, active_fetches)
        await asyncio.sleep(0.01)
        active_fetches -= 1
        return ScrapeResult(html="{}", final_url=settings.watchfacts_url)

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    async def run_searches() -> None:
        await asyncio.gather(
            workflow.search("5712g"),
            workflow.search("5712r"),
        )

    asyncio.run(run_searches())

    assert max_active_fetches == 1


def test_search_workflow_persists_no_result_queries(tmp_path) -> None:
    settings = make_settings(tmp_path)

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(html=FIXTURE.read_text(), final_url=settings.watchfacts_url)

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("does not exist"))

    assert results == []
    with sqlite3.connect(settings.db_path) as connection:
        query_row = connection.execute(
            "SELECT query_text, normalized_query, result_count FROM queries"
        ).fetchone()

    assert query_row == ("does not exist", "does not exist", 0)


def test_search_workflow_keeps_server_filtered_results_without_strict_refilter(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "matched_term": null,
      "listings": [
        {
          "title": "2017 Patek 5712/1A Fullset Retail Ready $116",
          "companyName": "Khoa Ng",
          "repostedAt": "2026-04-22 18:23:39",
          "number": 40881,
          "listings": [
            {
              "title": "2017 Patek 5712/1A Fullset Retail Ready $116",
              "frontImage": "https://watchfacts.example/5712.jpg",
              "dialColor": "blue"
            }
          ]
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("5712 blue"))

    assert len(results) == 1
    assert results[0].listing_text == "2017 Patek 5712/1A Fullset Retail Ready $116"
    assert results[0].seller == "Khoa Ng"
    assert results[0].posted_date == "April 22, 2026"
    assert results[0].image_url == "https://watchfacts.example/5712.jpg"
    assert results[0].source_url == "/flash-sales/40881"


def test_search_workflow_refilters_broad_server_filtered_reference_queries(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "5205R black 2016 full set 49500 USD",
          "companyName": "Seller Match",
          "number": 111
        },
        {
          "title": "5712R 2017 full set HKD 820000",
          "companyName": "Seller Other",
          "number": 222
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)
    results = asyncio.run(workflow.search("5205r"))

    assert [result.listing_text for result in results] == [
        "5205R black 2016 full set 49500 USD"
    ]
    assert workflow.last_search_diagnostics is not None
    assert workflow.last_search_diagnostics.parsed_count == 2
    assert workflow.last_search_diagnostics.matched_count == 1


def test_server_filtered_color_query_filters_text_mismatches(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "15510OR 2022 New 92k",
          "companyName": "Seller NonBlue",
          "number": 111,
          "frontImage": "https://watchfacts.example/15510or-noblue.jpg"
        },
        {
          "title": "15510OR Blue dial 2024 Fullset 94k",
          "companyName": "Seller Blue",
          "number": 222,
          "frontImage": "https://watchfacts.example/15510or-blue.jpg"
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)
    results = asyncio.run(workflow.search("15510or blue"))

    assert [result.listing_text for result in results] == [
        "15510OR Blue dial 2024 Fullset 94k",
    ]


def test_server_filtered_color_query_with_or_connector_filters_text_mismatches(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "15510OR 2022 New 92k",
          "companyName": "Seller NonBlue",
          "number": 111,
          "frontImage": "https://watchfacts.example/15510or-noblue.jpg"
        },
        {
          "title": "15510OR Blue dial 2024 Fullset 94k",
          "companyName": "Seller Blue",
          "number": 222,
          "frontImage": "https://watchfacts.example/15510or-blue.jpg"
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)
    results = asyncio.run(workflow.search("15510 or blue"))

    assert [result.listing_text for result in results] == [
        "15510OR Blue dial 2024 Fullset 94k",
    ]


def test_server_filtered_color_query_uses_dial_color_match_text_for_server_json(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "15510OR New 92k",
          "dialColor": "blue",
          "companyName": "Seller MetaBlue",
          "number": 111
        },
        {
          "title": "15510OR New 96k",
          "companyName": "Seller NoColor",
          "number": 222
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)
    results = asyncio.run(workflow.search("15510or blue"))

    assert len(results) == 1
    assert results[0].listing_text == "15510OR New 92k"
    assert results[0].source_url == "/flash-sales/111"


def test_server_filtered_nested_variant_color_matches_are_variant_specific(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "",
          "dialColor": "blue",
          "listings": [
            {
              "title": "15510OR.OO.D315CR02 nonblue",
              "dialColor": "black",
              "frontImage": "https://watchfacts.example/15510or-black.jpg"
            },
            {
              "title": "15510OR.OO.D315CR03 blue",
              "dialColor": "blue",
              "frontImage": "https://watchfacts.example/15510or-blue.jpg"
            }
          ],
          "number": 555
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)
    results = asyncio.run(workflow.search("15510or blue"))

    assert [result.listing_text for result in results] == [
        "15510OR.OO.D315CR03 blue",
    ]


def test_search_workflow_matches_non_blue_variant_without_inheriting_blue_parent_image(
    tmp_path,
) -> None:
    settings = make_settings(tmp_path)
    html = '''
    {
      "listings": [
        {
          "title": "15510OR",
          "dialColor": "blue",
          "frontImage": "https://watchfacts.example/parent-blue.jpg",
          "number": 333,
          "listings": [
            {
              "title": "15510OR black",
              "dialColor": "black"
            },
            {
              "title": "15510OR blue",
              "dialColor": "blue",
              "frontImage": "https://watchfacts.example/15510or-blue.jpg"
            }
          ]
        }
      ]
    }
    '''

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )

    results = asyncio.run(workflow.search("15510or black"))

    assert len(results) == 1
    assert results[0].listing_text == "15510OR black"
    assert results[0].image_url is None


def test_server_filtered_parent_color_isolation_for_nested_listings(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "15510OR",
          "dialColor": "blue",
          "listings": [
            {
              "title": "15510OR.OO.D315CR02 nonblue",
              "dialColor": "black",
              "frontImage": "https://watchfacts.example/15510or-black.jpg"
            },
            {
              "title": "15510OR.OO.D315CR03 blue",
              "dialColor": "blue",
              "frontImage": "https://watchfacts.example/15510or-blue.jpg"
            }
          ],
          "number": 555
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)
    results = asyncio.run(workflow.search("15510or blue"))

    assert [result.listing_text for result in results] == [
        "15510OR.OO.D315CR03 blue",
    ]


def test_search_workflow_drops_server_filtered_non_sale_requests(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "Lookingfor 228235A choco new 2026",
          "companyName": "Buyer",
          "number": 111
        },
        {
          "title": "228235A Choco New 3/26 $58,000 USD",
          "companyName": "Seller",
          "number": 222
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("228235a choco"))

    assert [result.listing_text for result in results] == [
        "228235A Choco New 3/26 $58,000 USD"
    ]


def test_search_workflow_refilters_server_filtered_non_color_descriptor_queries(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "228235A Choco New 3/26 $58,000 USD",
          "companyName": "Seller",
          "number": 111
        },
        {
          "title": "228235A Sundust 436k hkd 12/25y",
          "companyName": "Other",
          "number": 222
        },
        {
          "title": "228235A Cho N4 $465K",
          "companyName": "Third",
          "number": 333
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("228235a cho"))

    assert [result.listing_text for result in results] == [
        "228235A Choco New 3/26 $58,000 USD",
        "228235A Cho N4 $465K",
    ]


def test_search_workflow_refilters_server_filtered_non_color_variant_descriptors(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "228349RBR Blue OMBRE ROM 2025 Used HKD 525K",
          "companyName": "Seller",
          "number": 111
        },
        {
          "title": "228349RBR A METE 2024 $610000",
          "companyName": "Member 1000",
          "number": 222
        },
        {
          "title": "228349 pave N12 720000",
          "companyName": "Other",
          "number": 333
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("228349rbr mete"))

    assert [result.listing_text for result in results] == [
        "228349RBR A METE 2024 $610000"
    ]


def test_search_workflow_falls_back_to_image_backed_reference_matches(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "228349 r br Blue OMBRE ROM 2025 Used HKD 525K",
          "companyName": "Seller",
          "number": 111,
          "frontImage": "https://watchfacts.example/228349rbr.jpg"
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("228349rbr mete"))

    assert [result.listing_text for result in results] == [
        "228349 r br Blue OMBRE ROM 2025 Used HKD 525K"
    ]
    assert results[0].image_url == "https://watchfacts.example/228349rbr.jpg"


def test_search_workflow_matches_server_filtered_rg_snow_material_aliases(tmp_path) -> None:
    settings = make_settings(tmp_path)
    fetch_queries: list[str | None] = []
    html = """
    {
      "listings": [
        {
          "title": "rm07-01 Pink ceramic 2020y Full set 312K usdt",
          "companyName": "Member 5805",
          "repostedAt": "2026-06-09 10:00:00",
          "number": 111,
          "frontImage": "https://watchfacts.example/rm07-pink.jpg"
        },
        {
          "title": "RM07-01 WG Snow Onyx N4-26 360,000 USDT",
          "companyName": "member 656225",
          "repostedAt": "2026-06-01 10:00:00",
          "number": 222,
          "frontImage": "https://watchfacts.example/rm07-wg-snow.jpg"
        },
        {
          "title": "Rm07-01 Ladies 18K Rose Gold Diamonds Snow Setting Red Jasper Brand New Full Set Q4 2024 USD328,000",
          "companyName": "Member 5555",
          "repostedAt": "2026-04-11 10:00:00",
          "number": 333,
          "frontImage": "https://watchfacts.example/rm07-rg-snow-1.jpg"
        },
        {
          "title": "RM07-01 Rosegold Snow Diamonds Red Lips Good Condition Watch Only 2,028,000HK",
          "companyName": "Cici",
          "repostedAt": "2026-03-30 10:00:00",
          "number": 444,
          "frontImage": "https://watchfacts.example/rm07-rg-snow-2.jpg"
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        fetch_queries.append(query)
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("rm07-01 rg snow"))

    assert [result.listing_text for result in results] == [
        "Rm07-01 Ladies 18K Rose Gold Diamonds Snow Setting Red Jasper Brand New Full Set Q4 2024 USD328,000",
        "RM07-01 Rosegold Snow Diamonds Red Lips Good Condition Watch Only 2,028,000HK",
    ]
    assert fetch_queries == ["rm07-01"]
    assert workflow.last_search_diagnostics is not None
    assert workflow.last_search_diagnostics.matched_count == 2
    diagnostics_payload = workflow.last_search_diagnostics.to_payload()
    assert diagnostics_payload["retrieval_query_count"] == 1
    assert diagnostics_payload["retrieval_queries"] == ["rm07-01"]
    assert "retrieval.reference_with_descriptors" in diagnostics_payload[
        "retrieval_reason_codes"
    ]


def test_search_workflow_uses_same_uncached_retrieval_for_compound_material_aliases(
    tmp_path,
) -> None:
    settings = replace(make_settings(tmp_path), search_retrieval_branch_cache_ttl_seconds=0)
    fetch_queries: list[str | None] = []
    html = """
    {
      "listings": [
        {
          "title": "RM07-01 RG Medset Black Lips Used 2018 / 204k usdt",
          "companyName": "Dealer A",
          "repostedAt": "2026-06-13 10:00:00",
          "number": 111,
          "frontImage": "https://watchfacts.example/rm07-rg.jpg"
        },
        {
          "title": "RM07-01 Rose Gold Medset Likenew 2021 Fullset 199000USDT",
          "companyName": "Dealer B",
          "repostedAt": "2026-06-12 10:00:00",
          "number": 222,
          "frontImage": "https://watchfacts.example/rm07-rose-gold.jpg"
        },
        {
          "title": "RM07-01 WG Medset Red Lips Used 2020 - 195k usdt",
          "companyName": "Dealer C",
          "repostedAt": "2026-06-11 10:00:00",
          "number": 333,
          "frontImage": "https://watchfacts.example/rm07-wg.jpg"
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        fetch_queries.append(query)
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    rose_gold_results = asyncio.run(workflow.search("rm07-01 rose gold"))
    with sqlite3.connect(settings.db_path) as connection:
        connection.execute("DELETE FROM search_cache")
    rosegold_results = asyncio.run(workflow.search("rm07-01 rosegold"))

    assert fetch_queries == ["rm07-01", "rm07-01"]
    assert rose_gold_results == rosegold_results
    assert [result.listing_text for result in rosegold_results] == [
        "RM07-01 RG Medset Black Lips Used 2018 / 204k usdt",
        "RM07-01 Rose Gold Medset Likenew 2021 Fullset 199000USDT",
    ]


def test_search_workflow_expands_daytona_panda_retrieval_with_local_filters(
    tmp_path,
) -> None:
    settings = make_settings(tmp_path)
    fetch_queries: list[str | None] = []

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        fetch_queries.append(query)
        if query == "daytona panda":
            html = """
            {
              "listings": [
                {
                  "title": "Rolex Daytona Panda 2024 full set HKD 268000",
                  "companyName": "Dealer Panda",
                  "repostedAt": "2026-06-13 10:00:00",
                  "number": 111,
                  "frontImage": "https://watchfacts.example/daytona-panda.jpg"
                }
              ]
            }
            """
        elif query == "daytona white":
            html = """
            {
              "listings": [
                {
                  "title": "Rolex Daytona White Dial 2023 Full Set HKD 255000",
                  "companyName": "Dealer White",
                  "repostedAt": "2026-06-12 10:00:00",
                  "number": 222,
                  "frontImage": "https://watchfacts.example/daytona-white.jpg"
                },
                {
                  "title": "Rolex Daytona Black Dial 2024 Full Set HKD 238000",
                  "companyName": "Dealer Black",
                  "repostedAt": "2026-06-12 10:00:00",
                  "number": 223,
                  "frontImage": "https://watchfacts.example/daytona-black.jpg"
                }
              ]
            }
            """
        elif query == "126500ln white":
            html = """
            {
              "listings": [
                {
                  "title": "126500LN White Dial N5/2026 HKD 279000",
                  "companyName": "Dealer 126",
                  "repostedAt": "2026-06-11 10:00:00",
                  "number": 333,
                  "frontImage": "https://watchfacts.example/126500ln-white.jpg"
                },
                {
                  "title": "126500LN Black Dial N5/2026 HKD 239000",
                  "companyName": "Dealer 126 Black",
                  "repostedAt": "2026-06-11 10:00:00",
                  "number": 334,
                  "frontImage": "https://watchfacts.example/126500ln-black.jpg"
                }
              ]
            }
            """
        else:
            html = """
            {
              "listings": [
                {
                  "title": "116500LN White Dial 2021 Full Set HKD 225000",
                  "companyName": "Dealer 116",
                  "repostedAt": "2026-06-10 10:00:00",
                  "number": 444,
                  "frontImage": "https://watchfacts.example/116500ln-white.jpg"
                }
              ]
            }
            """
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("daytona panda"))
    result_texts = {result.listing_text for result in results}

    assert fetch_queries == [
        "daytona panda",
        "daytona white",
        "126500ln white",
        "116500ln white",
    ]
    assert result_texts == {
        "Daytona Panda 2024 full set HKD 268000",
        "Rolex Daytona White Dial 2023 Full Set HKD 255000",
        "126500LN White Dial N5/2026 HKD 279000",
        "116500LN White Dial 2021 Full Set HKD 225000",
    }
    assert all("Black Dial" not in result.listing_text for result in results)
    assert workflow.last_search_diagnostics is not None
    diagnostics_payload = workflow.last_search_diagnostics.to_payload()
    assert diagnostics_payload["retrieval_query_count"] == 4
    assert diagnostics_payload["retrieval_queries"] == [
        "daytona panda",
        "daytona white",
        "126500ln white",
        "116500ln white",
    ]
    retrieval_timings = diagnostics_payload["retrieval_timings"]
    assert [row["query"] for row in retrieval_timings] == [
        "daytona panda",
        "daytona white",
        "126500ln white",
        "116500ln white",
    ]
    assert [row["queue_index"] for row in retrieval_timings] == [1, 2, 3, 4]
    assert [row["cache_status"] for row in retrieval_timings] == [
        "miss",
        "miss",
        "miss",
        "miss",
    ]
    assert [row["parsed_count"] for row in retrieval_timings] == [1, 2, 2, 1]
    assert [row["matched_count"] for row in retrieval_timings] == [1, 1, 1, 1]
    assert [row["unique_result_count"] for row in retrieval_timings] == [1, 1, 1, 1]
    assert sum(row["top_result_count"] for row in retrieval_timings) == 3
    assert [row["empty"] for row in retrieval_timings] == [
        False,
        False,
        False,
        False,
    ]
    assert sum(1 for row in retrieval_timings if row["dominant"]) == 1
    assert all(
        isinstance(row[key], int) and row[key] >= 0
        for row in retrieval_timings
        for key in ("fetch_ms", "parse_ms", "match_ms", "total_ms")
    )
    assert all(
        row["reason_codes"] == [
            "retrieval.raw_query",
            "retrieval.nickname_expansion:panda",
            "retrieval.branch_cache_miss",
        ]
        for row in retrieval_timings
    )
    assert "retrieval.nickname_expansion:panda" in diagnostics_payload[
        "retrieval_reason_codes"
    ]


def test_search_workflow_fetches_retrieval_branches_with_bounded_parallelism(
    tmp_path,
) -> None:
    settings = replace(make_settings(tmp_path), search_retrieval_concurrency=2)
    started: list[str | None] = []
    completed: list[str | None] = []
    active_fetches = 0
    max_active_fetches = 0
    delays = {
        "daytona panda": 0.03,
        "daytona white": 0.01,
        "126500ln white": 0.01,
        "116500ln white": 0.01,
    }

    def listing_payload(title: str, number: int) -> str:
        return f"""
        {{
          "listings": [
            {{
              "title": "{title}",
              "companyName": "Dealer {number}",
              "repostedAt": "2026-06-13 10:00:00",
              "number": {number},
              "frontImage": "https://watchfacts.example/{number}.jpg"
            }}
          ]
        }}
        """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        nonlocal active_fetches, max_active_fetches
        started.append(query)
        active_fetches += 1
        max_active_fetches = max(max_active_fetches, active_fetches)
        try:
            await asyncio.sleep(delays[str(query)])
            if query == "daytona panda":
                title = "Rolex Daytona Panda 2024 full set HKD 268000"
                number = 111
            elif query == "daytona white":
                title = "Rolex Daytona White Dial 2023 Full Set HKD 255000"
                number = 222
            elif query == "126500ln white":
                title = "126500LN White Dial N5/2026 HKD 279000"
                number = 333
            else:
                title = "116500LN White Dial 2021 Full Set HKD 225000"
                number = 444
            return ScrapeResult(
                html=listing_payload(title, number),
                final_url="https://watchfacts.example/simon-search-matches",
                server_filtered=True,
            )
        finally:
            completed.append(query)
            active_fetches -= 1

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("daytona panda"))

    expected_queries = [
        "daytona panda",
        "daytona white",
        "126500ln white",
        "116500ln white",
    ]
    assert started == expected_queries
    assert completed != expected_queries
    assert max_active_fetches == 2
    assert {result.listing_text for result in results} == {
        "Daytona Panda 2024 full set HKD 268000",
        "Rolex Daytona White Dial 2023 Full Set HKD 255000",
        "126500LN White Dial N5/2026 HKD 279000",
        "116500LN White Dial 2021 Full Set HKD 225000",
    }
    assert workflow.last_search_diagnostics is not None
    diagnostics_payload = workflow.last_search_diagnostics.to_payload()
    assert diagnostics_payload["retrieval_queries"] == expected_queries
    assert [row["queue_index"] for row in diagnostics_payload["retrieval_timings"]] == [
        1,
        2,
        3,
        4,
    ]
    assert [row["query"] for row in diagnostics_payload["retrieval_timings"]] == (
        expected_queries
    )


def test_search_workflow_isolates_partial_retrieval_fetch_failures(tmp_path) -> None:
    settings = replace(make_settings(tmp_path), search_retrieval_concurrency=2)
    fail_reference_branch = True
    fetch_queries: list[str | None] = []

    def listing_payload(title: str, number: int) -> str:
        return f"""
        {{
          "listings": [
            {{
              "title": "{title}",
              "companyName": "Dealer {number}",
              "repostedAt": "2026-06-13 10:00:00",
              "number": {number},
              "frontImage": "https://watchfacts.example/{number}.jpg"
            }}
          ]
        }}
        """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        fetch_queries.append(query)
        await asyncio.sleep(0)
        if query == "5711" and fail_reference_branch:
            raise RuntimeError("simulated branch outage")
        if query == "5711 blue":
            title = "5711 Blue Dial 2022 Full Set HKD 980000"
            number = 111
        elif query == "5711":
            title = "Patek Philippe 5711/1A Blue Dial 2020 HKD 920000"
            number = 222
        else:
            title = "Nautilus 5711 Blue Dial 2019 Full Set HKD 910000"
            number = 333
        return ScrapeResult(
            html=listing_payload(title, number),
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("5711 blue"))

    assert fetch_queries == ["5711 blue", "5711", "nautilus 5711 blue"]
    assert {result.listing_text for result in results} == {
        "5711 Blue Dial 2022 Full Set HKD 980000",
        "Nautilus 5711 Blue Dial 2019 Full Set HKD 910000",
    }
    assert workflow.last_search_diagnostics is not None
    diagnostics_payload = workflow.last_search_diagnostics.to_payload()
    assert diagnostics_payload["retrieval_queries"] == [
        "5711 blue",
        "5711",
        "nautilus 5711 blue",
    ]
    failed_timing = diagnostics_payload["retrieval_timings"][1]
    assert failed_timing["query"] == "5711"
    assert failed_timing["failed"] is True
    assert failed_timing["unique_result_count"] == 0
    assert failed_timing["top_result_count"] == 0
    assert failed_timing["error_type"] == "RuntimeError"
    assert "retrieval.fetch_error:RuntimeError" in failed_timing["reason_codes"]
    assert diagnostics_payload["rejection_reasons"]["retrieval.fetch_error"] == 1
    with sqlite3.connect(settings.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0] == 0

    fail_reference_branch = False
    fetch_queries.clear()
    recovered = asyncio.run(workflow.search("5711 blue"))

    assert fetch_queries == ["5711"]
    assert {result.listing_text for result in recovered} == {
        "5711 Blue Dial 2022 Full Set HKD 980000",
        "Patek Philippe 5711/1A Blue Dial 2020 HKD 920000",
        "Nautilus 5711 Blue Dial 2019 Full Set HKD 910000",
    }
    assert workflow.last_search_diagnostics is not None
    assert workflow.last_search_diagnostics.cache_hit is False
    with sqlite3.connect(settings.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0] == 1


def test_search_workflow_does_not_expand_reference_only_query(tmp_path) -> None:
    settings = make_settings(tmp_path)
    fetch_queries: list[str | None] = []
    html = """
    {
      "listings": [
        {
          "title": "126500LN White Dial N5/2026 HKD 279000",
          "companyName": "Dealer 126",
          "repostedAt": "2026-06-11 10:00:00",
          "number": 333,
          "frontImage": "https://watchfacts.example/126500ln-white.jpg"
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        fetch_queries.append(query)
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("126500ln"))

    assert fetch_queries == ["126500ln"]
    assert [result.listing_text for result in results] == [
        "126500LN White Dial N5/2026 HKD 279000"
    ]
    assert workflow.last_search_diagnostics is not None
    assert workflow.last_search_diagnostics.retrieval_reason_codes == (
        "retrieval.raw_query",
    )


def test_search_workflow_does_not_expand_reference_with_nickname_query(tmp_path) -> None:
    settings = make_settings(tmp_path)
    fetch_queries: list[str | None] = []

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        fetch_queries.append(query)
        if query == "126500ln":
            html = """
            {
              "listings": [
                {
                  "title": "126500LN Panda Dial N5/2026 HKD 279000",
                  "companyName": "Dealer 126",
                  "repostedAt": "2026-06-11 10:00:00",
                  "number": 333,
                  "frontImage": "https://watchfacts.example/126500ln-panda.jpg"
                }
              ]
            }
            """
        else:
            html = """
            {
              "listings": [
                {
                  "title": "116500LN White Dial 2021 Full Set HKD 225000",
                  "companyName": "Dealer 116",
                  "repostedAt": "2026-06-10 10:00:00",
                  "number": 444,
                  "frontImage": "https://watchfacts.example/116500ln-white.jpg"
                }
              ]
            }
            """
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("126500ln panda"))

    assert fetch_queries == ["126500ln"]
    assert [result.listing_text for result in results] == [
        "126500LN Panda Dial N5/2026 HKD 279000"
    ]
    assert workflow.last_search_diagnostics is not None
    assert workflow.last_search_diagnostics.retrieval_reason_codes == (
        "retrieval.reference_with_descriptors",
    )


def test_search_workflow_expands_5711_blue_retrieval_with_reference_scoped_filters(
    tmp_path,
) -> None:
    settings = make_settings(tmp_path)
    fetch_queries: list[str | None] = []

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        fetch_queries.append(query)
        if query == "5711 blue":
            html = """
            {
              "listings": [
                {
                  "title": "5711 Blue Dial 2022 Full Set HKD 980000",
                  "companyName": "Dealer Blue",
                  "repostedAt": "2026-06-13 10:00:00",
                  "number": 111,
                  "frontImage": "https://watchfacts.example/5711-blue.jpg"
                },
                {
                  "title": "5711 Black Dial 2021 Full Set HKD 930000",
                  "companyName": "Dealer Black",
                  "repostedAt": "2026-06-13 10:00:00",
                  "number": 112,
                  "frontImage": "https://watchfacts.example/5711-black.jpg"
                }
              ]
            }
            """
        elif query == "5711":
            html = """
            {
              "listings": [
                {
                  "title": "Patek Philippe 5711/1A Blue Dial 2020 HKD 920000",
                  "companyName": "Dealer Ref",
                  "repostedAt": "2026-06-12 10:00:00",
                  "number": 222,
                  "frontImage": "https://watchfacts.example/5711-ref-blue.jpg"
                },
                {
                  "title": "Patek Philippe 5712 Blue Dial 2020 HKD 780000",
                  "companyName": "Dealer Other Ref",
                  "repostedAt": "2026-06-12 10:00:00",
                  "number": 223,
                  "frontImage": "https://watchfacts.example/5712-blue.jpg"
                }
              ]
            }
            """
        else:
            html = """
            {
              "listings": [
                {
                  "title": "Nautilus 5711 Blue Dial 2019 Full Set HKD 910000",
                  "companyName": "Dealer Nautilus",
                  "repostedAt": "2026-06-11 10:00:00",
                  "number": 333,
                  "frontImage": "https://watchfacts.example/nautilus-5711-blue.jpg"
                }
              ]
            }
            """
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("5711 blue"))
    result_texts = {result.listing_text for result in results}

    assert fetch_queries == [
        "5711 blue",
        "5711",
        "nautilus 5711 blue",
    ]
    assert result_texts == {
        "5711 Blue Dial 2022 Full Set HKD 980000",
        "Patek Philippe 5711/1A Blue Dial 2020 HKD 920000",
        "Nautilus 5711 Blue Dial 2019 Full Set HKD 910000",
    }
    assert all("Black Dial" not in result.listing_text for result in results)
    assert all("5712" not in result.listing_text for result in results)
    assert workflow.last_search_diagnostics is not None
    diagnostics_payload = workflow.last_search_diagnostics.to_payload()
    assert diagnostics_payload["retrieval_query_count"] == 3
    assert diagnostics_payload["retrieval_queries"] == [
        "5711 blue",
        "5711",
        "nautilus 5711 blue",
    ]
    assert "retrieval.collection_expansion:nautilus" in diagnostics_payload[
        "retrieval_reason_codes"
    ]


def test_search_workflow_skips_5711_blue_fallback_when_primary_is_sufficient(
    tmp_path,
) -> None:
    settings = make_settings(tmp_path)
    fetch_queries: list[str | None] = []

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        fetch_queries.append(query)
        if query != "5711 blue":
            raise AssertionError(f"unexpected fallback fetch: {query}")
        html = """
        {
          "listings": [
            {
              "title": "5711 Blue Dial 2026 Full Set HKD 990000",
              "companyName": "Dealer 1",
              "repostedAt": "2026-06-13 10:00:00",
              "number": 101,
              "frontImage": "https://watchfacts.example/5711-blue-1.jpg"
            },
            {
              "title": "5711 Blue Dial 2025 Full Set HKD 980000",
              "companyName": "Dealer 2",
              "repostedAt": "2026-06-13 10:00:00",
              "number": 102,
              "frontImage": "https://watchfacts.example/5711-blue-2.jpg"
            },
            {
              "title": "5711 Blue Dial 2024 Full Set HKD 970000",
              "companyName": "Dealer 3",
              "repostedAt": "2026-06-13 10:00:00",
              "number": 103,
              "frontImage": "https://watchfacts.example/5711-blue-3.jpg"
            },
            {
              "title": "5711 Blue Dial 2023 Full Set HKD 960000",
              "companyName": "Dealer 4",
              "repostedAt": "2026-06-13 10:00:00",
              "number": 104,
              "frontImage": "https://watchfacts.example/5711-blue-4.jpg"
            },
            {
              "title": "5711 Blue Dial 2022 Full Set HKD 950000",
              "companyName": "Dealer 5",
              "repostedAt": "2026-06-13 10:00:00",
              "number": 105,
              "frontImage": "https://watchfacts.example/5711-blue-5.jpg"
            }
          ]
        }
        """
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("5711 blue"))

    assert fetch_queries == ["5711 blue"]
    assert len(results) == 5
    assert workflow.last_search_diagnostics is not None
    diagnostics_payload = workflow.last_search_diagnostics.to_payload()
    assert diagnostics_payload["retrieval_query_count"] == 1
    assert diagnostics_payload["retrieval_queries"] == ["5711 blue"]
    assert diagnostics_payload["retrieval_timings"][0]["matched_count"] == 5
    assert "retrieval.conditional_fallback_skipped" in diagnostics_payload[
        "retrieval_reason_codes"
    ]


@pytest.mark.parametrize("query", ["rolex 5711 blue", "5711 blue leather"])
def test_search_workflow_does_not_expand_5711_blue_when_extra_descriptors_change_intent(
    tmp_path,
    query: str,
) -> None:
    settings = make_settings(tmp_path)
    fetch_queries: list[str | None] = []

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        fetch_queries.append(query)
        html = """
        {
          "listings": [
            {
              "title": "Patek Philippe 5711/1A Blue Dial 2020 HKD 920000",
              "companyName": "Dealer Ref",
              "repostedAt": "2026-06-12 10:00:00",
              "number": 222,
              "frontImage": "https://watchfacts.example/5711-ref-blue.jpg"
            }
          ]
        }
        """
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search(query))

    assert fetch_queries == ["5711"]
    assert results == []
    assert workflow.last_search_diagnostics is not None
    assert workflow.last_search_diagnostics.retrieval_reason_codes == (
        "retrieval.reference_with_descriptors",
    )


@pytest.mark.parametrize(
    ("query", "expected_fetch_queries"),
    [
        (
            "patek 5711 blue",
            ("patek 5711 blue", "5711", "nautilus 5711 blue"),
        ),
        ("nautilus 5711 blue", ("nautilus 5711 blue", "5711")),
        ("Nautilus 5711 Blue", ("Nautilus 5711 Blue", "5711")),
    ],
)
def test_retrieval_plan_allows_safe_5711_blue_context_descriptors(
    query: str,
    expected_fetch_queries: tuple[str, ...],
) -> None:
    query_plan = search_module.build_query_plan(query)
    retrieval_plan = search_module._build_retrieval_plan(query, query_plan)

    assert retrieval_plan.fetch_queries == (query,)
    assert retrieval_plan.fallback_fetch_queries == expected_fetch_queries[1:]
    assert retrieval_plan.fallback_min_matched_count == 5
    assert retrieval_plan.local_filter_queries == (query, "5711 blue")
    assert retrieval_plan.strict_local_filter is True


def test_retrieval_plan_reports_duplicate_5711_branch_skip() -> None:
    query_plan = search_module.build_query_plan("Nautilus 5711 Blue")
    retrieval_plan = search_module._build_retrieval_plan(
        "Nautilus 5711 Blue",
        query_plan,
    )

    assert retrieval_plan.fetch_queries == ("Nautilus 5711 Blue",)
    assert retrieval_plan.fallback_fetch_queries == ("5711",)
    assert "retrieval.duplicate_branch_skipped" in retrieval_plan.reason_codes


def test_search_workflow_expands_15500st_blue_retrieval_with_reference_scoped_filters(
    tmp_path,
) -> None:
    settings = make_settings(tmp_path)
    fetch_queries: list[str | None] = []

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        fetch_queries.append(query)
        if query == "15500st blue":
            html = """
            {
              "listings": [
                {
                  "title": "15500ST Blue Dial 2021 Full Set HKD 520000",
                  "companyName": "Dealer Blue",
                  "repostedAt": "2026-06-13 10:00:00",
                  "number": 111,
                  "frontImage": "https://watchfacts.example/15500st-blue.jpg"
                },
                {
                  "title": "15500ST Black Dial 2021 Full Set HKD 500000",
                  "companyName": "Dealer Black",
                  "repostedAt": "2026-06-13 10:00:00",
                  "number": 112,
                  "frontImage": "https://watchfacts.example/15500st-black.jpg"
                }
              ]
            }
            """
        elif query == "15500st":
            html = """
            {
              "listings": [
                {
                  "title": "Audemars Piguet 15500ST.OO.1220ST.01 Blue Dial 2020 HKD 500000",
                  "companyName": "Dealer Ref",
                  "repostedAt": "2026-06-12 10:00:00",
                  "number": 222,
                  "frontImage": "https://watchfacts.example/15500st-ref-blue.jpg"
                },
                {
                  "title": "Audemars Piguet 15510ST Blue Dial 2020 HKD 470000",
                  "companyName": "Dealer Other Ref",
                  "repostedAt": "2026-06-12 10:00:00",
                  "number": 223,
                  "frontImage": "https://watchfacts.example/15510st-blue.jpg"
                }
              ]
            }
            """
        else:
            html = """
            {
              "listings": [
                {
                  "title": "Royal Oak 15500ST Blue Dial 2019 Full Set HKD 490000",
                  "companyName": "Dealer Royal Oak",
                  "repostedAt": "2026-06-11 10:00:00",
                  "number": 333,
                  "frontImage": "https://watchfacts.example/royal-oak-15500st-blue.jpg"
                }
              ]
            }
            """
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("15500st blue"))
    result_texts = {result.listing_text for result in results}

    assert fetch_queries == [
        "15500st blue",
        "15500st",
        "royal oak 15500st blue",
    ]
    assert result_texts == {
        "15500ST Blue Dial 2021 Full Set HKD 520000",
        "Audemars Piguet 15500ST.OO.1220ST.01 Blue Dial 2020 HKD 500000",
        "15500ST Blue Dial 2019 Full Set HKD 490000",
    }
    assert all("Black Dial" not in result.listing_text for result in results)
    assert all("15510ST" not in result.listing_text for result in results)
    assert workflow.last_search_diagnostics is not None
    diagnostics_payload = workflow.last_search_diagnostics.to_payload()
    assert diagnostics_payload["retrieval_query_count"] == 3
    assert diagnostics_payload["retrieval_queries"] == [
        "15500st blue",
        "15500st",
        "royal oak 15500st blue",
    ]
    assert "retrieval.collection_expansion:royal_oak" in diagnostics_payload[
        "retrieval_reason_codes"
    ]


def test_search_workflow_skips_15500st_blue_fallback_when_primary_is_sufficient(
    tmp_path,
) -> None:
    settings = make_settings(tmp_path)
    fetch_queries: list[str | None] = []

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        fetch_queries.append(query)
        if query != "15500st blue":
            raise AssertionError(f"unexpected fallback fetch: {query}")
        html = """
        {
          "listings": [
            {
              "title": "15500ST Blue Dial 2026 Full Set HKD 550000",
              "companyName": "Dealer 1",
              "repostedAt": "2026-06-13 10:00:00",
              "number": 201,
              "frontImage": "https://watchfacts.example/15500st-blue-1.jpg"
            },
            {
              "title": "15500ST Blue Dial 2025 Full Set HKD 540000",
              "companyName": "Dealer 2",
              "repostedAt": "2026-06-13 10:00:00",
              "number": 202,
              "frontImage": "https://watchfacts.example/15500st-blue-2.jpg"
            },
            {
              "title": "15500ST Blue Dial 2024 Full Set HKD 530000",
              "companyName": "Dealer 3",
              "repostedAt": "2026-06-13 10:00:00",
              "number": 203,
              "frontImage": "https://watchfacts.example/15500st-blue-3.jpg"
            },
            {
              "title": "15500ST Blue Dial 2023 Full Set HKD 520000",
              "companyName": "Dealer 4",
              "repostedAt": "2026-06-13 10:00:00",
              "number": 204,
              "frontImage": "https://watchfacts.example/15500st-blue-4.jpg"
            },
            {
              "title": "15500ST Blue Dial 2022 Full Set HKD 510000",
              "companyName": "Dealer 5",
              "repostedAt": "2026-06-13 10:00:00",
              "number": 205,
              "frontImage": "https://watchfacts.example/15500st-blue-5.jpg"
            }
          ]
        }
        """
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("15500st blue"))

    assert fetch_queries == ["15500st blue"]
    assert len(results) == 5
    assert workflow.last_search_diagnostics is not None
    diagnostics_payload = workflow.last_search_diagnostics.to_payload()
    assert diagnostics_payload["retrieval_query_count"] == 1
    assert diagnostics_payload["retrieval_queries"] == ["15500st blue"]
    assert diagnostics_payload["retrieval_timings"][0]["matched_count"] == 5
    assert "retrieval.conditional_fallback_skipped" in diagnostics_payload[
        "retrieval_reason_codes"
    ]


@pytest.mark.parametrize("query", ["rolex 15500st blue", "15500st blue leather"])
def test_search_workflow_does_not_expand_15500st_blue_when_extra_descriptors_change_intent(
    tmp_path,
    query: str,
) -> None:
    settings = make_settings(tmp_path)
    fetch_queries: list[str | None] = []

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        fetch_queries.append(query)
        html = """
        {
          "listings": [
            {
              "title": "Audemars Piguet 15500ST.OO.1220ST.01 Blue Dial 2020 HKD 500000",
              "companyName": "Dealer Ref",
              "repostedAt": "2026-06-12 10:00:00",
              "number": 222,
              "frontImage": "https://watchfacts.example/15500st-ref-blue.jpg"
            }
          ]
        }
        """
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search(query))

    assert fetch_queries == ["15500st"]
    assert results == []
    assert workflow.last_search_diagnostics is not None
    assert workflow.last_search_diagnostics.retrieval_reason_codes == (
        "retrieval.reference_with_descriptors",
    )


@pytest.mark.parametrize(
    ("query", "expected_fetch_queries"),
    [
        (
            "ap 15500st blue",
            ("ap 15500st blue", "15500st", "royal oak 15500st blue"),
        ),
        (
            "audemars piguet 15500st blue",
            (
                "audemars piguet 15500st blue",
                "15500st",
                "royal oak 15500st blue",
            ),
        ),
        ("royal oak 15500st blue", ("royal oak 15500st blue", "15500st")),
        ("Royal Oak 15500ST Blue", ("Royal Oak 15500ST Blue", "15500st")),
    ],
)
def test_retrieval_plan_allows_safe_15500st_blue_context_descriptors(
    query: str,
    expected_fetch_queries: tuple[str, ...],
) -> None:
    query_plan = search_module.build_query_plan(query)
    retrieval_plan = search_module._build_retrieval_plan(query, query_plan)

    assert retrieval_plan.fetch_queries == (query,)
    assert retrieval_plan.fallback_fetch_queries == expected_fetch_queries[1:]
    assert retrieval_plan.fallback_min_matched_count == 5
    assert retrieval_plan.local_filter_queries == (query, "15500st blue")
    assert retrieval_plan.strict_local_filter is True


def test_retrieval_plan_reports_duplicate_15500st_branch_skip() -> None:
    query_plan = search_module.build_query_plan("Royal Oak 15500ST Blue")
    retrieval_plan = search_module._build_retrieval_plan(
        "Royal Oak 15500ST Blue",
        query_plan,
    )

    assert retrieval_plan.fetch_queries == ("Royal Oak 15500ST Blue",)
    assert retrieval_plan.fallback_fetch_queries == ("15500st",)
    assert "retrieval.duplicate_branch_skipped" in retrieval_plan.reason_codes


@pytest.mark.parametrize(
    ("query", "expected_fallback_query"),
    [
        ("rp journe elegante titanium", "fpj elegante titanium"),
        ("f.p. journe elegante titanium", "fpj elegante titanium"),
        ("f.p.journe elegante titanium", "fpj elegante titanium"),
    ],
)
def test_retrieval_plan_uses_brand_alias_fallback_for_fp_journe(
    query: str,
    expected_fallback_query: str,
) -> None:
    query_plan = search_module.build_query_plan(query)
    retrieval_plan = search_module._build_retrieval_plan(query, query_plan)

    assert retrieval_plan.fetch_queries == (query,)
    assert retrieval_plan.fallback_fetch_queries == (expected_fallback_query,)
    assert retrieval_plan.fallback_min_matched_count == 1
    assert retrieval_plan.local_filter_queries == (
        query,
        expected_fallback_query,
    )
    assert retrieval_plan.strict_local_filter is True
    assert "retrieval.conditional_fallback:fp_journe" in retrieval_plan.reason_codes


def test_search_workflow_uses_fp_journe_brand_alias_retrieval_fallback(tmp_path) -> None:
    settings = make_settings(tmp_path)
    fetch_queries: list[str | None] = []

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        fetch_queries.append(query)
        if query == "rp journe elegante titanium":
            html = """
            {
              "listings": []
            }
            """
        else:
            html = """
        {
          "listings": [
            {
              "title": "FPJ Elegante Titanium 48mm 2026 Full Set HKD 520000",
              "companyName": "Dealer FPJ",
              "repostedAt": "2026-06-14 10:00:00",
              "number": 404,
              "frontImage": "https://watchfacts.example/fpj-elegante.jpg"
            }
          ]
        }
        """
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)
    results = asyncio.run(workflow.search("rp journe elegante titanium"))
    result_texts = {result.listing_text for result in results}

    assert fetch_queries == ["rp journe elegante titanium", "fpj elegante titanium"]
    assert result_texts == {"FPJ Elegante Titanium 48mm 2026 Full Set HKD 520000"}
    assert workflow.last_search_diagnostics is not None
    diagnostics_payload = workflow.last_search_diagnostics.to_payload()
    assert diagnostics_payload["retrieval_query_count"] == 2
    assert diagnostics_payload["retrieval_queries"] == [
        "rp journe elegante titanium",
        "fpj elegante titanium",
    ]
    assert "retrieval.conditional_fallback_fetched" in diagnostics_payload[
        "retrieval_reason_codes"
    ]
    assert "retrieval.brand_alias_expansion:fp_journe" in diagnostics_payload[
        "retrieval_reason_codes"
    ]


def test_search_workflow_does_not_use_reference_only_fallback_for_multi_descriptor_query(
    tmp_path,
) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "rm07-01 Pink ceramic 2020y Full set 312K usdt",
          "companyName": "Member 5805",
          "repostedAt": "2026-06-09 10:00:00",
          "number": 111,
          "frontImage": "https://watchfacts.example/rm07-pink.jpg"
        },
        {
          "title": "RM07-01 WG Snow Onyx N4-26 360,000 USDT",
          "companyName": "member 656225",
          "repostedAt": "2026-06-01 10:00:00",
          "number": 222,
          "frontImage": "https://watchfacts.example/rm07-wg-snow.jpg"
        },
        {
          "title": "6/20 Rm07-01 gold tpt 2026/06 1.98mill hkd",
          "companyName": "Thomas Glory Watch Limited",
          "repostedAt": "2026-05-30 10:00:00",
          "number": 333,
          "frontImage": "https://watchfacts.example/rm07-gold-tpt.jpg"
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("rm07-01 rg snow"))

    assert results == []
    assert workflow.last_search_diagnostics is not None
    assert workflow.last_search_diagnostics.matched_count == 0


def test_search_workflow_allows_reference_only_fallback_for_optional_year_descriptor(
    tmp_path,
) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "126500LN White Daytona full set 279000 HKD",
          "companyName": "Dealer A",
          "repostedAt": "2026-06-01 10:00:00",
          "number": 111,
          "frontImage": "https://watchfacts.example/126500ln-white.jpg"
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("126500ln white 2026"))

    assert [result.listing_text for result in results] == [
        "126500LN White Daytona full set 279000 HKD"
    ]
    assert workflow.last_search_diagnostics is not None
    assert workflow.last_search_diagnostics.query_intent == "reference_with_year"
    assert workflow.last_search_diagnostics.required_descriptor_tokens == ("white",)
    assert workflow.last_search_diagnostics.optional_descriptor_tokens == ("2026",)


def test_search_workflow_exposes_raw_context_used_segment_reason(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "MODEL: PANDA DAYTONA REF: 126500LN YEAR: 2026 CONDITION: UNWORN COMES AS: W&C + WHITE TAG PRICE: 27350 USD",
          "companyName": "Dealer A",
          "repostedAt": "2026-03-26 10:00:00",
          "number": 126500
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("126500ln white 2026"))

    assert len(results) == 1
    assert results[0].scope_reason == "scope.scoped"
    assert "raw_context.used:panda" in results[0].segment_reason_codes


def test_to_search_result_exposes_raw_context_ignored_segment_reason() -> None:
    result = search_module._to_search_result(
        "126500ln white 2026",
        ListingCandidate(
            listing_text=(
                "MODEL: PANDA DAYTONA REF: 126500LN YEAR: 2026 "
                "CONDITION: UNWORN PRICE: 27350 USD"
            ),
        ),
    )

    assert result.scope_reason == "scope.scoped"
    assert "raw_context.ignored:panda" in result.segment_reason_codes


def test_search_workflow_matches_server_filtered_compound_material_phrases(
    tmp_path,
) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "RM07-01 RG Medset Black Lips Used 2018 / 204k usdt",
          "companyName": "Dealer A",
          "repostedAt": "2026-06-13 10:00:00",
          "number": 111,
          "frontImage": "https://watchfacts.example/rm07-rg.jpg"
        },
        {
          "title": "RM07-01 Rose Gold Medset Likenew 2021 Fullset 199000USDT",
          "companyName": "Dealer B",
          "repostedAt": "2026-06-12 10:00:00",
          "number": 222,
          "frontImage": "https://watchfacts.example/rm07-rose-gold.jpg"
        },
        {
          "title": "RM07-01 WG Medset Red Lips Used 2020 - 195k usdt",
          "companyName": "Dealer C",
          "repostedAt": "2026-06-11 10:00:00",
          "number": 333,
          "frontImage": "https://watchfacts.example/rm07-wg.jpg"
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("rm07-01 rose gold"))

    assert [result.listing_text for result in results] == [
        "RM07-01 RG Medset Black Lips Used 2018 / 204k usdt",
        "RM07-01 Rose Gold Medset Likenew 2021 Fullset 199000USDT",
    ]
    assert workflow.last_search_diagnostics is not None
    assert workflow.last_search_diagnostics.required_descriptor_tokens == ("rg",)


def test_search_workflow_refilters_server_filtered_non_color_variant_descriptor_alias(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "228349RBR A METE 2024 $610000",
          "companyName": "Member 1000",
          "number": 222
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("228349rbr meteorite"))

    assert [result.listing_text for result in results] == [
        "228349RBR A METE 2024 $610000"
    ]


def test_server_filtered_query_matching_policy() -> None:
    assert (
        search_module._server_filtered_query_matching_policy(
            "228349rbr mete",
            search_module._color_descriptors("228349rbr mete"),
        )
        == "strict_non_color_descriptor"
    )
    assert (
        search_module._server_filtered_query_matching_policy(
            "228349rbr meteorite",
            search_module._color_descriptors("228349rbr meteorite"),
        )
        == "strict_non_color_descriptor"
    )
    assert (
        search_module._server_filtered_query_requires_local_matching(
            "228349rbr mete",
            search_module._color_descriptors("228349rbr mete"),
        )
        is True
    )

    assert (
        search_module._server_filtered_query_matching_policy(
            "228235A choco",
            search_module._color_descriptors("228235A choco"),
        )
        == "strict_color_alias"
    )
    assert (
        search_module._server_filtered_query_requires_local_matching(
            "228235A choco",
            search_module._color_descriptors("228235A choco"),
        )
        is True
    )

    assert (
        search_module._server_filtered_query_matching_policy(
            "116500 panda",
            search_module._color_descriptors("116500 panda"),
        )
        == "coarse_pass_through_alias"
    )
    assert (
        search_module._server_filtered_query_requires_local_matching(
            "116500 panda",
            search_module._color_descriptors("116500 panda"),
        )
        is False
    )

    assert (
        search_module._server_filtered_query_matching_policy(
            "126500ln white",
            search_module._color_descriptors("126500ln white"),
        )
        == "strict_non_color_descriptor"
    )
    assert (
        search_module._server_filtered_query_requires_local_matching(
            "126500ln white",
            search_module._color_descriptors("126500ln white"),
        )
        is True
    )


def test_search_workflow_refilters_server_filtered_alias_plus_noncolor_descriptor(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "116500 panda 30.5k",
          "companyName": "Dealer A",
          "number": 2
        },
        {
          "title": "116500 mete 31.5k",
          "companyName": "Dealer B",
          "number": 3
        },
        {
          "title": "116500 white dial 31.5k",
          "companyName": "Dealer C",
          "number": 4
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("116500 panda mete"))

    assert [result.listing_text for result in results] == [
        "116500 mete 31.5k"
    ]


def test_search_workflow_demotes_missing_price_result_when_priced_results_exist(
    tmp_path,
) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "5205r 2026",
          "companyName": "H",
          "repostedAt": "2026-05-18 10:00:00",
          "number": 1
        },
        {
          "title": "5205R 2026-04 $428000",
          "companyName": "Sally",
          "repostedAt": "2026-05-17 10:00:00",
          "number": 2
        },
        {
          "title": "5205r 2026/3 $435,000",
          "companyName": "Hugh",
          "repostedAt": "2026-03-16 10:00:00",
          "number": 3
        },
        {
          "title": "5205R 2026-04 436k HKD",
          "companyName": "Mr Et",
          "repostedAt": "2026-05-10 10:00:00",
          "number": 4
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("5205r 2026"))

    assert [result.listing_text for result in results] == [
        "5205R 2026-04 $428000",
        "5205R 2026-04 436k HKD",
        "5205r 2026/3 $435,000",
        "5205r 2026",
    ]


def test_search_workflow_expands_sparse_year_query_and_refilters_locally(tmp_path) -> None:
    settings = make_settings(tmp_path)
    fetch_queries: list[str | None] = []

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        fetch_queries.append(query)
        if query == "126500ln white 2026":
            html = """
            {
              "listings": [
                {
                  "title": "Rolex 126500ln white 2026 n1 HKD 273000",
                  "companyName": "AP",
                  "repostedAt": "2026-03-08 10:00:00",
                  "number": 1
                }
              ]
            }
            """
        else:
            html = """
            {
              "listings": [
                {
                  "title": "126500LN White N3/2026 HK$279000 without box",
                  "companyName": "Dealer A",
                  "repostedAt": "2026-03-03 10:00:00",
                  "number": 2
                },
                {
                  "title": "126500LN Black N3/2026 HK$236000",
                  "companyName": "Dealer B",
                  "repostedAt": "2026-03-03 10:00:00",
                  "number": 3
                }
              ]
            }
            """
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("126500ln white 2026"))

    assert fetch_queries == ["126500ln white 2026", "126500ln white"]
    assert [result.listing_text for result in results] == [
        "Rolex 126500ln white 2026 n1 HKD 273000",
        "126500LN White N3/2026 HK$279000 without box",
    ]


def test_search_workflow_drops_server_filtered_conflicting_color_descriptor(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "126500LN White N3/2026 HK$279000 without box",
          "companyName": "Dealer A",
          "repostedAt": "2026-03-03 10:00:00",
          "number": 2
        },
        {
          "title": "126500LN Black N3/2026 HK$236000",
          "companyName": "Dealer B",
          "repostedAt": "2026-03-03 10:00:00",
          "number": 3
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("126500ln white 2026"))

    assert [result.listing_text for result in results] == [
        "126500LN White N3/2026 HK$279000 without box",
    ]


def test_search_workflow_keeps_server_filtered_panda_alias_results(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "116500 panda 30.5k",
          "companyName": "Dealer A",
          "repostedAt": "2026-04-01 10:00:00",
          "number": 2
        },
        {
          "title": "116500 white dial 31.5k",
          "companyName": "Dealer B",
          "repostedAt": "2026-04-01 10:00:00",
          "number": 3
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("116500 panda"))

    assert [result.listing_text for result in results] == [
        "116500 panda 30.5k",
        "116500 white dial 31.5k",
    ]


def test_search_workflow_omits_bundle_images_for_multi_listing_cards(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    <html>
      <body>
        <div class="product">
          <a href="/flash-sales/1">
            <img src="https://watchfacts.example/watch-bundle.jpg" />
          </a>
          <div class="product-description">
            <a class="title-link" href="/flash-sales/1">
              124200 pistachio $60000 N12
              126303g black oys $128000 N8
              126331g sundust jub $155500 N3
              126334 blue jub $116500 N2
              7118/1200A blue N2/2026y 725k hkd
              7300/1200R white 03/2026 $366k
              5726/1A blue N9/2025y 1.065m hkd
            </a>
          </div>
          <span data-field="seller">Forest</span>
          <time data-field="posted-date">April 23, 2026</time>
        </div>
      </body>
    </html>
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("7118/1200a blue"))

    assert len(results) == 1
    assert results[0].listing_text == "7118/1200A blue N2/2026y 725k hkd"
    assert results[0].image_url is None


def test_search_workflow_records_suspicious_incomplete_results(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    <html>
      <body>
        <div class="product">
          <div class="product-description">
            <a class="title-link" href="/flash-sales/9927122">
              ✅PP ❣️5711R Watch and Service paper, HKD 605000
              ❣️5712R 2016/ HKD
              ❣️5134R Service paper, HKD 130000
            </a>
          </div>
          <span data-field="seller">AM.Timepiece TONY</span>
          <time data-field="posted-date">February 14, 2026</time>
        </div>
      </body>
    </html>
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    database = Database(settings.db_path)
    workflow = WatchFactsSearchWorkflow(
        settings,
        database=database,
        fetch_html=fetch_html,
    )

    results = asyncio.run(workflow.search("5712r"))
    issues = database.list_open_suspicious_issues()

    assert results[0].listing_text == "5712R 2016/ HKD"
    assert {issue.issue_type for issue in issues} == {"suspicious"}
    assert {issue.reason for issue in issues} >= {
        "ends_with_currency",
        "missing_price_after_currency",
    }
    assert {issue.listing_text for issue in issues} == {"5712R 2016/ HKD"}


def test_search_workflow_scopes_variant_reference_and_omits_bundle_image(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    <html>
      <body>
        <div class="product">
          <a href="/flash-sales/2">
            <img src="https://watchfacts.example/wrong-first-product.jpg" />
          </a>
          <div class="product-description">
            <a class="title-link" href="/flash-sales/2">
              PP 7130G-016 Paper of 2022 USD31000
              PP7010G-013, 2025 model, full set price: US$63,000
              5726/1A-014 2021 Full Set: US$115,000
            </a>
          </div>
          <span data-field="seller">HL</span>
          <time data-field="posted-date">May 9, 2026</time>
        </div>
      </body>
    </html>
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("5726/1a"))

    assert len(results) == 1
    assert results[0].listing_text == "5726/1A-014 2021 Full Set: US$115,000"
    assert results[0].seller == "HL"
    assert results[0].image_url is None


def test_server_filtered_json_stock_list_scopes_reference_and_omits_bundle_image(
    tmp_path,
) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "HK STOCK LIST 116505 aftermarket rainbow watch only → 284k 5712g new 2024 → 115k 116500 panda 2025 → 31k",
          "companyName": "Mr Et",
          "repostedAt": "2026-06-10 10:00:00",
          "frontImage": "https://watchfacts.example/stock-list-cover.jpg",
          "number": 9714092
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("5712g"))

    assert len(results) == 1
    assert results[0].listing_text == "5712g new 2024 → 115k"
    assert results[0].raw_listing_text == (
        "HK STOCK LIST 116505 aftermarket rainbow watch only → 284k "
        "5712g new 2024 → 115k 116500 panda 2025 → 31k"
    )
    assert results[0].seller == "Mr Et"
    assert results[0].image_url is None
    assert results[0].scope_reason == "scope.stock_list"
    assert results[0].image_reason == "image.omitted_bundle_ambiguous"
    assert results[0].price_reason == "price.visible"
    assert results[0].segment_reason_codes == (
        "segment.stock_list_marker",
        "segment.reference_boundary",
    )


def test_server_filtered_json_stock_list_exposes_excluded_raw_context_reason(
    tmp_path,
) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "HK STOCK LIST 116500LN black 2025 31k 126500LN panda 2026 W&C + WHITE TAG 35k",
          "companyName": "Mr Et",
          "repostedAt": "2026-06-10 10:00:00",
          "frontImage": "https://watchfacts.example/stock-list-cover.jpg",
          "number": 9714092
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("126500ln white 2026"))

    assert len(results) == 1
    assert results[0].scope_reason == "scope.stock_list"
    assert results[0].segment_reason_codes == (
        "segment.stock_list_marker",
        "segment.reference_boundary",
        "raw_context.excluded_stock_list:panda",
    )


def test_search_workflow_logs_counts_without_query_or_state_path(tmp_path, caplog) -> None:
    settings = make_settings(tmp_path)

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(html=FIXTURE.read_text(), final_url=settings.watchfacts_url)

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    with caplog.at_level(logging.INFO, logger="app.search"):
        asyncio.run(workflow.search("228253a choco"))

    log_text = caplog.text
    assert "event=query.start query_length=13" in log_text
    assert "event=query.end parsed_count=2 matched_count=1 result_count=1" in log_text
    assert "228253a choco" not in log_text
    assert str(settings.browser_state_path) not in log_text
    assert settings.telegram_bot_token not in log_text


def test_search_workflow_logs_error_type_without_query_or_state_path(tmp_path, caplog) -> None:
    settings = make_settings(tmp_path)

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        raise RuntimeError("network unavailable")

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    with caplog.at_level(logging.INFO, logger="app.search"):
        try:
            asyncio.run(workflow.search("228253a choco"))
        except RuntimeError:
            pass

    log_text = caplog.text
    assert "event=query.error error_type=RuntimeError" in log_text
    assert "228253a choco" not in log_text
    assert str(settings.browser_state_path) not in log_text
    assert settings.telegram_bot_token not in log_text


def test_search_workflow_refines_results_with_openai_when_enabled(tmp_path) -> None:
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
    html = """
    <html>
      <body>
        <div class="product">
          <div class="product-description">
            <a class="title-link" href="/flash-sales/3">
              FPJ quantieme perpetuel platinum 2022 used Fullset $298,500USD - [ ]
              FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd - [ ]
              FPJ Rose Gold CS opendate watch with card $130,000USD
            </a>
          </div>
          <span data-field="seller">Member 9058</span>
          <time data-field="posted-date">March 28, 2026</time>
        </div>
      </body>
    </html>
    """
    refine_calls: list[tuple[str, list[SearchResult]]] = []

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    async def refine_results(query: str, results: list[SearchResult]) -> list[SearchResult]:
        refine_calls.append((query, results))
        return [
            SearchResult(
                listing_text="FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd",
                seller=results[0].seller,
                posted_date=results[0].posted_date,
                image_url=results[0].image_url,
                source_url=results[0].source_url,
            )
        ]

    workflow = WatchFactsSearchWorkflow(
        settings,
        fetch_html=fetch_html,
        refine_results=refine_results,
    )

    results = asyncio.run(workflow.search("Fpj Elegante Titanium"))

    assert refine_calls
    assert results[0].listing_text == "FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd"


def test_search_workflow_dedupes_again_after_openai_refine(tmp_path) -> None:
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
    html = """
    {
      "listings": [
        {
          "title": "FPJ quantieme perpetuel - [ ] FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd - [ ] FPJ Rose Gold CS opendate watch",
          "companyName": "Member 9058",
          "repostedAt": "2026-03-22 10:00:00",
          "number": 10
        },
        {
          "title": "FPJ quantieme perpetuel platinum - [ ] FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd - [ ] FPJ Rose Gold CS",
          "companyName": "Member 9058",
          "repostedAt": "2026-03-28 10:00:00",
          "number": 11
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    async def refine_results(_: str, results: list[SearchResult]) -> list[SearchResult]:
        return [
            SearchResult(
                listing_text="FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd",
                seller=result.seller,
                posted_date=result.posted_date,
                image_url=result.image_url,
                source_url=result.source_url,
            )
            for result in results
        ]

    workflow = WatchFactsSearchWorkflow(
        settings,
        fetch_html=fetch_html,
        refine_results=refine_results,
    )

    results = asyncio.run(workflow.search("Fpj Elegante Titanium"))

    assert len(results) == 1
    assert results[0].posted_date == "March 28, 2026"
    assert results[0].source_url == "/flash-sales/11"


def test_search_workflow_records_shadow_ai_suggestions_without_changing_results(
    tmp_path,
) -> None:
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
    html = """
    {
      "listings": [
        {
          "title": "FPJ quantieme perpetuel / FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd / FPJ Rose Gold CS",
          "companyName": "Member 9058",
          "repostedAt": "2026-03-28 10:00:00",
          "number": 11
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    async def refine_results(_: str, results: list[SearchResult]) -> list[SearchResult]:
        return [
            SearchResult(
                listing_text="FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd",
                seller=result.seller,
                posted_date=result.posted_date,
                image_url=result.image_url,
                source_url=result.source_url,
                raw_listing_text=result.raw_listing_text,
            )
            for result in results
        ]

    database = Database(settings.db_path)
    workflow = WatchFactsSearchWorkflow(
        settings,
        database=database,
        fetch_html=fetch_html,
        refine_results=refine_results,
    )

    results = asyncio.run(workflow.search("Fpj Elegante Titanium"))
    suggestions = database.list_ai_refinement_suggestions()

    assert results[0].listing_text.endswith("FPJ Rose Gold CS")
    assert suggestions[0].mode == "shadow"
    assert suggestions[0].gate_status == "accepted"
    assert suggestions[0].deterministic_text == results[0].listing_text
    assert suggestions[0].suggested_text == (
        "FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd"
    )


def test_search_workflow_records_guarded_ai_suggestions_and_applies_safe_result(
    tmp_path,
) -> None:
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
    html = """
    {
      "listings": [
        {
          "title": "FPJ quantieme perpetuel / FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd / FPJ Rose Gold CS",
          "companyName": "Seller",
          "number": 12
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    async def refine_results(_: str, results: list[SearchResult]) -> list[SearchResult]:
        return [
            SearchResult(
                listing_text="FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd",
                seller=result.seller,
                posted_date=result.posted_date,
                image_url=result.image_url,
                source_url=result.source_url,
                raw_listing_text=result.raw_listing_text,
            )
            for result in results
        ]

    database = Database(settings.db_path)
    workflow = WatchFactsSearchWorkflow(
        settings,
        database=database,
        fetch_html=fetch_html,
        refine_results=refine_results,
    )

    results = asyncio.run(workflow.search("Fpj Elegante Titanium"))
    suggestions = database.list_ai_refinement_suggestions()

    assert results[0].listing_text == (
        "FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd"
    )
    assert suggestions[0].mode == "guarded"
    assert suggestions[0].gate_status == "accepted"


def test_search_workflow_final_dedupe_keeps_newest_when_text_matches_across_sellers(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "FPJ Elegante titanium ti",
          "companyName": "A",
          "repostedAt": "2026-04-03 10:00:00",
          "number": 20
        },
        {
          "title": "FPJ Elegante titanium ti",
          "companyName": "Chris",
          "repostedAt": "2026-04-05 10:00:00",
          "number": 21
        },
        {
          "title": "FPJ Elegante titanium ti",
          "companyName": "KI",
          "repostedAt": "2026-03-30 10:00:00",
          "number": 22
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("Fpj Elegante Titanium"))

    assert len(results) == 1
    assert results[0].seller == "Chris"
    assert results[0].posted_date == "April 5, 2026"
    assert results[0].source_url == "/flash-sales/21"


def test_search_workflow_blocks_short_model_final_phrase_miss() -> None:
    events: list[search_module.SearchAuditEvent] = []
    query_intent = search_module.classify_query_intent("Lange 1")
    clear_lange_1 = SearchResult(
        "1 Series 101.031 Watch LANGE 1 101.031 38.5 mm watch only 24500usd",
        posted_date="May 14, 2026",
    )
    zeitwerk_false_positive = SearchResult(
        "1,163,000 145.032 Zeitwerk, Used Full set | HKD 821,000 Lange Zeitwerk",
        seller="Member 6685",
        posted_date="May 28, 2026",
        raw_listing_text=(
            "Rolex and others 336938 green Jub 540000 hkd "
            "1,163,000 145.032 Zeitwerk, Used Full set | HKD 821,000 Lange Zeitwerk"
        ),
    )
    results = [clear_lange_1, zeitwerk_false_positive]

    blocked_count = WatchFactsSearchWorkflow._audit_and_filter_blocked_final_results(
        events,
        query="Lange 1",
        query_intent=query_intent,
        results=results,
    )

    assert blocked_count == 1
    assert results == [clear_lange_1]
    blocked_events = [
        event
        for event in events
        if event.stage == "blocked_final"
    ]
    assert len(blocked_events) == 1
    assert blocked_events[0].decision == "exclude"
    assert blocked_events[0].guardrail_action == "block_from_final"
    assert "guardrail.brand_model_phrase_missing" in blocked_events[0].reason_codes


def test_search_workflow_keeps_short_model_when_raw_phrase_is_local() -> None:
    events: list[search_module.SearchAuditEvent] = []
    query_intent = search_module.classify_query_intent("Lange 1")
    extracted = SearchResult(
        "1 Series 139.032 watch only 28900usd",
        raw_listing_text=(
            "A Lange LANGE 1 Series 139.032 watch only 28900usd "
            "Lange Zeitwerk 821000 hkd"
        ),
    )
    results = [extracted]

    blocked_count = WatchFactsSearchWorkflow._audit_and_filter_blocked_final_results(
        events,
        query="Lange 1",
        query_intent=query_intent,
        results=results,
    )

    assert blocked_count == 0
    assert results == [extracted]
    assert events == []


def test_search_workflow_blocks_short_model_when_raw_phrase_is_distant() -> None:
    events: list[search_module.SearchAuditEvent] = []
    query_intent = search_module.classify_query_intent("Lange 1")
    distant_false_positive = SearchResult(
        "1,163,000 145.032 Zeitwerk | HKD 821,000 Lange Zeitwerk",
        raw_listing_text=(
            "A Lange LANGE 1 Series 139.032 watch only 28900usd "
            "Rolex 336938 green 540000 hkd Patek 5712R 820000 hkd "
            "1,163,000 145.032 Zeitwerk | HKD 821,000 Lange Zeitwerk"
        ),
    )
    results = [distant_false_positive]

    blocked_count = WatchFactsSearchWorkflow._audit_and_filter_blocked_final_results(
        events,
        query="Lange 1",
        query_intent=query_intent,
        results=results,
    )

    assert blocked_count == 1
    assert results == []
    assert events[0].stage == "blocked_final"


def test_search_workflow_blocks_short_model_when_candidate_starts_at_previous_price() -> None:
    events: list[search_module.SearchAuditEvent] = []
    query_intent = search_module.classify_query_intent("Lange 1")
    price_leak_false_positive = SearchResult(
        "1,163,000 145.032 Zeitwerk Used Full set HKD 821,000 Lange Zeitwerk",
        raw_listing_text=(
            "425.025 Lange 1 brand new full set 2024 HKD 1,163,000 "
            "145.032 Zeitwerk Used Full set HKD 821,000 Lange Zeitwerk"
        ),
    )
    results = [price_leak_false_positive]

    blocked_count = WatchFactsSearchWorkflow._audit_and_filter_blocked_final_results(
        events,
        query="Lange 1",
        query_intent=query_intent,
        results=results,
    )

    assert blocked_count == 1
    assert results == []
    assert events[0].reason_codes == (
        "guardrail.brand_model_phrase_missing",
        "blocked.short_model_phrase_missing",
    )
