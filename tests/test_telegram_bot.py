from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from app.db import Database
from app.scraper import BrowserSessionError, BrowserSessionStatus
from app.telegram_bot import (
    EMPTY_QUERY_MESSAGE,
    ALLOWED_USER_IDS_KEY,
    CANCEL_EMPTY_MESSAGE,
    CANCEL_MESSAGE,
    HELP_MESSAGE,
    ISSUE_DATABASE_KEY,
    PROCESSING_MIN_SECONDS_KEY,
    PROCESSING_MESSAGE,
    QUEUED_MESSAGE,
    SEARCH_SEMAPHORE_KEY,
    START_MESSAGE,
    TELEGRAM_RESULT_LIMIT_KEY,
    TELEGRAM_PHOTO_CAPTION_LIMIT,
    TELEGRAM_TEXT_MESSAGE_LIMIT,
    UNAUTHORIZED_MESSAGE,
    WATCHFACTS_OWNER_ALERT_MESSAGE,
    WATCHFACTS_SESSION_CHECKER_KEY,
    WATCHFACTS_SESSION_ERROR_MESSAGE,
    WORKFLOW_KEY,
    RESULT_REFINER_KEY,
    SearchResult,
    cancel_command,
    format_result_summary,
    format_health_message,
    format_issue_detail,
    format_issues_message,
    format_posted_date,
    format_settings_message,
    handle_more_results,
    handle_feedback,
    handle_text_message,
    health_command,
    issue_command,
    issues_command,
    issues_export_command,
    help_command,
    settings_command,
    start_command,
)
from app.config import DEFAULT_TELEGRAM_RESULT_LIMIT


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
    def __init__(
        self,
        text: str | None = None,
        *,
        user_id: int = 123,
        chat_type: str = "private",
        reply_to_message=None,
        fail_photos: bool = False,
    ) -> None:
        self.text = text
        self.chat_id = 12345
        self.chat = SimpleNamespace(type=chat_type)
        self.from_user = SimpleNamespace(id=user_id)
        self.reply_to_message = reply_to_message
        self.replies: list[str] = []
        self.photos: list[tuple[str, str]] = []
        self.sent_messages: list[FakeSentMessage] = []
        self.fail_photos = fail_photos

    async def reply_text(self, text: str, **kwargs) -> FakeSentMessage:
        self.replies.append(text)
        sent_message = FakeSentMessage(
            self.replies,
            text,
            reply_markup=kwargs.get("reply_markup"),
        )
        self.sent_messages.append(sent_message)
        return sent_message

    async def reply_photo(self, photo: str, caption: str, **kwargs) -> None:
        if self.fail_photos:
            raise TimeoutError("photo timeout")
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


class ExpiredSessionWorkflow:
    async def search(self, query: str) -> list[SearchResult]:
        raise BrowserSessionError("Saved browser session appears expired. cookie=secret")


class BlockingWorkflow:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.queries: list[str] = []

    async def search(self, query: str) -> list[SearchResult]:
        self.queries.append(query)
        self.started.set()
        await self.release.wait()
        return [SearchResult(f"result {query}")]


class FakeBot:
    def __init__(self) -> None:
        self.id = 777
        self.username = "DealerScanBot"
        self.chat_actions: list[tuple[int, str]] = []
        self.sent_messages: list[tuple[int, str]] = []

    async def send_chat_action(self, *, chat_id: int, action: str) -> None:
        self.chat_actions.append((chat_id, action))

    async def send_message(self, *, chat_id: int, text: str) -> None:
        self.sent_messages.append((chat_id, text))


class FakeCallbackQuery:
    def __init__(self, data: str, message: FakeMessage, *, user_id: int = 123) -> None:
        self.data = data
        self.message = message
        self.from_user = SimpleNamespace(id=user_id)
        self.answers: list[str] = []

    async def answer(self, text: str) -> None:
        self.answers.append(text)


