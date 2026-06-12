from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("mcp.server.fastmcp")

from starlette.testclient import TestClient

from app.config import load_search_settings
from app import mcp_server
from app.integrations.openwa_handoff import OpenWAChatDraftResponse
from app.results.result_pages import generate_result_page, read_result_page_action_payload
from app.searching.search_result import SearchResult


def test_search_tool_calls_payload(monkeypatch) -> None:
    calls: list[tuple[str, int, int, bool]] = []

    async def fake_payload(
        query: str,
        *,
        limit: int | None = None,
        offset: int = 0,
        include_similar: bool = True,
        include_raw: bool = False,
        settings=None,
        workflow=None,
    ) -> dict[str, object]:
        calls.append((query, limit if limit is not None else 0, offset, include_similar))
        return {"query": query, "total_count": 0}

    monkeypatch.setattr(mcp_server, "watchfacts_search_payload", fake_payload)

    result = asyncio.run(mcp_server.search("5712G 2015 full set", offset=5))

    assert calls == [("5712G 2015 full set", 5, 5, True)]
    assert result == {"query": "5712G 2015 full set", "total_count": 0}


def test_create_chat_draft_tool_calls_payload(monkeypatch) -> None:
    calls: list[tuple[str, str | None, int | None]] = []

    async def fake_payload(
        query: str,
        result_id: str | None = None,
        *,
        rank: int | None = None,
    ) -> dict[str, object]:
        calls.append((query, result_id, rank))
        return {"status": "created"}

    monkeypatch.setattr(
        mcp_server,
        "watchfacts_create_chat_draft_payload",
        fake_payload,
    )

    result = asyncio.run(mcp_server.create_chat_draft("5712g", "watchfacts:abc"))

    assert calls == [("5712g", "watchfacts:abc", None)]
    assert result == {"status": "created"}

    result = asyncio.run(mcp_server.create_chat_draft("5712g", rank=20))

    assert calls[-1] == ("5712g", None, 20)
    assert result == {"status": "created"}


def test_issue_tools_call_payloads(monkeypatch) -> None:
    report_calls = []
    update_calls = []

    async def fake_report(query, result_id, reason, rank=None, notes=None):
        report_calls.append((query, result_id, reason, rank, notes))
        return {"status": "recorded"}

    def fake_list(issue_type="all", limit=20, min_severity=None, status="open"):
        return {
            "issue_type": issue_type,
            "limit": limit,
            "min_severity": min_severity,
            "status": status,
        }

    def fake_get(issue_ref, issue_type=None, include_raw_context=True):
        return {
            "issue_ref": issue_ref,
            "issue_type": issue_type,
            "include_raw_context": include_raw_context,
        }

    def fake_update(issue_ref, status, notes=None, issue_type=None):
        update_calls.append((issue_ref, status, notes, issue_type))
        return {"updated": True}

    def fake_summary(limit=20):
        return {"limit": limit}

    monkeypatch.setattr(mcp_server, "watchfacts_report_issue_payload", fake_report)
    monkeypatch.setattr(mcp_server, "watchfacts_list_issues_payload", fake_list)
    monkeypatch.setattr(mcp_server, "watchfacts_get_issue_payload", fake_get)
    monkeypatch.setattr(mcp_server, "watchfacts_update_issue_payload", fake_update)
    monkeypatch.setattr(
        mcp_server,
        "watchfacts_suspicious_summary_payload",
        fake_summary,
    )

    report = asyncio.run(
        mcp_server.report_issue(
            "5712g",
            "wrong_result",
            result_id="watchfacts:abc",
            notes="bad year",
        )
    )
    listed = mcp_server.list_issues("suspicious", limit=7, min_severity=3, status="fixed")
    detail = mcp_server.get_issue("S1", include_raw_context=False)
    updated = mcp_server.update_issue("S1", "ignored", "false positive")
    summary = mcp_server.suspicious_summary(5)

    assert report == {"status": "recorded"}
    assert report_calls == [("5712g", "watchfacts:abc", "wrong_result", None, "bad year")]
    assert listed == {
        "issue_type": "suspicious",
        "limit": 7,
        "min_severity": 3,
        "status": "fixed",
    }
    assert detail == {
        "issue_ref": "S1",
        "issue_type": None,
        "include_raw_context": False,
    }
    assert updated == {"updated": True}
    assert update_calls == [("S1", "ignored", "false positive", None)]
    assert summary == {"limit": 5}


def test_result_page_route_serves_generated_html(monkeypatch, tmp_path) -> None:
    settings = load_search_settings(
        env={
            "RESULT_PAGE_PUBLIC_BASE_URL": "https://mcp.example/results",
            "RESULT_PAGE_STORAGE_DIR": str(tmp_path / "pages"),
            "RESULT_PAGE_TTL_SECONDS": "60",
        },
        project_root=tmp_path,
    )
    page = generate_result_page(
        "5712g",
        [SearchResult("5712G")],
        settings=settings,
        now=datetime.now(timezone.utc),
    )
    assert page is not None
    token = page.url.rsplit("/", maxsplit=1)[1]
    monkeypatch.setattr(mcp_server, "load_search_settings", lambda: settings)

    response = TestClient(mcp_server.app.streamable_http_app()).get(f"/results/{token}")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "5712G" in response.text


