from __future__ import annotations

import asyncio
import json

import pytest

from app.config import load_search_settings
from app.db import Database
from app.openwa_handoff import OpenWAChatDraftResponse
from app.scraper import BrowserSessionStatus
from app.search_contracts import validate_search_payload
from app.search_result import SearchResult
from app.tool_runtime import (
    watchfacts_create_chat_draft_payload,
    watchfacts_get_issue_payload,
    watchfacts_health_payload,
    _RESULT_CACHE,
    watchfacts_list_issues_payload,
    watchfacts_report_issue_payload,
    watchfacts_search_payload,
    watchfacts_suspicious_summary_payload,
    watchfacts_update_issue_payload,
)
from app.watchfacts_http import WatchFactsHttpClientStatus


class FakeWorkflow:
    def __init__(self, results: list[SearchResult]) -> None:
        self.results = results
        self.queries: list[str] = []

    async def search(self, query: str) -> list[SearchResult]:
        self.queries.append(query)
        return self.results


def test_watchfacts_search_payload_includes_hybrid_refiner_when_enabled(tmp_path, monkeypatch) -> None:
    settings = load_search_settings(
        env={
            "HYBRID_AI_MODE": "guarded",
            "OPENAI_API_KEY": "test-key",
        },
        project_root=tmp_path,
    )
    captured_refiners: list[object] = []

    class FakeRefinedWorkflow:
        def __init__(
            self,
            settings_arg,
            *,
            database=None,
            fetch_html=None,
            refine_results=None,
        ) -> None:
            self.settings = settings_arg
            self.refine_results = refine_results

        async def search(self, query: str) -> list[SearchResult]:
            assert self.refine_results is not None
            captured_refiners.append(self.refine_results)
            return [SearchResult("5712G Used")]

    monkeypatch.setattr(
        "app.tool_runtime.WatchFactsSearchWorkflow",
        FakeRefinedWorkflow,
    )

    payload = asyncio.run(
        watchfacts_search_payload(
            "5712g",
            settings=settings,
        )
    )

    assert payload["query"] == "5712g"
    assert payload["results"][0]["listing_text"] == "5712G Used"
    assert len(captured_refiners) == 1
    assert captured_refiners[0] is not None


def test_watchfacts_search_payload_serializes_results_for_tool_runtime(tmp_path) -> None:
    settings = load_search_settings(env={}, project_root=tmp_path)
    workflow = FakeWorkflow(
        [
            SearchResult(
                listing_text="5712G Used 2015 - 76k usdt",
                seller="Issac",
                seller_phone="17826241887",
                raw_listing_text="raw listing text",
                similar_results=(SearchResult("5712G similar"),),
            ),
            SearchResult("5712R 2016 HKD 830000"),
        ]
    )
    workflow.last_search_diagnostics = {
        "parsed_count": 4,
        "matched_count": 3,
        "search_result_count": 3,
        "unique_latest_count": 2,
        "unique_text_count": 2,
        "final_count": 2,
        "server_filtered": True,
        "playwright_fallback": False,
        "cache_hit": False,
        "source_truncation_suspected": False,
    }

    payload = asyncio.run(
        watchfacts_search_payload(
            " 5712g ",
            workflow=workflow,
            settings=settings,
            limit=1,
            include_similar=False,
            include_raw=False,
        )
    )

    assert workflow.queries == ["5712g"]
    assert payload["query"] == "5712g"
    assert payload["total_count"] == 2
    assert payload["offset"] == 0
    assert payload["limit"] == 1
    assert payload["result_count"] == 1
    assert payload["truncated"] is True
    assert payload["has_more"] is True
    assert payload["next_offset"] == 1
    assert payload["result_cache_ttl_seconds"] == settings.search_cache_ttl_seconds
    assert payload["search_diagnostics"] == workflow.last_search_diagnostics
    result = payload["results"][0]
    assert result["rank"] == 1
    assert result["result_id"].startswith("watchfacts:")
    assert result["source_result_id"] == result["result_id"]
    assert result["stable_listing_id"].startswith("watchfacts-listing:")
    assert result["listing_text"] == "5712G Used 2015 - 76k usdt"
    assert result["seller"] == "Issac"
    assert result["seller_phone"] == "17826241887"
    assert result["similar_results"] == []


