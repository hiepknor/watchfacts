from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("mcp.server.fastmcp")

from app import mcp_server


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

    def fake_list(issue_type="all", limit=10, min_severity=None):
        return {"issue_type": issue_type, "limit": limit, "min_severity": min_severity}

    def fake_get(issue_ref, issue_type=None):
        return {"issue_ref": issue_ref, "issue_type": issue_type}

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
    listed = mcp_server.list_issues("suspicious", 7, 3)
    detail = mcp_server.get_issue("S1")
    updated = mcp_server.update_issue("S1", "ignored", "false positive")
    summary = mcp_server.suspicious_summary(5)

    assert report == {"status": "recorded"}
    assert report_calls == [("5712g", "watchfacts:abc", "wrong_result", None, "bad year")]
    assert listed == {"issue_type": "suspicious", "limit": 7, "min_severity": 3}
    assert detail == {"issue_ref": "S1", "issue_type": None}
    assert updated == {"updated": True}
    assert update_calls == [("S1", "ignored", "false positive", None)]
    assert summary == {"limit": 5}