def test_result_page_route_reports_missing_and_expired(monkeypatch, tmp_path) -> None:
    settings = load_search_settings(
        env={
            "RESULT_PAGE_PUBLIC_BASE_URL": "https://mcp.example/results",
            "RESULT_PAGE_STORAGE_DIR": str(tmp_path / "pages"),
            "RESULT_PAGE_TTL_SECONDS": "1",
        },
        project_root=tmp_path,
    )
    page = generate_result_page(
        "5712g",
        [SearchResult("5712G")],
        settings=settings,
        now=datetime.now(timezone.utc) - timedelta(seconds=5),
    )
    assert page is not None
    token = page.url.rsplit("/", maxsplit=1)[1]
    monkeypatch.setattr(mcp_server, "load_search_settings", lambda: settings)
    client = TestClient(mcp_server.app.streamable_http_app())

    assert client.get("/results/missing-token").status_code == 404
    assert client.get(f"/results/{token}").status_code == 410


def test_result_page_route_is_disabled_without_public_base_url(
    monkeypatch,
    tmp_path,
) -> None:
    enabled_settings = load_search_settings(
        env={
            "RESULT_PAGE_PUBLIC_BASE_URL": "https://mcp.example/results",
            "RESULT_PAGE_STORAGE_DIR": str(tmp_path / "pages"),
            "RESULT_PAGE_TTL_SECONDS": "60",
        },
        project_root=tmp_path,
    )
    page = generate_result_page(
        "5712g",
        [SearchResult("5712G")],
        settings=enabled_settings,
        now=datetime.now(timezone.utc),
    )
    assert page is not None
    token = page.url.rsplit("/", maxsplit=1)[1]
    disabled_settings = load_search_settings(
        env={
            "RESULT_PAGE_STORAGE_DIR": str(tmp_path / "pages"),
            "RESULT_PAGE_TTL_SECONDS": "60",
        },
        project_root=tmp_path,
    )
    monkeypatch.setattr(mcp_server, "load_search_settings", lambda: disabled_settings)

    response = TestClient(mcp_server.app.streamable_http_app()).get(f"/results/{token}")

    assert response.status_code == 404


def test_result_page_openwa_action_uses_sidecar_payload(
    monkeypatch,
    tmp_path,
) -> None:
    settings = load_search_settings(
        env={
            "RESULT_PAGE_PUBLIC_BASE_URL": "https://mcp.example/results",
            "RESULT_PAGE_STORAGE_DIR": str(tmp_path / "pages"),
            "RESULT_PAGE_TTL_SECONDS": "60",
            "OPENWA_BASE_URL": "http://openwa-api:2785",
            "OPENWA_API_KEY": "test-key",
            "OPENWA_DASHBOARD_URL": "https://openwa.example",
            "ENABLE_OPENWA_CHAT_HANDOFF": "true",
        },
        project_root=tmp_path,
    )
    page = generate_result_page(
        "5712g",
        [
            SearchResult(
                "5712G blue 2015 full set",
                seller="Seller One",
                seller_phone="+15550001",
                image_url="/images/5712g.jpg",
                source_url="/listing/5712g",
            )
        ],
        settings=settings,
        now=datetime.now(timezone.utc),
    )
    assert page is not None
    token = page.url.rsplit("/", maxsplit=1)[1]
    action_page = read_result_page_action_payload(token, settings=settings)
    assert action_page.payload is not None
    result_id = action_page.payload["results"][0]["result_id"]
    calls: list[dict[str, object]] = []

    async def fake_create_openwa_chat_draft(config, payload):
        calls.append(payload)
        return OpenWAChatDraftResponse(
            draft_id="draft-1",
            chat_id=None,
            dashboard_url="https://openwa.example/chats/drafts/draft-1",
        )

    monkeypatch.setattr(mcp_server, "load_search_settings", lambda: settings)
    monkeypatch.setattr(
        mcp_server,
        "create_openwa_chat_draft",
        fake_create_openwa_chat_draft,
    )

    response = TestClient(mcp_server.app.streamable_http_app()).post(
        f"/results/{token}/actions/openwa-draft",
        json={"action_nonce": action_page.action_nonce, "result_id": result_id},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "status": "created",
        "result_id": result_id,
        "draft_id": "draft-1",
        "chat_id": None,
        "dashboard_url": "https://openwa.example/chats/drafts/draft-1",
    }
    assert calls[0]["sourceResultId"] == result_id
    assert calls[0]["sourceUrl"] == "https://watchfacts.com/listing/5712g"
    assert calls[0]["listingText"] == "5712G blue 2015 full set"
    assert calls[0]["rawListingText"] is None
    assert calls[0]["seller"] == {
        "name": "Seller One",
        "phone": "+15550001",
        "watchfactsId": None,
        "profileUrl": None,
    }
    assert calls[0]["product"]["imageUrl"] == "https://watchfacts.com/images/5712g.jpg"