def test_watchfacts_search_payload_preserves_mcp_contract_fields(tmp_path) -> None:
    settings = load_search_settings(env={}, project_root=tmp_path)
    workflow = FakeWorkflow(
        [
            SearchResult(
                listing_text="5205R Green New 2/2026 $417,000 HKD",
                seller="Mr Dain",
                posted_date="11/06/2026",
                source_url="/listing/5205r-green",
                image_url="/images/5205r-green.jpg",
                raw_listing_text="5205R Green New 2/2026 $417,000 HKD",
            ),
            SearchResult(
                listing_text="5205R green 2017 used fullset HK$360k",
                seller="BenGi",
                posted_date="05/06/2026",
                source_url="/listing/5205r-2017",
                image_url="/images/5205r-2017.jpg",
            ),
        ]
    )
    workflow.last_search_diagnostics = {
        "parsed_count": 26,
        "matched_count": 26,
        "final_count": 2,
        "server_filtered": True,
        "playwright_fallback": False,
        "cache_hit": False,
        "query_intent": "reference_with_descriptor",
        "weak_match_count": 0,
        "ambiguous_candidate_count": 0,
        "guardrail_action_counts": {"none": 2},
    }

    payload = asyncio.run(
        watchfacts_search_payload(
            "5205r green",
            workflow=workflow,
            settings=settings,
            limit=1,
            offset=0,
            include_similar=False,
            include_raw=False,
        )
    )

    assert validate_search_payload(payload) == []
    assert payload["query"] == "5205r green"
    assert payload["total_count"] == 2
    assert payload["result_count"] == 1
    assert payload["has_more"] is True
    assert payload["next_offset"] == 1
    assert payload["search_diagnostics"]["query_intent"] == "reference_with_descriptor"
    result = payload["results"][0]
    assert result["rank"] == 1
    assert result["result_id"].startswith("watchfacts:")
    assert result["source_result_id"] == result["result_id"]
    assert result["stable_listing_id"].startswith("watchfacts-listing:")
    assert result["source_url"] == "/listing/5205r-green"
    assert result["image_url"] == "/images/5205r-green.jpg"
    assert result["seller"] == "Mr Dain"
    assert result["posted_date"] == "11/06/2026"


def test_watchfacts_create_chat_draft_uses_db_reference_when_memory_cache_missing(
    tmp_path,
) -> None:
    settings = load_search_settings(
        env={
            "ENABLE_OPENWA_CHAT_HANDOFF": "true",
            "OPENWA_BASE_URL": "https://openwa.example",
            "OPENWA_API_KEY": "secret",
            "OPENWA_DASHBOARD_URL": "https://dashboard.example",
        },
        project_root=tmp_path,
    )
    search_workflow = FakeWorkflow(
        [
            SearchResult(
                listing_text="5712G Used 2015 - 76k usdt",
                seller="Issac",
                seller_phone="+86 178 2624 1887",
                source_url="/listing/1",
                image_url="/image/1.jpg",
                raw_listing_text="raw listing",
            )
        ]
    )
    search_payload = asyncio.run(
        watchfacts_search_payload(
            "5712g",
            workflow=search_workflow,
            settings=settings,
        )
    )
    result_id = search_payload["results"][0]["result_id"]

    original_cache = dict(_RESULT_CACHE)
    _RESULT_CACHE.clear()
    requests = []

    async def fake_client(payload):
        requests.append(payload)
        return OpenWAChatDraftResponse(
            draft_id="draft-1",
            chat_id="chat-1",
            dashboard_url="https://dashboard.example/chats/drafts/draft-1",
        )

    followup_workflow = FakeWorkflow([])
    try:
        draft_payload = asyncio.run(
            watchfacts_create_chat_draft_payload(
                "5712g",
                result_id,
                settings=settings,
                workflow=followup_workflow,
                openwa_client=fake_client,
            )
        )
    finally:
        _RESULT_CACHE.clear()
        _RESULT_CACHE.update(original_cache)

    assert followup_workflow.queries == []
    assert draft_payload["status"] == "created"
    assert draft_payload["result_id"] == result_id
    assert draft_payload["rank"] == 1
    assert len(requests) == 1
    assert requests[0]["sourceResultId"] == result_id