def make_context(
    workflow=None,
    *,
    db_path=None,
    refiner=None,
    session_checker=None,
    result_limit: int | None = None,
    allowed_user_ids: tuple[int, ...] = (),
):
    bot_data = {PROCESSING_MIN_SECONDS_KEY: 0}
    bot_data[ALLOWED_USER_IDS_KEY] = allowed_user_ids
    if result_limit is not None:
        bot_data[TELEGRAM_RESULT_LIMIT_KEY] = result_limit
    if workflow is not None:
        bot_data[WORKFLOW_KEY] = workflow
    bot_data[SEARCH_SEMAPHORE_KEY] = asyncio.Semaphore(1)
    if refiner is not None:
        bot_data[RESULT_REFINER_KEY] = refiner
    if session_checker is not None:
        bot_data[WATCHFACTS_SESSION_CHECKER_KEY] = session_checker
    if db_path is not None:
        bot_data[ISSUE_DATABASE_KEY] = Database(db_path)
    bot = FakeBot()
    return SimpleNamespace(bot=bot, application=SimpleNamespace(bot=bot, bot_data=bot_data))


def test_start_command_returns_usage_message() -> None:
    message = FakeMessage()

    asyncio.run(start_command(SimpleNamespace(message=message), make_context()))

    assert message.replies == [START_MESSAGE]


def test_start_command_rejects_unauthorized_user() -> None:
    message = FakeMessage(user_id=999)
    context = make_context(allowed_user_ids=(123,))

    asyncio.run(start_command(SimpleNamespace(message=message), context))

    assert message.replies == [UNAUTHORIZED_MESSAGE]


def test_help_command_returns_visual_usage_message() -> None:
    message = FakeMessage()

    asyncio.run(help_command(SimpleNamespace(message=message), make_context()))

    assert message.replies == [HELP_MESSAGE]
    assert "Xem kết quả" in message.replies[0]
    assert "/cancel" in message.replies[0]
    assert "/health" in message.replies[0]


def test_help_command_rejects_unauthorized_user() -> None:
    message = FakeMessage(user_id=999)
    context = make_context(allowed_user_ids=(123,))

    asyncio.run(help_command(SimpleNamespace(message=message), context))

    assert message.replies == [UNAUTHORIZED_MESSAGE]


def test_settings_command_returns_safe_runtime_settings() -> None:
    message = FakeMessage()
    context = make_context(result_limit=7, allowed_user_ids=(123, 456))

    asyncio.run(settings_command(SimpleNamespace(message=message), context))

    assert message.replies == [
        (
            "⚙️ Cấu hình bot\n\n"
            "🔐 Quyền truy cập: Chỉ chủ bot\n"
            "👤 ID chủ bot: 2\n"
            "📨 Kết quả mỗi lượt: 7\n\n"
            "🔒 Mã bot, cookie và trạng thái trình duyệt không bao giờ hiển thị ở đây."
        )
    ]
    assert "token=" not in message.replies[0].lower()


def test_health_command_reports_valid_watchfacts_session() -> None:
    message = FakeMessage()

    async def checker() -> BrowserSessionStatus:
        return BrowserSessionStatus(ok=True, status="valid", detail="cookie=secret")

    asyncio.run(
        health_command(
            SimpleNamespace(message=message),
            make_context(session_checker=checker),
        )
    )

    assert message.replies == [
        (
            "🩺 Kiểm tra hệ thống\n\n"
            "🟢 WatchFacts session: hợp lệ\n"
            "📨 Bot Telegram: đang phản hồi\n\n"
            "✅ Bot có thể dùng session hiện tại để quét WatchFacts.\n\n"
            "🔒 Không hiển thị cookie, token hoặc browser state."
        )
    ]
    assert "cookie=secret" not in message.replies[0]


def test_health_command_reports_expired_watchfacts_session() -> None:
    message = FakeMessage()

    async def checker() -> BrowserSessionStatus:
        return BrowserSessionStatus(ok=False, status="expired", detail="login page")

    asyncio.run(
        health_command(
            SimpleNamespace(message=message),
            make_context(session_checker=checker),
        )
    )

    assert "🟠 WatchFacts session: đã hết hạn" in message.replies[0]
    assert "Đăng nhập lại WatchFacts" in message.replies[0]


