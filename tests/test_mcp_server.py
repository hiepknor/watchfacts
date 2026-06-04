from __future__ import annotations

import asyncio

from app import mcp_server


def test_watchfacts_search_tool_calls_payload(monkeypatch) -> None:
    calls: list[tuple[str, int, bool]] = []

    async def fake_payload(
        query: str,
        *,
        limit: int | None = None,
        include_similar: bool = True,
        include_raw: bool = False,
        settings=None,
        workflow=None,
    ) -> dict[str, object]:
        calls.append((query, limit if limit is not None else 0, include_similar))
        return {"query": query, "total_count": 0}

    monkeypatch.setattr(mcp_server, "watchfacts_search_payload", fake_payload)

    result = asyncio.run(mcp_server.watchfacts_search("5712G 2015 full set"))

    assert calls == [("5712G 2015 full set", 5, True)]
    assert result == {"query": "5712G 2015 full set", "total_count": 0}