def test_watchfacts_create_chat_draft_prefers_result_id_when_rank_is_also_supplied(
    tmp_path,
) -> None:
    settings = load_search_settings(
        env={
            "ENABLE_OPENWA_CHAT_HANDOFF": "true",
            "OPENWA_BASE_URL": "https://openwa.example",
            "OPENWA_API_KEY": "secret",
            "OPENWA_DASHBOARD_URL": "https://dashboard.example",
        },
        project_root=tmp_path,
    )
    search_workflow = FakeWorkflow(
        [
            SearchResult(
                listing_text="5712G Used 2015 - 76k usdt",
                seller="Issac",
                source_url="/listing/1",
            )
        ]
    )
    original_cache = dict(_RESULT_CACHE)
    _RESULT_CACHE.clear()
    requests = []

    async def fake_client(payload):
        requests.append(payload)
        return OpenWAChatDraftResponse(
            draft_id="draft-1",
            chat_id="chat-1",
            dashboard_url="https://dashboard.example/chats/drafts/draft-1",
        )

    try:
        search_payload = asyncio.run(
            watchfacts_search_payload(
                "5712g",
                workflow=search_workflow,
                settings=settings,
            )
        )
        result_id = search_payload["results"][0]["result_id"]
        followup_workflow = FakeWorkflow([])

        draft_payload = asyncio.run(
            watchfacts_create_chat_draft_payload(
                "5712g",
                result_id,
                rank=0,
                settings=settings,
                workflow=followup_workflow,
                openwa_client=fake_client,
            )
        )
    finally:
        _RESULT_CACHE.clear()
        _RESULT_CACHE.update(original_cache)

    assert followup_workflow.queries == []
    assert draft_payload["status"] == "created"
    assert draft_payload["result_id"] == result_id
    assert requests


def test_watchfacts_create_chat_draft_uses_db_reference_by_stable_listing_id(
    tmp_path,
) -> None:
    settings = load_search_settings(
        env={
            "ENABLE_OPENWA_CHAT_HANDOFF": "true",
            "OPENWA_BASE_URL": "https://openwa.example",
            "OPENWA_API_KEY": "secret",
            "OPENWA_DASHBOARD_URL": "https://dashboard.example",
        },
        project_root=tmp_path,
    )
    search_workflow = FakeWorkflow(
        [
            SearchResult(
                listing_text="5712G Used 2015 - 76k usdt",
                seller="Issac",
                seller_phone="+86 178 2624 1887",
                source_url="/listing/1",
                image_url="/image/1.jpg",
                raw_listing_text="raw listing",
            )
        ]
    )
    search_payload = asyncio.run(
        watchfacts_search_payload(
            "5712g",
            workflow=search_workflow,
            settings=settings,
        )
    )
    stable_id = search_payload["results"][0]["stable_listing_id"]

    original_cache = dict(_RESULT_CACHE)
    _RESULT_CACHE.clear()
    requests = []

    async def fake_client(payload):
        requests.append(payload)
        return OpenWAChatDraftResponse(
            draft_id="draft-1",
            chat_id=None,
            dashboard_url="https://dashboard.example/chats/drafts/draft-1",
        )

    followup_workflow = FakeWorkflow([])
    try:
        draft_payload = asyncio.run(
            watchfacts_create_chat_draft_payload(
                "5712g",
                stable_id,
                settings=settings,
                workflow=followup_workflow,
                openwa_client=fake_client,
            )
        )
    finally:
        _RESULT_CACHE.clear()
        _RESULT_CACHE.update(original_cache)

    assert followup_workflow.queries == []
    assert draft_payload["status"] == "created"
    assert draft_payload["rank"] == 1
    assert requests
    assert requests[0]["sourceResultId"] == draft_payload["result_id"]


