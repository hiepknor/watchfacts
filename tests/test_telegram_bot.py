from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.telegram_bot import (
    EMPTY_QUERY_MESSAGE,
    START_MESSAGE,
    WORKFLOW_KEY,
    SearchResult,
    handle_text_message,
    start_command,
)


class FakeMessage:
    def __init__(self, text: str | None = None) -> None:
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeWorkflow:
    def __init__(self, results: list[SearchResult]) -> None:
        self.results = results
        self.queries: list[str] = []

    async def search(self, query: str) -> list[SearchResult]:
        self.queries.append(query)
        return self.results


def make_context(workflow: FakeWorkflow | None = None):
    bot_data = {}
    if workflow is not None:
        bot_data[WORKFLOW_KEY] = workflow
    return SimpleNamespace(application=SimpleNamespace(bot_data=bot_data))


def test_start_command_returns_usage_message() -> None:
    message = FakeMessage()

    asyncio.run(start_command(SimpleNamespace(message=message), make_context()))

    assert message.replies == [START_MESSAGE]


def test_empty_messages_are_rejected() -> None:
    message = FakeMessage("   ")
    workflow = FakeWorkflow([SearchResult("unused")])

    asyncio.run(
        handle_text_message(SimpleNamespace(message=message), make_context(workflow))
    )

    assert message.replies == [EMPTY_QUERY_MESSAGE]
    assert workflow.queries == []


def test_text_messages_call_search_workflow() -> None:
    message = FakeMessage("  228253a choco  ")
    workflow = FakeWorkflow(
        [
            SearchResult(
                listing_text="228253A choco N2",
                seller="HK STOCKS",
                posted_date="April 20, 2026",
            )
        ]
    )

    asyncio.run(
        handle_text_message(SimpleNamespace(message=message), make_context(workflow))
    )

    assert workflow.queries == ["228253a choco"]
    assert message.replies == ["228253A choco N2\nSeller: HK STOCKS\nPosted: April 20, 2026"]
