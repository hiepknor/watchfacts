from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.openwa_handoff import (
    OpenWAChatDraftResponse,
    OpenWAHandoffConfig,
    OpenWAHandoffConfigError,
    OpenWAHandoffResponseError,
    create_openwa_chat_draft,
)


class FakeHTTPResponse:
    def __init__(self, payload: dict, *, status: int = 200) -> None:
        self.status = status
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def make_config() -> OpenWAHandoffConfig:
    return OpenWAHandoffConfig(
        base_url="https://openwa.example",
        api_key="openwa-secret",
        dashboard_url="https://dashboard.example",
        chat_draft_endpoint="/api/chats/drafts",
        enabled=True,
    )


def test_create_openwa_chat_draft_posts_json_and_builds_dashboard_url(monkeypatch) -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(SimpleNamespace(request=request, timeout=timeout))
        return FakeHTTPResponse({"draftId": "draft-123", "chatId": None})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    response = asyncio.run(
        create_openwa_chat_draft(
            make_config(),
            {"source": "watchfacts", "listingText": "5712R"},
            timeout_seconds=3,
        )
    )

    assert isinstance(response, OpenWAChatDraftResponse)
    assert response.draft_id == "draft-123"
    assert response.chat_id is None
    assert response.dashboard_url == "https://dashboard.example/chats/drafts/draft-123"
    assert requests[0].timeout == 3
    request = requests[0].request
    assert request.full_url == "https://openwa.example/api/chats/drafts"
    assert request.headers["X-api-key"] == "openwa-secret"
    assert json.loads(request.data.decode("utf-8")) == {
        "source": "watchfacts",
        "listingText": "5712R",
    }


def test_create_openwa_chat_draft_uses_openwa_dashboard_url(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        return FakeHTTPResponse(
            {
                "draftId": "draft-123",
                "dashboardUrl": "https://openwa.example/chats/drafts/custom",
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    response = asyncio.run(
        create_openwa_chat_draft(
            make_config(),
            {"source": "watchfacts"},
        )
    )

    assert response.dashboard_url == "https://openwa.example/chats/drafts/custom"


def test_create_openwa_chat_draft_accepts_chat_draft_id(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        return FakeHTTPResponse({"chatDraftId": "draft-456"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    response = asyncio.run(
        create_openwa_chat_draft(
            make_config(),
            {"source": "watchfacts"},
        )
    )

    assert response.draft_id == "draft-456"
    assert response.dashboard_url == "https://dashboard.example/chats/drafts/draft-456"


def test_create_openwa_chat_draft_requires_ready_config() -> None:
    config = OpenWAHandoffConfig(
        base_url="",
        api_key="",
        dashboard_url="",
        chat_draft_endpoint="/api/chats/drafts",
        enabled=False,
    )

    with pytest.raises(OpenWAHandoffConfigError):
        asyncio.run(create_openwa_chat_draft(config, {"source": "watchfacts"}))


def test_create_openwa_chat_draft_rejects_missing_draft_id(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        return FakeHTTPResponse({"dashboardUrl": "https://dashboard.example/chats/drafts/1"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(OpenWAHandoffResponseError, match="missing draftId"):
        asyncio.run(create_openwa_chat_draft(make_config(), {"source": "watchfacts"}))