def test_watchfacts_search_payload_can_include_raw_and_similar_results() -> None:
    workflow = FakeWorkflow(
        [
            SearchResult(
                listing_text="5712G Used",
                raw_listing_text="raw listing text",
                similar_results=(SearchResult("5712G similar"),),
            )
        ]
    )

    payload = asyncio.run(
        watchfacts_search_payload(
            "5712g",
            workflow=workflow,
            include_similar=True,
            include_raw=True,
        )
    )

    assert payload["results"] == [
        {
            "listing_text": "5712G Used",
            "seller": None,
            "posted_date": None,
            "image_url": None,
            "source_url": None,
            "seller_phone": None,
            "similar_results": [
                {
                    "listing_text": "5712G similar",
                    "seller": None,
                    "posted_date": None,
                    "image_url": None,
                    "source_url": None,
                    "seller_phone": None,
                    "similar_results": [],
                    "raw_listing_text": None,
                }
            ],
            "raw_listing_text": "raw listing text",
            "rank": 1,
            "result_id": payload["results"][0]["result_id"],
            "stable_listing_id": payload["results"][0]["stable_listing_id"],
            "source_result_id": payload["results"][0]["result_id"],
        }
    ]


def test_watchfacts_search_payload_supports_offset_pagination() -> None:
    workflow = FakeWorkflow(
        [
            SearchResult("5712G first"),
            SearchResult("5712G second"),
            SearchResult("5712G third"),
        ]
    )

    payload = asyncio.run(
        watchfacts_search_payload(
            "5712g",
            workflow=workflow,
            limit=1,
            offset=1,
        )
    )

    assert payload["offset"] == 1
    assert payload["limit"] == 1
    assert payload["result_count"] == 1
    assert payload["has_more"] is True
    assert payload["next_offset"] == 2
    assert payload["results"][0]["rank"] == 2
    assert payload["results"][0]["listing_text"] == "5712G second"


def test_watchfacts_search_payload_adds_result_page_when_enabled(tmp_path) -> None:
    settings = load_search_settings(
        env={
            "RESULT_PAGE_PUBLIC_BASE_URL": "https://mcp.example/results",
            "RESULT_PAGE_STORAGE_DIR": str(tmp_path / "pages"),
            "RESULT_PAGE_TTL_SECONDS": "60",
            "RESULT_PAGE_MAX_RESULTS": "2",
        },
        project_root=tmp_path,
    )
    workflow = FakeWorkflow(
        [
            SearchResult(
                listing_text="5712G Used",
                raw_listing_text="raw context should not render",
            ),
            SearchResult("5712G second"),
            SearchResult("5712G third bounded out"),
        ]
    )

    payload = asyncio.run(
        watchfacts_search_payload(
            "5712g",
            workflow=workflow,
            settings=settings,
            limit=1,
        )
    )

    assert payload["result_count"] == 1
    assert payload["result_page"] == {
        "url": payload["result_page"]["url"],
        "expires_at": payload["result_page"]["expires_at"],
        "result_count": 2,
        "schema_version": 1,
    }
    assert payload["result_page"]["url"].startswith("https://mcp.example/results/")
    html_files = list(settings.result_page_storage_dir.glob("*.html"))
    assert len(html_files) == 1
    html = html_files[0].read_text(encoding="utf-8")
    assert "5712G Used" in html
    assert "5712G second" in html
    assert "5712G third bounded out" not in html
    assert "raw context should not render" not in html


def test_watchfacts_search_payload_omits_result_page_when_disabled(tmp_path) -> None:
    settings = load_search_settings(env={}, project_root=tmp_path)

    payload = asyncio.run(
        watchfacts_search_payload(
            "5712g",
            workflow=FakeWorkflow([SearchResult("5712G")]),
            settings=settings,
        )
    )

    assert "result_page" not in payload


def test_watchfacts_search_payload_rejects_empty_query() -> None:
    with pytest.raises(ValueError, match="query must not be empty"):
        asyncio.run(watchfacts_search_payload(" ", workflow=FakeWorkflow([])))


def test_watchfacts_search_payload_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError, match="limit must be a positive integer"):
        asyncio.run(
            watchfacts_search_payload("5712g", workflow=FakeWorkflow([]), limit=0)
        )