def test_result_page_report_action_records_feedback_issue(
    monkeypatch,
    tmp_path,
) -> None:
    settings = load_search_settings(
        env={
            "RESULT_PAGE_PUBLIC_BASE_URL": "https://mcp.example/results",
            "RESULT_PAGE_STORAGE_DIR": str(tmp_path / "pages"),
            "RESULT_PAGE_TTL_SECONDS": "60",
            "DB_PATH": str(tmp_path / "bot.db"),
        },
        project_root=tmp_path,
    )
    page = generate_result_page(
        "5712g",
        [
            SearchResult(
                "5712G blue 2015 full set",
                seller="Seller One",
                posted_date="June 1, 2026",
                source_url="/listing/5712g",
            )
        ],
        settings=settings,
        now=datetime.now(timezone.utc),
    )
    assert page is not None
    token = page.url.rsplit("/", maxsplit=1)[1]
    action_page = read_result_page_action_payload(token, settings=settings)
    assert action_page.payload is not None
    result_id = action_page.payload["results"][0]["result_id"]
    monkeypatch.setattr(mcp_server, "load_search_settings", lambda: settings)

    response = TestClient(mcp_server.app.streamable_http_app()).post(
        f"/results/{token}/actions/report",
        json={
            "action_nonce": action_page.action_nonce,
            "result_id": result_id,
            "reason": "wrong_result",
            "notes": "bad reference",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["status"] == "recorded"
    assert payload["result_id"] == result_id
    assert payload["issue_ref"] == "F1"
    assert payload["issue"] == {
        "id": 1,
        "issue_type": "feedback",
        "status": "open",
        "reason": "wrong_result",
    }


def test_result_page_action_rejects_invalid_nonce(monkeypatch, tmp_path) -> None:
    settings = load_search_settings(
        env={
            "RESULT_PAGE_PUBLIC_BASE_URL": "https://mcp.example/results",
            "RESULT_PAGE_STORAGE_DIR": str(tmp_path / "pages"),
            "RESULT_PAGE_TTL_SECONDS": "60",
        },
        project_root=tmp_path,
    )
    page = generate_result_page(
        "5712g",
        [SearchResult("5712G")],
        settings=settings,
        now=datetime.now(timezone.utc),
    )
    assert page is not None
    token = page.url.rsplit("/", maxsplit=1)[1]
    action_page = read_result_page_action_payload(token, settings=settings)
    assert action_page.payload is not None
    result_id = action_page.payload["results"][0]["result_id"]
    monkeypatch.setattr(mcp_server, "load_search_settings", lambda: settings)

    response = TestClient(mcp_server.app.streamable_http_app()).post(
        f"/results/{token}/actions/report",
        json={
            "action_nonce": "wrong",
            "result_id": result_id,
            "reason": "wrong_result",
        },
    )

    assert response.status_code == 403
    assert response.json() == {
        "ok": False,
        "error": "invalid_nonce",
        "message": "Invalid result page action nonce.",
    }


def test_result_page_action_is_rate_limited(monkeypatch, tmp_path) -> None:
    settings = load_search_settings(
        env={
            "RESULT_PAGE_PUBLIC_BASE_URL": "https://mcp.example/results",
            "RESULT_PAGE_STORAGE_DIR": str(tmp_path / "pages"),
            "RESULT_PAGE_TTL_SECONDS": "60",
            "RESULT_PAGE_RATE_LIMIT_MAX_REQUESTS": "1",
            "RESULT_PAGE_RATE_LIMIT_WINDOW_SECONDS": "60",
            "RESULT_PAGE_RATE_LIMIT_BLOCK_SECONDS": "30",
        },
        project_root=tmp_path,
    )
    page = generate_result_page(
        "5712g",
        [SearchResult("5712G")],
        settings=settings,
        now=datetime.now(timezone.utc),
    )
    assert page is not None
    token = page.url.rsplit("/", maxsplit=1)[1]
    action_page = read_result_page_action_payload(token, settings=settings)
    assert action_page.payload is not None
    result_id = action_page.payload["results"][0]["result_id"]
    monkeypatch.setattr(mcp_server, "load_search_settings", lambda: settings)
    client = TestClient(mcp_server.app.streamable_http_app())
    body = {
        "action_nonce": action_page.action_nonce,
        "result_id": result_id,
        "reason": "wrong_result",
    }

    assert client.post(f"/results/{token}/actions/report", json=body).status_code == 200
    limited = client.post(f"/results/{token}/actions/report", json=body)

    assert limited.status_code == 429
    assert limited.json()["error"] == "rate_limited"
