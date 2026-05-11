from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from app.telegram_bot import (
    EMPTY_QUERY_MESSAGE,
    PROCESSING_MIN_SECONDS_KEY,
    PROCESSING_MESSAGE,
    START_MESSAGE,
    WORKFLOW_KEY,
    SearchResult,
    format_posted_date,
    handle_text_message,
    start_command,
)


class FakeSentMessage:
    def __init__(self, replies: list[str], text: str) -> None:
        self.replies = replies
        self.text = text
        self.deleted = False

    async def delete(self) -> None:
        self.deleted = True
        if self.text in self.replies:
            self.replies.remove(self.text)


class FakeMessage:
    def __init__(self, text: str | None = None) -> None:
        self.text = text
        self.chat_id = 12345
        self.replies: list[str] = []
        self.photos: list[tuple[str, str]] = []
        self.sent_messages: list[FakeSentMessage] = []

    async def reply_text(self, text: str) -> FakeSentMessage:
        self.replies.append(text)
        sent_message = FakeSentMessage(self.replies, text)
        self.sent_messages.append(sent_message)
        return sent_message

    async def reply_photo(self, photo: str, caption: str) -> None:
        self.photos.append((photo, caption))


class FakeWorkflow:
    def __init__(self, results: list[SearchResult]) -> None:
        self.results = results
        self.queries: list[str] = []

    async def search(self, query: str) -> list[SearchResult]:
        self.queries.append(query)
        return self.results


class FailingWorkflow:
    async def search(self, query: str) -> list[SearchResult]:
        raise RuntimeError("boom")


class FakeBot:
    def __init__(self) -> None:
        self.chat_actions: list[tuple[int, str]] = []

    async def send_chat_action(self, *, chat_id: int, action: str) -> None:
        self.chat_actions.append((chat_id, action))


def make_context(workflow=None):
    bot_data = {PROCESSING_MIN_SECONDS_KEY: 0}
    if workflow is not None:
        bot_data[WORKFLOW_KEY] = workflow
    return SimpleNamespace(application=SimpleNamespace(bot=FakeBot(), bot_data=bot_data))


def test_start_command_returns_usage_message() -> None:
    message = FakeMessage()

    asyncio.run(start_command(SimpleNamespace(message=message), make_context()))

    assert message.replies == [START_MESSAGE]


def test_empty_messages_are_rejected() -> None:
    message = FakeMessage("   ")
    workflow = FakeWorkflow([SearchResult("unused")])
    context = make_context(workflow)

    asyncio.run(
        handle_text_message(SimpleNamespace(message=message), context)
    )

    assert message.replies == [EMPTY_QUERY_MESSAGE]
    assert workflow.queries == []
    assert context.application.bot.chat_actions == []


def test_text_messages_call_search_workflow() -> None:
    message = FakeMessage("  228253a choco  ")
    workflow = FakeWorkflow(
        [
            SearchResult(
                listing_text="228253A choco N2",
                seller="HK STOCKS",
                posted_date="April 20, 2026",
                image_url="https://image-url.jpg",
            )
        ]
    )
    context = make_context(workflow)

    asyncio.run(
        handle_text_message(SimpleNamespace(message=message), context)
    )

    assert workflow.queries == ["228253a choco"]
    assert message.replies == []
    assert message.sent_messages[0].text == PROCESSING_MESSAGE
    assert message.sent_messages[0].deleted is True
    assert context.application.bot.chat_actions == [(12345, "typing")]
    assert message.photos == [
        (
            "https://image-url.jpg",
            (
                "🏷️ Thông tin:\n"
                "228253A choco N2\n\n"
                "👤 Người đăng:\n"
                "HK STOCKS\n\n"
                "📅 Ngày đăng:\n"
                "20/04/2026"
            ),
        )
    ]


def test_text_messages_send_each_result_as_separate_photo() -> None:
    message = FakeMessage("7118/1200a blue")
    workflow = FakeWorkflow(
        [
            SearchResult(
                listing_text="7118/1200A blue 2/2026 $725000",
                seller="Ella",
                posted_date="April 17, 2026",
                image_url="https://image-1.jpg",
            ),
            SearchResult(
                listing_text="7118/1200A blue N2/2026y 725k hkd",
                seller="Forest",
                posted_date="April 23, 2026",
                image_url="https://image-2.jpg",
            ),
        ]
    )
    context = make_context(workflow)

    asyncio.run(
        handle_text_message(SimpleNamespace(message=message), context)
    )

    assert len(message.photos) == 2
    assert message.photos[0] == (
        "https://image-1.jpg",
        (
            "🏷️ Thông tin:\n"
            "7118/1200A blue 2/2026 $725000\n\n"
            "👤 Người đăng:\n"
            "Ella\n\n"
            "📅 Ngày đăng:\n"
            "17/04/2026"
        ),
    )
    assert message.photos[1][0] == "https://image-2.jpg"
    assert "Forest" in message.photos[1][1]
    assert "23/04/2026" in message.photos[1][1]
    assert message.replies == []
    assert context.application.bot.chat_actions == [(12345, "typing")]


def test_text_messages_fallback_to_text_when_image_is_missing() -> None:
    message = FakeMessage("228253a choco")
    workflow = FakeWorkflow(
        [
            SearchResult(
                listing_text="228253A choco N2",
                seller="HK STOCKS",
                posted_date="April 20, 2026",
            )
        ]
    )
    context = make_context(workflow)

    asyncio.run(
        handle_text_message(SimpleNamespace(message=message), context)
    )

    assert message.photos == []
    assert message.replies == [
        (
            "🏷️ Thông tin:\n"
            "228253A choco N2\n\n"
            "👤 Người đăng:\n"
            "HK STOCKS\n\n"
            "📅 Ngày đăng:\n"
            "20/04/2026"
        )
    ]
    assert message.sent_messages[0].text == PROCESSING_MESSAGE
    assert message.sent_messages[0].deleted is True
    assert context.application.bot.chat_actions == [(12345, "typing")]


def test_format_posted_date_handles_reposted_suffix() -> None:
    assert format_posted_date("April 22, 2026 · Reposted 2x") == "22/04/2026"


def test_search_errors_are_logged_without_query_text(caplog) -> None:
    message = FakeMessage("228253a choco")
    context = make_context(FailingWorkflow())

    with caplog.at_level(logging.ERROR, logger="app.telegram_bot"):
        asyncio.run(
            handle_text_message(
                SimpleNamespace(message=message),
                context,
            )
        )

    assert message.replies == ["Search failed. Please check the bot logs."]
    assert message.sent_messages[0].text == PROCESSING_MESSAGE
    assert message.sent_messages[0].deleted is True
    assert context.application.bot.chat_actions == [(12345, "typing")]
    assert (
        "event=telegram.search_error error_type=RuntimeError query_length=13"
        in caplog.text
    )
    assert "228253a choco" not in caplog.text
    assert "token" not in caplog.text