def test_watchfacts_search_payload_rejects_negative_offset() -> None:
    with pytest.raises(ValueError, match="offset must not be negative"):
        asyncio.run(
            watchfacts_search_payload("5712g", workflow=FakeWorkflow([]), offset=-1)
        )


def test_watchfacts_report_issue_records_feedback_from_result_id(tmp_path) -> None:
    settings = load_search_settings(env={}, project_root=tmp_path)
    workflow = FakeWorkflow(
        [
            SearchResult(
                listing_text="5712G Used 2015 - 76k usdt",
                seller="Issac",
                posted_date="April 20, 2026",
                source_url="/listing/1",
                raw_listing_text="raw listing",
            )
        ]
    )
    search_payload = asyncio.run(
        watchfacts_search_payload("5712g", workflow=workflow, settings=settings)
    )
    result_id = search_payload["results"][0]["result_id"]

    issue_payload = asyncio.run(
        watchfacts_report_issue_payload(
            "5712g",
            result_id,
            "wrong_result",
            notes="Wrong metal.",
            settings=settings,
            workflow=workflow,
        )
    )

    assert issue_payload["status"] == "recorded"
    assert issue_payload["issue"]["issue_ref"] == "F1"
    assert issue_payload["issue"]["reason"] == "wrong_result"
    assert issue_payload["issue"]["seller"] == "Issac"
    assert Database(settings.db_path).get_issue(1, issue_type="feedback") is not None


def test_watchfacts_create_chat_draft_uses_cached_search_result(tmp_path) -> None:
    settings = load_search_settings(
        env={
            "ENABLE_OPENWA_CHAT_HANDOFF": "true",
            "OPENWA_BASE_URL": "https://openwa.example",
            "OPENWA_API_KEY": "secret",
            "OPENWA_DASHBOARD_URL": "https://dashboard.example",
        },
        project_root=tmp_path,
    )
    workflow = FakeWorkflow(
        [
            SearchResult(
                listing_text="5712G Used 2015 - 76k usdt",
                seller="Issac",
                seller_phone="+86 178 2624 1887",
                source_url="/listing/1",
                image_url="/image/1.jpg",
                raw_listing_text="raw listing",
            )
        ]
    )
    search_payload = asyncio.run(
        watchfacts_search_payload("5712g", workflow=workflow, settings=settings)
    )
    result_id = search_payload["results"][0]["result_id"]
    requests = []

    async def fake_client(payload):
        requests.append(payload)
        return OpenWAChatDraftResponse(
            draft_id="draft-1",
            chat_id="chat-1",
            dashboard_url="https://dashboard.example/chats/drafts/draft-1",
        )

    draft_payload = asyncio.run(
        watchfacts_create_chat_draft_payload(
            "5712g",
            result_id,
            settings=settings,
            workflow=workflow,
            openwa_client=fake_client,
        )
    )

    assert draft_payload["status"] == "created"
    assert draft_payload["rank"] == 1
    assert draft_payload["result_id"] == result_id
    assert draft_payload["draft_id"] == "draft-1"
    assert draft_payload["dashboard_url"] == "https://dashboard.example/chats/drafts/draft-1"
    assert "payload" not in draft_payload
    assert requests[0]["sourceResultId"] == result_id
    assert requests[0]["seller"]["phone"] == "8617826241887"
    assert requests[0]["sourceUrl"] == "https://watchfacts.com/listing/1"
    assert requests[0]["product"]["imageUrl"] == "https://watchfacts.com/image/1.jpg"

    rank_payload = asyncio.run(
        watchfacts_create_chat_draft_payload(
            "5712g",
            rank=1,
            settings=settings,
            workflow=workflow,
            openwa_client=fake_client,
        )
    )

    assert rank_payload["status"] == "created"
    assert rank_payload["rank"] == 1
    assert rank_payload["result_id"] == result_id


