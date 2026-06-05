from __future__ import annotations

import asyncio
import json

import pytest

from app.config import load_search_settings
from app.db import Database
from app.openwa_handoff import OpenWAChatDraftResponse
from app.scraper import BrowserSessionStatus
from app.search_result import SearchResult
from app.tool_runtime import (
    watchfacts_create_chat_draft_payload,
    watchfacts_get_issue_payload,
    watchfacts_health_payload,
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


def test_watchfacts_search_payload_serializes_results_for_tool_runtime() -> None:
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

    payload = asyncio.run(
        watchfacts_search_payload(
            " 5712g ",
            workflow=workflow,
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
    assert payload["result_cache_ttl_seconds"] == 1800
    result = payload["results"][0]
    assert result["rank"] == 1
    assert result["result_id"].startswith("watchfacts:")
    assert result["source_result_id"] == result["result_id"]
    assert result["listing_text"] == "5712G Used 2015 - 76k usdt"
    assert result["seller"] == "Issac"
    assert result["seller_phone"] == "17826241887"
    assert result["similar_results"] == []


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
            last_fallback_at=1005.0,
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
        "last_fallback_at": 1005.0,
    }
    serialized = json.dumps(payload).casefold()
    assert "cookie" not in serialized
    assert "csrf" not in serialized