def test_format_health_message_handles_missing_checker() -> None:
    assert format_health_message(None) == (
        "🩺 Kiểm tra hệ thống\n\n"
        "⚪ WatchFacts session: chưa cấu hình checker\n"
        "📨 Bot Telegram: đang phản hồi\n\n"
        "🔒 Không hiển thị cookie, token hoặc browser state."
    )


def test_issues_command_lists_open_issues(tmp_path) -> None:
    db_path = tmp_path / "data" / "bot.db"
    database = Database(db_path)
    issue_id = database.record_result_feedback(
        query_text="5712r",
        result_rank=26,
        reason="missing_info",
        listing_text="5712R 2016/ HKD",
        raw_listing_text="5712R 2016/ HKD 830000",
        seller="AM.Timepiece TONY",
        source_url="/flash-sales/9927122",
        telegram_user_id=123,
    )
    message = FakeMessage()
    context = make_context(db_path=db_path, allowed_user_ids=(123,))

    asyncio.run(issues_command(SimpleNamespace(message=message), context))

    assert f"#F{issue_id} ⚠️ Thiếu thông tin" in message.replies[0]
    assert "🔎 Query: 5712r" in message.replies[0]
    assert "🏷️ Bot gửi: 5712R 2016/ HKD" in message.replies[0]
    assert "cookie" not in message.replies[0].casefold()


def test_issue_command_shows_issue_detail(tmp_path) -> None:
    db_path = tmp_path / "data" / "bot.db"
    issue_id = Database(db_path).record_result_feedback(
        query_text="5712r",
        result_rank=26,
        reason="missing_info",
        listing_text="5712R 2016/ HKD",
        raw_listing_text="5712R 2016/ HKD 830000",
        seller="AM.Timepiece TONY",
        source_url="/flash-sales/9927122",
        telegram_user_id=123,
    )
    message = FakeMessage()
    context = make_context(db_path=db_path, allowed_user_ids=(123,))
    context.args = [f"F{issue_id}"]

    asyncio.run(issue_command(SimpleNamespace(message=message), context))

    assert f"🧾 Issue #F{issue_id}" in message.replies[0]
    assert "5712R 2016/ HKD 830000" in message.replies[0]
    assert "🔒 Không hiển thị cookie" in message.replies[0]


def test_issues_export_command_returns_json(tmp_path) -> None:
    db_path = tmp_path / "data" / "bot.db"
    Database(db_path).record_result_feedback(
        query_text="5712r",
        result_rank=26,
        reason="missing_info",
        listing_text="5712R 2016/ HKD",
        raw_listing_text="5712R 2016/ HKD 830000",
        seller="AM.Timepiece TONY",
        source_url="/flash-sales/9927122",
        telegram_user_id=123,
    )
    message = FakeMessage()

    asyncio.run(
        issues_export_command(
            SimpleNamespace(message=message),
            make_context(db_path=db_path, allowed_user_ids=(123,)),
        )
    )

    assert "📤 Export issue regression" in message.replies[0]
    assert '"query": "5712r"' in message.replies[0]
    assert '"raw_text": "5712R 2016/ HKD 830000"' in message.replies[0]


def test_format_settings_message_shows_public_access() -> None:
    assert format_settings_message(make_context(result_limit=5)) == (
        "⚙️ Cấu hình bot\n\n"
        "🔐 Quyền truy cập: Công khai\n"
        "👤 ID chủ bot: Không giới hạn\n"
        "📨 Kết quả mỗi lượt: 5\n\n"
        "🔒 Mã bot, cookie và trạng thái trình duyệt không bao giờ hiển thị ở đây."
    )


def test_cancel_command_clears_pending_result_pages() -> None:
    message = FakeMessage("7118/1a grey")
    workflow = FakeWorkflow([SearchResult("7118/1A grey")])
    context = make_context(workflow)

    asyncio.run(handle_text_message(SimpleNamespace(message=message), context))

    assert context.application.bot_data["result_pages"]

    asyncio.run(cancel_command(SimpleNamespace(message=message), context))

    assert message.replies[-1] == CANCEL_MESSAGE
    assert context.application.bot_data["result_pages"] == {}