def test_watchfacts_create_chat_draft_rank_uses_latest_cached_result(tmp_path) -> None:
    settings = load_search_settings(
        env={
            "ENABLE_OPENWA_CHAT_HANDOFF": "true",
            "OPENWA_BASE_URL": "https://openwa.example",
            "OPENWA_API_KEY": "secret",
            "OPENWA_DASHBOARD_URL": "https://dashboard.example",
        },
        project_root=tmp_path,
    )
    old_workflow = FakeWorkflow(
        [
            SearchResult(
                listing_text="5712G old listing",
                source_url="/listing/old",
            )
        ]
    )
    new_workflow = FakeWorkflow(
        [
            SearchResult(
                listing_text="5712G new listing",
                source_url="/listing/new",
            )
        ]
    )

    old_payload = asyncio.run(
        watchfacts_search_payload("5712g", workflow=old_workflow, settings=settings)
    )
    new_payload = asyncio.run(
        watchfacts_search_payload("5712g", workflow=new_workflow, settings=settings)
    )
    old_result_id = old_payload["results"][0]["result_id"]
    new_result_id = new_payload["results"][0]["result_id"]
    requests = []

    async def fake_client(payload):
        requests.append(payload)
        return OpenWAChatDraftResponse(
            draft_id="draft-1",
            chat_id=None,
            dashboard_url="https://dashboard.example/chats/drafts/draft-1",
        )

    rank_payload = asyncio.run(
        watchfacts_create_chat_draft_payload(
            "5712g",
            rank=1,
            settings=settings,
            workflow=new_workflow,
            openwa_client=fake_client,
        )
    )

    assert old_result_id != new_result_id
    assert rank_payload["result_id"] == new_result_id
    assert requests[0]["sourceResultId"] == new_result_id
    assert requests[0]["listingText"] == "5712G new listing"


def test_watchfacts_create_chat_draft_requires_result_reference(tmp_path) -> None:
    settings = load_search_settings(
        env={
            "ENABLE_OPENWA_CHAT_HANDOFF": "true",
            "OPENWA_BASE_URL": "https://openwa.example",
            "OPENWA_API_KEY": "secret",
        },
        project_root=tmp_path,
    )

    with pytest.raises(ValueError, match="result_id or rank is required"):
        asyncio.run(
            watchfacts_create_chat_draft_payload(
                "5712g",
                settings=settings,
                workflow=FakeWorkflow([]),
            )
        )


def test_watchfacts_issue_queue_payloads_round_trip(tmp_path) -> None:
    settings = load_search_settings(env={}, project_root=tmp_path)
    database = Database(settings.db_path)
    feedback_id = database.record_result_feedback(
        query_text="5712g",
        result_rank=1,
        reason="missing_info",
        listing_text="5712G Used",
    )
    database.record_suspicious_result(
        query_text="5712g",
        result_rank=2,
        reason="missing_price_evidence",
        severity=3,
        listing_text="5712G price missing",
    )

    listed = watchfacts_list_issues_payload(settings=settings, database=database)
    detail = watchfacts_get_issue_payload(f"F{feedback_id}", database=database)
    updated = watchfacts_update_issue_payload(
        f"F{feedback_id}",
        "fixed",
        notes="Covered by fixture.",
        database=database,
    )
    summary = watchfacts_suspicious_summary_payload(database=database)

    assert listed["result_count"] == 2
    assert detail["issue"]["issue_ref"] == f"F{feedback_id}"
    assert updated["updated"] is True
    assert updated["issue"]["status"] == "fixed"
    assert summary["summary"][0]["latest_issue_ref"] == "S1"
    assert summary["summary"][0]["severity"] == 3


def test_watchfacts_list_issues_payload_filters_by_status(tmp_path) -> None:
    settings = load_search_settings(env={}, project_root=tmp_path)
    database = Database(settings.db_path)
    fixed_id = database.record_result_feedback(
        query_text="5712r",
        result_rank=1,
        reason="missing_info",
        listing_text="5712R 2016/ HKD",
    )
    open_id = database.record_result_feedback(
        query_text="5205r",
        result_rank=2,
        reason="wrong_result",
        listing_text="5205R green HKD 425000",
    )
    database.mark_issue_status(
        fixed_id,
        issue_type="feedback",
        status="fixed",
        notes="Verified after deploy.",
    )

    open_payload = watchfacts_list_issues_payload(
        status="open",
        settings=settings,
        database=database,
    )
    fixed_payload = watchfacts_list_issues_payload(
        status="fixed",
        settings=settings,
        database=database,
    )
    all_payload = watchfacts_list_issues_payload(
        status="all",
        settings=settings,
        database=database,
    )

    assert [issue["id"] for issue in open_payload["issues"]] == [open_id]
    assert [issue["id"] for issue in fixed_payload["issues"]] == [fixed_id]
    assert all_payload["status"] == "all"
    assert all_payload["result_count"] == 2


