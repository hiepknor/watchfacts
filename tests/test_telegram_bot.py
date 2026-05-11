from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from app.telegram_bot import (
    EMPTY_QUERY_MESSAGE,
    DEFAULT_RESULT_LIMIT,
    PROCESSING_MIN_SECONDS_KEY,
    PROCESSING_MESSAGE,
    RESULT_LIMIT_KEY,
    START_MESSAGE,
    WORKFLOW_KEY,
    SearchResult,
    format_result_limit_notice,
    format_posted_date,
    handle_more_results,
    handle_text_message,
    start_command,
)


class FakeSentMessage:
    def __init__(self, replies: list[str], text: str, reply_markup=None) -> None:
        self.replies = replies
        self.text = text
        self.reply_markup = reply_markup
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

    async def reply_text(self, text: str, **kwargs) -> FakeSentMessage:
        self.replies.append(text)
        sent_message = FakeSentMessage(
            self.replies,
            text,
            reply_markup=kwargs.get("reply_markup"),
        )
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


class FakeCallbackQuery:
    def __init__(self, data: str, message: FakeMessage) -> None:
        self.data = data
        self.message = message
        self.answers: list[str] = []

    async def answer(self, text: str) -> None:
        self.answers.append(text)


def make_context(workflow=None, *, result_limit: int | None = None):
    bot_data = {PROCESSING_MIN_SECONDS_KEY: 0}
    if result_limit is not None:
        bot_data[RESULT_LIMIT_KEY] = result_limit
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


def test_text_messages_limit_large_result_sets_to_avoid_spam() -> None:
    message = FakeMessage("7118/1a grey")
    workflow = FakeWorkflow(
        [
            SearchResult(
                listing_text=f"7118/1A grey listing {index}",
                seller="Dealer",
                posted_date="April 20, 2026",
            )
            for index in range(1, 9)
        ]
    )
    context = make_context(workflow, result_limit=3)

    asyncio.run(
        handle_text_message(SimpleNamespace(message=message), context)
    )

    assert workflow.queries == ["7118/1a grey"]
    assert message.photos == []
    assert message.replies == [
        (
            "🏷️ Thông tin:\n"
            "7118/1A grey listing 1\n\n"
            "👤 Người đăng:\n"
            "Dealer\n\n"
            "📅 Ngày đăng:\n"
            "20/04/2026"
        ),
        (
            "🏷️ Thông tin:\n"
            "7118/1A grey listing 2\n\n"
            "👤 Người đăng:\n"
            "Dealer\n\n"
            "📅 Ngày đăng:\n"
            "20/04/2026"
        ),
        (
            "🏷️ Thông tin:\n"
            "7118/1A grey listing 3\n\n"
            "👤 Người đăng:\n"
            "Dealer\n\n"
            "📅 Ngày đăng:\n"
            "20/04/2026"
        ),
        format_result_limit_notice(3, 8),
    ]
    assert message.sent_messages[-1].reply_markup is not None
    assert message.sent_messages[0].text == PROCESSING_MESSAGE
    assert message.sent_messages[0].deleted is True


def test_more_results_callback_sends_next_batch() -> None:
    message = FakeMessage("7118/1a grey")
    workflow = FakeWorkflow(
        [
            SearchResult(
                listing_text=f"7118/1A grey listing {index}",
                seller="Dealer",
                posted_date="April 20, 2026",
            )
            for index in range(1, 7)
        ]
    )
    context = make_context(workflow, result_limit=2)

    asyncio.run(
        handle_text_message(SimpleNamespace(message=message), context)
    )

    notice_markup = message.sent_messages[-1].reply_markup
    token = notice_markup.inline_keyboard[0][0].callback_data.split(":", maxsplit=1)[1]
    callback = FakeCallbackQuery(f"more_results:{token}", message)

    asyncio.run(handle_more_results(SimpleNamespace(callback_query=callback), context))

    assert callback.answers == ["Đang gửi thêm kết quả..."]
    assert message.replies[-3:] == [
        (
            "🏷️ Thông tin:\n"
            "7118/1A grey listing 3\n\n"
            "👤 Người đăng:\n"
            "Dealer\n\n"
            "📅 Ngày đăng:\n"
            "20/04/2026"
        ),
        (
            "🏷️ Thông tin:\n"
            "7118/1A grey listing 4\n\n"
            "👤 Người đăng:\n"
            "Dealer\n\n"
            "📅 Ngày đăng:\n"
            "20/04/2026"
        ),
        "📊 Đã hiển thị 4/6 kết quả.\nBấm “Xem thêm” để nhận batch tiếp theo.",
    ]

    second_notice_markup = message.sent_messages[-1].reply_markup
    second_token = second_notice_markup.inline_keyboard[0][0].callback_data.split(
        ":",
        maxsplit=1,
    )[1]
    second_callback = FakeCallbackQuery(f"more_results:{second_token}", message)

    asyncio.run(
        handle_more_results(SimpleNamespace(callback_query=second_callback), context)
    )

    assert second_callback.answers == ["Đang gửi thêm kết quả..."]
    assert message.replies[-3:] == [
        (
            "🏷️ Thông tin:\n"
            "7118/1A grey listing 5\n\n"
            "👤 Người đăng:\n"
            "Dealer\n\n"
            "📅 Ngày đăng:\n"
            "20/04/2026"
        ),
        (
            "🏷️ Thông tin:\n"
            "7118/1A grey listing 6\n\n"
            "👤 Người đăng:\n"
            "Dealer\n\n"
            "📅 Ngày đăng:\n"
            "20/04/2026"
        ),
        "✅ Đã gửi hết kết quả.",
    ]


def test_more_results_callback_handles_expired_page() -> None:
    message = FakeMessage()
    context = make_context()
    callback = FakeCallbackQuery("more_results:expired", message)

    asyncio.run(handle_more_results(SimpleNamespace(callback_query=callback), context))

    assert callback.answers == ["Kết quả đã hết hạn. Vui lòng search lại."]
    assert message.replies == []


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


def test_default_result_limit_is_conservative_for_telegram_chat() -> None:
    assert DEFAULT_RESULT_LIMIT == 5


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