def test_cancel_command_handles_empty_result_pages() -> None:
    message = FakeMessage()

    asyncio.run(cancel_command(SimpleNamespace(message=message), make_context()))

    assert message.replies == [CANCEL_EMPTY_MESSAGE]


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


def test_text_messages_are_public_when_allowed_user_ids_are_empty() -> None:
    message = FakeMessage("228253a choco", user_id=999)
    workflow = FakeWorkflow([SearchResult("228253A choco N2")])
    context = make_context(workflow, allowed_user_ids=())

    asyncio.run(handle_text_message(SimpleNamespace(message=message), context))

    assert workflow.queries == ["228253a choco"]


def test_group_regular_chat_is_ignored() -> None:
    message = FakeMessage("chơi đi a", chat_type="group")
    workflow = FakeWorkflow([SearchResult("unused")])
    context = make_context(workflow)

    asyncio.run(handle_text_message(SimpleNamespace(message=message), context))

    assert workflow.queries == []
    assert message.replies == []
    assert context.application.bot.chat_actions == []


def test_group_mention_triggers_search_with_mention_removed() -> None:
    message = FakeMessage("@DealerScanBot 7118/1a grey", chat_type="supergroup")
    workflow = FakeWorkflow([SearchResult("7118/1A grey")])
    context = make_context(workflow)

    asyncio.run(handle_text_message(SimpleNamespace(message=message), context))

    assert workflow.queries == ["7118/1a grey"]
    assert message.replies == [format_result_summary(1, DEFAULT_TELEGRAM_RESULT_LIMIT)]


def test_group_reply_to_bot_triggers_search() -> None:
    bot_message = SimpleNamespace(from_user=SimpleNamespace(id=777, username="DealerScanBot"))
    message = FakeMessage(
        "7118/1a grey",
        chat_type="group",
        reply_to_message=bot_message,
    )
    workflow = FakeWorkflow([SearchResult("7118/1A grey")])
    context = make_context(workflow)

    asyncio.run(handle_text_message(SimpleNamespace(message=message), context))

    assert workflow.queries == ["7118/1a grey"]
    assert message.replies == [format_result_summary(1, DEFAULT_TELEGRAM_RESULT_LIMIT)]


def test_text_messages_reject_unauthorized_user_before_search() -> None:
    message = FakeMessage("228253a choco", user_id=999)
    workflow = FakeWorkflow([SearchResult("unused")])
    context = make_context(workflow, allowed_user_ids=(123,))

    asyncio.run(handle_text_message(SimpleNamespace(message=message), context))

    assert message.replies == [UNAUTHORIZED_MESSAGE]
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
    assert message.replies == [format_result_summary(1, DEFAULT_TELEGRAM_RESULT_LIMIT)]
    assert message.sent_messages[0].text == PROCESSING_MESSAGE
    assert message.sent_messages[0].deleted is True
    assert context.application.bot.chat_actions == [(12345, "typing")]
    assert message.sent_messages[-1].reply_markup is not None
    assert message.photos == []


def test_concurrent_text_messages_show_queue_notice() -> None:
    async def run_test() -> None:
        first_message = FakeMessage("6159G")
        second_message = FakeMessage("Fpj Elegante Titanium")
        workflow = BlockingWorkflow()
        context = make_context(workflow)

        first_task = asyncio.create_task(
            handle_text_message(SimpleNamespace(message=first_message), context)
        )
        await workflow.started.wait()

        second_task = asyncio.create_task(
            handle_text_message(SimpleNamespace(message=second_message), context)
        )
        await asyncio.sleep(0)

        assert QUEUED_MESSAGE in second_message.replies
        assert PROCESSING_MESSAGE in second_message.replies
        assert workflow.queries == ["6159G"]

        workflow.release.set()
        await first_task
        await second_task

        assert workflow.queries == ["6159G", "Fpj Elegante Titanium"]
        assert QUEUED_MESSAGE not in second_message.replies
        assert second_message.replies == [format_result_summary(1, DEFAULT_TELEGRAM_RESULT_LIMIT)]

    asyncio.run(run_test())