def test_watchfacts_get_issue_payload_returns_bounded_safe_raw_context(tmp_path) -> None:
    settings = load_search_settings(env={}, project_root=tmp_path)
    database = Database(settings.db_path)
    listing_text = "5712R 2016/ HKD"
    raw_listing_text = (
        "before "
        + " ".join(f"left{i}" for i in range(120))
        + f" {listing_text} 830000 "
        + "cookie=secret-token .env data/watchfacts_state.json "
        + " ".join(f"right{i}" for i in range(120))
    )
    issue_id = database.record_result_feedback(
        query_text="5712r",
        result_rank=26,
        reason="missing_info",
        listing_text=listing_text,
        raw_listing_text=raw_listing_text,
    )

    payload = watchfacts_get_issue_payload(f"F{issue_id}", database=database)
    issue = payload["issue"]

    assert payload["found"] is True
    assert "raw_listing_text" not in issue
    assert issue["raw_context"]["matched_listing_found"] is True
    assert issue["raw_context"]["truncated_before"] is True
    assert issue["raw_context"]["truncated_after"] is True
    assert listing_text in issue["raw_context"]["text"]
    assert len(issue["raw_context"]["text"]) <= issue["raw_context"]["max_chars"]
    assert "secret-token" not in issue["raw_context"]["text"]
    assert ".env" not in issue["raw_context"]["text"]
    assert "watchfacts_state.json" not in issue["raw_context"]["text"]


def test_watchfacts_get_issue_payload_can_omit_raw_context(tmp_path) -> None:
    settings = load_search_settings(env={}, project_root=tmp_path)
    database = Database(settings.db_path)
    issue_id = database.record_result_feedback(
        query_text="5712r",
        result_rank=1,
        reason="missing_info",
        listing_text="5712R 2016/ HKD",
        raw_listing_text="5712R 2016/ HKD 830000",
    )

    payload = watchfacts_get_issue_payload(
        f"F{issue_id}",
        include_raw_context=False,
        database=database,
    )

    assert "raw_context" not in payload["issue"]
    assert "raw_listing_text" not in payload["issue"]


def test_watchfacts_update_issue_payload_returns_review_notes_and_rejects_bad_status(
    tmp_path,
) -> None:
    settings = load_search_settings(env={}, project_root=tmp_path)
    database = Database(settings.db_path)
    issue_id = database.record_result_feedback(
        query_text="5712r",
        result_rank=1,
        reason="missing_info",
        listing_text="5712R 2016/ HKD",
    )

    updated = watchfacts_update_issue_payload(
        f"F{issue_id}",
        "ignored",
        notes="Raw source lacks price; no code fix.",
        database=database,
    )

    assert updated["updated"] is True
    assert updated["issue"]["status"] == "ignored"
    assert updated["issue"]["review_notes"] == "Raw source lacks price; no code fix."
    with pytest.raises(ValueError, match="status must be one of"):
        watchfacts_update_issue_payload(
            f"F{issue_id}",
            "closed",
            database=database,
        )


def test_watchfacts_health_payload_reports_dependencies(tmp_path) -> None:
    settings = load_search_settings(env={}, project_root=tmp_path)

    async def fake_checker(active_settings):
        assert active_settings == settings
        return BrowserSessionStatus(
            ok=True,
            status="valid",
            detail="Saved browser session is valid.",
        )

    payload = asyncio.run(
        watchfacts_health_payload(settings=settings, session_checker=fake_checker)
    )

    assert payload["watchfacts_session"]["ok"] is True
    assert payload["database"]["ok"] is True
    assert payload["openwa"]["ready"] is False
    assert payload["search_runtime"]["ready"] is True