def test_results_callback_sends_first_photo_batch() -> None:
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

    notice_markup = message.sent_messages[-1].reply_markup
    token = notice_markup.inline_keyboard[0][0].callback_data.split(":", maxsplit=1)[1]
    callback = FakeCallbackQuery(f"more_results:{token}", message)

    asyncio.run(handle_more_results(SimpleNamespace(callback_query=callback), context))

    assert callback.answers == ["Đang gửi thêm kết quả..."]
    assert message.photos == [
        (
            "https://image-url.jpg",
            (
                "🏷️ 228253A choco N2\n\n"
                "👤 HK STOCKS\n\n"
                "📅 20/04/2026"
            ),
        )
    ]
    assert message.replies[-1] == "✅ Đã gửi hết kết quả."


def test_results_callback_refines_only_requested_page() -> None:
    message = FakeMessage("Fpj Elegante Titanium")
    workflow = FakeWorkflow(
        [
            SearchResult("raw page one"),
            SearchResult("raw page two"),
        ]
    )
    refine_calls: list[tuple[str, list[SearchResult]]] = []

    async def refiner(query: str, results: list[SearchResult]) -> list[SearchResult]:
        refine_calls.append((query, results))
        return [SearchResult(f"refined {result.listing_text}") for result in results]

    context = make_context(workflow, refiner=refiner, result_limit=1)

    asyncio.run(handle_text_message(SimpleNamespace(message=message), context))

    notice_markup = message.sent_messages[-1].reply_markup
    token = notice_markup.inline_keyboard[0][0].callback_data.split(":", maxsplit=1)[1]
    callback = FakeCallbackQuery(f"more_results:{token}", message)

    asyncio.run(handle_more_results(SimpleNamespace(callback_query=callback), context))

    assert refine_calls[0] == ("Fpj Elegante Titanium", [SearchResult("raw page one")])
    assert message.replies[-2:] == [
        "🏷️ refined raw page one",
        "📊 Đã hiển thị 1/2 kết quả.\nBấm “Xem thêm” để nhận lượt tiếp theo.",
    ]


def test_results_callback_limits_long_photo_caption() -> None:
    message = FakeMessage("5164a")
    workflow = FakeWorkflow(
        [
            SearchResult(
                listing_text="5164A " + ("retail ready full set " * 80),
                seller="BP",
                posted_date="April 9, 2026",
                image_url="https://image-url.jpg",
            )
        ]
    )
    context = make_context(workflow)

    asyncio.run(handle_text_message(SimpleNamespace(message=message), context))

    notice_markup = message.sent_messages[-1].reply_markup
    token = notice_markup.inline_keyboard[0][0].callback_data.split(":", maxsplit=1)[1]
    callback = FakeCallbackQuery(f"more_results:{token}", message)

    asyncio.run(handle_more_results(SimpleNamespace(callback_query=callback), context))

    caption = message.photos[0][1]
    assert len(caption) == TELEGRAM_PHOTO_CAPTION_LIMIT
    assert caption.endswith("…")


def test_results_callback_falls_back_to_text_and_keeps_more_button_on_photo_timeout() -> None:
    message = FakeMessage("6159G", fail_photos=True)
    workflow = FakeWorkflow(
        [
            SearchResult(
                listing_text=f"6159G listing {index}",
                seller="Dealer",
                posted_date="April 20, 2026",
                image_url=f"https://image-{index}.jpg",
            )
            for index in range(1, 4)
        ]
    )
    context = make_context(workflow, result_limit=2)

    asyncio.run(handle_text_message(SimpleNamespace(message=message), context))

    notice_markup = message.sent_messages[-1].reply_markup
    token = notice_markup.inline_keyboard[0][0].callback_data.split(":", maxsplit=1)[1]
    callback = FakeCallbackQuery(f"more_results:{token}", message)

    asyncio.run(handle_more_results(SimpleNamespace(callback_query=callback), context))

    assert message.photos == []
    assert message.replies[-3:] == [
        (
            "🏷️ 6159G listing 1\n\n"
            "👤 Dealer\n\n"
            "📅 20/04/2026"
        ),
        (
            "🏷️ 6159G listing 2\n\n"
            "👤 Dealer\n\n"
            "📅 20/04/2026"
        ),
        "📊 Đã hiển thị 2/3 kết quả.\nBấm “Xem thêm” để nhận lượt tiếp theo.",
    ]
    assert message.sent_messages[-1].reply_markup.inline_keyboard[0][0].text == "Xem thêm 1"


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

    assert message.photos == []
    notice_markup = message.sent_messages[-1].reply_markup
    token = notice_markup.inline_keyboard[0][0].callback_data.split(":", maxsplit=1)[1]
    callback = FakeCallbackQuery(f"more_results:{token}", message)

    asyncio.run(handle_more_results(SimpleNamespace(callback_query=callback), context))

    assert len(message.photos) == 2
    assert message.photos[0] == (
        "https://image-1.jpg",
        (
            "🏷️ 7118/1200A blue 2/2026 $725000\n\n"
            "👤 Ella\n\n"
            "📅 17/04/2026"
        ),
    )
    assert message.photos[1][0] == "https://image-2.jpg"
    assert "Forest" in message.photos[1][1]
    assert "23/04/2026" in message.photos[1][1]
    assert message.replies == [format_result_summary(2, DEFAULT_TELEGRAM_RESULT_LIMIT), "✅ Đã gửi hết kết quả."]
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
        format_result_summary(8, 3),
    ]
    assert message.replies[0] == (
        "✅ Đã tìm xong\n\n"
        "📦 Kết quả chính: 8\n"
        "🔁 Không có listing tương tự bị gộp\n"
        "📨 Lượt đầu: 3 kết quả\n\n"
        "👇 Bấm “Xem kết quả” để bắt đầu nhận danh sách.\n"
        "💡 Muốn gọn hơn: thêm màu dial, năm, tình trạng hoặc khoảng giá."
    )
    assert "Similar listings sẽ hiện" not in message.replies[0]
    assert message.sent_messages[-1].reply_markup is not None
    button = message.sent_messages[-1].reply_markup.inline_keyboard[0][0]
    assert button.text == "Xem kết quả 3"
    assert message.sent_messages[0].text == PROCESSING_MESSAGE
    assert message.sent_messages[0].deleted is True


def test_result_summary_counts_grouped_similar_listings() -> None:
    message = FakeMessage("Fpj Elegante Titanium")
    workflow = FakeWorkflow(
        [
            SearchResult(
                "FPJ Elegante Titanium 48mm 2019 full set 780000",
                similar_results=(
                    SearchResult("FPJ Elegante titanium 48mm 2019 fullset 780000 hkd"),
                    SearchResult("FPJ Elegante titanium ti 48mm2019 used 780000"),
                ),
            ),
            SearchResult("FPJ Elegante Titanium 48mm 2022 Fullset HKD895,000"),
        ]
    )
    context = make_context(workflow, result_limit=5)

    asyncio.run(handle_text_message(SimpleNamespace(message=message), context))

    assert message.replies == [format_result_summary(2, 5, similar_count=2)]
    assert "📦 Kết quả chính: 2" in message.replies[0]
    assert "🔁 Listing tương tự đã gộp: 2" in message.replies[0]
    assert "🔎 Similar listings sẽ hiện bên trong từng kết quả." in message.replies[0]


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
            "🏷️ 7118/1A grey listing 1\n\n"
            "👤 Dealer\n\n"
            "📅 20/04/2026"
        ),
        (
            "🏷️ 7118/1A grey listing 2\n\n"
            "👤 Dealer\n\n"
            "📅 20/04/2026"
        ),
        "📊 Đã hiển thị 2/6 kết quả.\nBấm “Xem thêm” để nhận lượt tiếp theo.",
    ]
    assert message.sent_messages[-1].reply_markup.inline_keyboard[0][0].text == "Xem thêm 2"

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
            "🏷️ 7118/1A grey listing 3\n\n"
            "👤 Dealer\n\n"
            "📅 20/04/2026"
        ),
        (
            "🏷️ 7118/1A grey listing 4\n\n"
            "👤 Dealer\n\n"
            "📅 20/04/2026"
        ),
        "📊 Đã hiển thị 4/6 kết quả.\nBấm “Xem thêm” để nhận lượt tiếp theo.",
    ]

    final_notice_markup = message.sent_messages[-1].reply_markup
    final_token = final_notice_markup.inline_keyboard[0][0].callback_data.split(
        ":",
        maxsplit=1,
    )[1]
    final_callback = FakeCallbackQuery(f"more_results:{final_token}", message)

    asyncio.run(
        handle_more_results(SimpleNamespace(callback_query=final_callback), context)
    )

    assert final_callback.answers == ["Đang gửi thêm kết quả..."]
    assert message.replies[-3:] == [
        (
            "🏷️ 7118/1A grey listing 5\n\n"
            "👤 Dealer\n\n"
            "📅 20/04/2026"
        ),
        (
            "🏷️ 7118/1A grey listing 6\n\n"
            "👤 Dealer\n\n"
            "📅 20/04/2026"
        ),
        "✅ Đã gửi hết kết quả.",
    ]