def test_watchfacts_health_payload_includes_http_client_status_without_secrets(
    tmp_path,
) -> None:
    settings = load_search_settings(env={}, project_root=tmp_path)

    async def fake_checker(active_settings):
        assert active_settings == settings
        return BrowserSessionStatus(
            ok=True,
            status="valid",
            detail="Saved browser session is valid.",
        )

    def fake_http_status(active_settings):
        assert active_settings == settings
        return WatchFactsHttpClientStatus(
            enabled=True,
            form_cache_fresh=True,
            last_error_type="timeout",
            last_success_at=1000.0,
            last_failure_at=1005.0,
            last_fallback_at=None,
            last_elapsed_ms=321,
            last_form_refresh_elapsed_ms=123,
            last_post_elapsed_ms=198,
            last_http_version="HTTP/1.1",
            last_status_code=200,
            last_response_bytes=12345,
            last_server_query_changed=True,
            last_server_query_token_count=1,
            consecutive_failures=1,
            cooldown_until=1065.0,
        )

    payload = asyncio.run(
        watchfacts_health_payload(
            settings=settings,
            session_checker=fake_checker,
            http_client_status_provider=fake_http_status,
        )
    )

    assert payload["watchfacts_http_client"] == {
        "enabled": True,
        "form_cache_fresh": True,
        "last_error_type": "timeout",
        "last_success_at": 1000.0,
        "last_failure_at": 1005.0,
        "last_fallback_at": None,
        "last_elapsed_ms": 321,
        "last_form_refresh_elapsed_ms": 123,
        "last_post_elapsed_ms": 198,
        "last_http_version": "HTTP/1.1",
        "last_status_code": 200,
        "last_response_bytes": 12345,
        "last_server_query_changed": True,
        "last_server_query_token_count": 1,
        "consecutive_failures": 1,
        "cooldown_until": 1065.0,
    }
    serialized = json.dumps(payload).casefold()
    assert "cookie" not in serialized
    assert "csrf" not in serialized


def test_watchfacts_health_payload_includes_quality_metrics(tmp_path) -> None:
    settings = load_search_settings(env={}, project_root=tmp_path)
    Database(settings.db_path).record_query_results(
        "15510or",
        [
            SearchResult("15510OR black", source_url="/listing/1"),
            SearchResult("15510OR blue", image_url="https://img.example/blue.jpg"),
        ],
        image_missing_count=1,
        server_filtered_hit_count=1,
        playwright_fallback_count=1,
    )

    async def fake_checker(active_settings):
        return BrowserSessionStatus(
            ok=True,
            status="valid",
            detail="Saved browser session is valid.",
        )

    payload = asyncio.run(
        watchfacts_health_payload(settings=settings, session_checker=fake_checker)
    )

    quality_metrics = payload["search_runtime"]["quality_metrics"]
    assert quality_metrics == {
        "image_missing_count": 1,
        "server_filtered_hit_count": 1,
        "playwright_fallback_count": 1,
        "query_count": 1,
        "total_results": 2,
        "image_missing_rate": 0.5,
        "server_filtered_hit_rate": 1.0,
        "playwright_fallback_rate": 1.0,
    }


def test_watchfacts_health_payload_warms_http_client_when_enabled(
    tmp_path,
    monkeypatch,
) -> None:
    settings = load_search_settings(
        env={"WATCHFACTS_HTTP_WARMUP_ON_HEALTH": "true"},
        project_root=tmp_path,
    )
    warmed = []

    async def fake_checker(active_settings):
        assert active_settings == settings
        return BrowserSessionStatus(
            ok=True,
            status="valid",
            detail="Saved browser session is valid.",
        )

    async def fake_warmup(active_settings):
        warmed.append(active_settings)

    monkeypatch.setattr(
        "app.tool_runtime.warm_watchfacts_http_client",
        fake_warmup,
    )

    payload = asyncio.run(
        watchfacts_health_payload(settings=settings, session_checker=fake_checker)
    )

    assert warmed == [settings]
    assert payload["watchfacts_session"]["ok"] is True