def test_more_results_callback_handles_expired_page() -> None:
    message = FakeMessage()
    context = make_context()
    callback = FakeCallbackQuery("more_results:expired", message)

    asyncio.run(handle_more_results(SimpleNamespace(callback_query=callback), context))

    assert callback.answers == ["Kết quả đã hết hạn. Vui lòng tìm lại."]
    assert message.replies == []


def test_more_results_callback_rejects_unauthorized_user() -> None:
    message = FakeMessage()
    context = make_context(allowed_user_ids=(123,))
    callback = FakeCallbackQuery("more_results:anything", message, user_id=999)

    asyncio.run(handle_more_results(SimpleNamespace(callback_query=callback), context))

    assert callback.answers == [UNAUTHORIZED_MESSAGE]
    assert message.replies == []


def test_feedback_callback_records_missing_info(tmp_path) -> None:
    message = FakeMessage("5712r")
    workflow = FakeWorkflow(
        [
            SearchResult(
                "5712R 2016/ HKD",
                seller="AM.Timepiece TONY",
                posted_date="February 14, 2026",
                source_url="/flash-sales/9927122",
                raw_listing_text="5712R 2016/ HKD 830000",
            )
        ]
    )
    db_path = tmp_path / "data" / "bot.db"
    context = make_context(workflow, db_path=db_path, allowed_user_ids=(123,))

    asyncio.run(handle_text_message(SimpleNamespace(message=message), context))
    result_token = message.sent_messages[-1].reply_markup.inline_keyboard[0][0].callback_data.split(":", maxsplit=1)[1]
    asyncio.run(
        handle_more_results(
            SimpleNamespace(callback_query=FakeCallbackQuery(f"more_results:{result_token}", message)),
            context,
        )
    )
    feedback_data = message.sent_messages[-2].reply_markup.inline_keyboard[0][0].callback_data
    callback = FakeCallbackQuery(feedback_data, message)

    asyncio.run(handle_feedback(SimpleNamespace(callback_query=callback), context))

    assert callback.answers == [
        "📝 Đã ghi nhận. Mình đã lưu case này để owner review sau."
    ]
    issue = Database(db_path).list_open_issues()[0]
    assert issue.reason == "missing_info"
    assert issue.query_text == "5712r"
    assert issue.listing_text == "5712R 2016/ HKD"
    assert issue.raw_listing_text == "5712R 2016/ HKD 830000"


def test_feedback_callback_rejects_unauthorized_user(tmp_path) -> None:
    message = FakeMessage("5712r")
    context = make_context(db_path=tmp_path / "data" / "bot.db", allowed_user_ids=(123,))
    callback = FakeCallbackQuery("feedback:missing:missing_info", message, user_id=999)

    asyncio.run(handle_feedback(SimpleNamespace(callback_query=callback), context))

    assert callback.answers == [UNAUTHORIZED_MESSAGE]


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
    assert message.replies == [format_result_summary(1, DEFAULT_TELEGRAM_RESULT_LIMIT)]
    notice_markup = message.sent_messages[-1].reply_markup
    token = notice_markup.inline_keyboard[0][0].callback_data.split(":", maxsplit=1)[1]
    callback = FakeCallbackQuery(f"more_results:{token}", message)

    asyncio.run(handle_more_results(SimpleNamespace(callback_query=callback), context))

    assert message.replies[-2:] == [
        (
            "🏷️ 228253A choco N2\n\n"
            "👤 HK STOCKS\n\n"
            "📅 20/04/2026"
        ),
        "✅ Đã gửi hết kết quả.",
    ]
    assert message.sent_messages[0].text == PROCESSING_MESSAGE
    assert message.sent_messages[0].deleted is True
    assert context.application.bot.chat_actions == [(12345, "typing")]


def test_results_callback_limits_long_text_message() -> None:
    message = FakeMessage("5164a")
    workflow = FakeWorkflow(
        [
            SearchResult(
                listing_text="5164A " + ("retail ready full set " * 250),
                seller="BP",
                posted_date="April 9, 2026",
            )
        ]
    )
    context = make_context(workflow)

    asyncio.run(handle_text_message(SimpleNamespace(message=message), context))

    notice_markup = message.sent_messages[-1].reply_markup
    token = notice_markup.inline_keyboard[0][0].callback_data.split(":", maxsplit=1)[1]
    callback = FakeCallbackQuery(f"more_results:{token}", message)

    asyncio.run(handle_more_results(SimpleNamespace(callback_query=callback), context))

    result_message = message.replies[-2]
    assert len(result_message) == TELEGRAM_TEXT_MESSAGE_LIMIT
    assert result_message.endswith("…")


def test_format_posted_date_handles_reposted_suffix() -> None:
    assert format_posted_date("April 22, 2026 · Reposted 2x") == "22/04/2026"


def test_default_result_limit_is_conservative_for_telegram_chat() -> None:
    assert DEFAULT_TELEGRAM_RESULT_LIMIT == 5


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

    assert message.replies == [
        (
            "⚠️ Tìm kiếm thất bại\n\n"
            "Vui lòng thử lại sau hoặc kiểm tra nhật ký bot nếu lỗi tiếp diễn."
        )
    ]
    assert message.sent_messages[0].text == PROCESSING_MESSAGE
    assert message.sent_messages[0].deleted is True
    assert context.application.bot.chat_actions == [(12345, "typing")]
    assert (
        "event=telegram.search_error error_type=RuntimeError query_length=13"
        in caplog.text
    )
    assert "228253a choco" not in caplog.text
    assert "token" not in caplog.text


def test_watchfacts_session_error_notifies_owner_in_vietnamese(caplog) -> None:
    message = FakeMessage("5712r", user_id=123)
    context = make_context(ExpiredSessionWorkflow(), allowed_user_ids=(123, 456))

    with caplog.at_level(logging.ERROR, logger="app.telegram_bot"):
        asyncio.run(
            handle_text_message(
                SimpleNamespace(message=message),
                context,
            )
        )

    assert message.replies == [WATCHFACTS_SESSION_ERROR_MESSAGE]
    assert context.application.bot.sent_messages == [
        (123, WATCHFACTS_OWNER_ALERT_MESSAGE),
        (456, WATCHFACTS_OWNER_ALERT_MESSAGE),
    ]
    assert "🚨 WatchFacts session cần xử lý" in WATCHFACTS_OWNER_ALERT_MESSAGE
    assert "Đăng nhập lại WatchFacts" in WATCHFACTS_OWNER_ALERT_MESSAGE
    assert "cookie=secret" not in WATCHFACTS_OWNER_ALERT_MESSAGE
    assert "event=telegram.watchfacts_session_error" in caplog.text
    assert "5712r" not in caplog.text
    assert "cookie=secret" not in caplog.text


def test_watchfacts_session_owner_alert_is_debounced() -> None:
    message = FakeMessage("5712r", user_id=123)
    context = make_context(ExpiredSessionWorkflow(), allowed_user_ids=(123,))

    asyncio.run(handle_text_message(SimpleNamespace(message=message), context))
    asyncio.run(handle_text_message(SimpleNamespace(message=message), context))

    assert context.application.bot.sent_messages == [
        (123, WATCHFACTS_OWNER_ALERT_MESSAGE)
    ]
