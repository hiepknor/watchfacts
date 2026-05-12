from __future__ import annotations

import asyncio
import inspect
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.config import Settings


logger = logging.getLogger(__name__)


START_MESSAGE = (
    "🔎 Dealer Scan Bot\n\n"
    "Gửi mã đồng hồ hoặc mô tả ngắn để quét WatchFacts.\n\n"
    "Ví dụ:\n"
    "• 7118/1a grey\n"
    "• 116500 black\n"
    "• 5712 blue\n\n"
    "Mẹo: thêm màu dial, năm, tình trạng hoặc khoảng giá để kết quả gọn hơn."
)
EMPTY_QUERY_MESSAGE = "Please send a non-empty WatchFacts search query."
UNAUTHORIZED_MESSAGE = "Bạn không có quyền sử dụng bot này."
PROCESSING_MESSAGE = (
    "🔎 Đang quét WatchFacts\n"
    "⏳ Bot đang tìm listing phù hợp..."
)
WORKFLOW_KEY = "search_workflow"
PROCESSING_MIN_SECONDS_KEY = "processing_min_seconds"
DEFAULT_PROCESSING_MIN_SECONDS = 1.0
RESULT_LIMIT_KEY = "result_limit"
DEFAULT_RESULT_LIMIT = 5
RESULT_PAGES_KEY = "result_pages"
MORE_RESULTS_PREFIX = "more_results:"
ALLOWED_USER_IDS_KEY = "allowed_user_ids"


@dataclass(frozen=True)
class SearchResult:
    listing_text: str
    seller: str | None = None
    posted_date: str | None = None
    image_url: str | None = None
    source_url: str | None = None


class SearchWorkflow(Protocol):
    async def search(self, query: str) -> list[SearchResult]:
        ...


class PlaceholderSearchWorkflow:
    async def search(self, query: str) -> list[SearchResult]:
        return [
            SearchResult(
                listing_text=(
                    "Search pipeline is not implemented yet. "
                    f"Received query: {query}"
                )
            )
        ]


async def start_command(update, context) -> None:
    message = getattr(update, "message", None)
    if not _is_authorized(update, context):
        if message is not None:
            await _maybe_await(message.reply_text(UNAUTHORIZED_MESSAGE))
        return
    if message is not None:
        await _maybe_await(message.reply_text(START_MESSAGE))


async def handle_text_message(update, context) -> None:
    message = getattr(update, "message", None)
    if not _is_authorized(update, context):
        if message is not None:
            await _maybe_await(message.reply_text(UNAUTHORIZED_MESSAGE))
        return

    text = getattr(message, "text", None) if message is not None else None
    query = text.strip() if text else ""

    if not query:
        if message is not None:
            await _maybe_await(message.reply_text(EMPTY_QUERY_MESSAGE))
        return

    processing_message = None
    processing_started_at = 0.0
    if message is not None:
        await _send_typing_action(message, context)
        processing_message = await _maybe_await(
            message.reply_text(PROCESSING_MESSAGE)
        )
        processing_started_at = time.monotonic()

    workflow = _get_search_workflow(context)
    try:
        results = await workflow.search(query)
    except Exception as exc:
        await _delete_processing_message(
            processing_message,
            started_at=processing_started_at,
            min_seconds=_processing_min_seconds(context),
        )
        logger.error(
            "event=telegram.search_error error_type=%s query_length=%d",
            exc.__class__.__name__,
            len(query),
        )
        if message is not None:
            await _maybe_await(
                message.reply_text("Search failed. Please check the bot logs.")
            )
        return

    if message is not None:
        await _delete_processing_message(
            processing_message,
            started_at=processing_started_at,
            min_seconds=_processing_min_seconds(context),
        )
        await send_search_results(
            context,
            message,
            results,
            result_limit=_result_limit(context),
        )


async def send_search_results(
    context,
    message,
    results: list[SearchResult],
    *,
    result_limit: int = DEFAULT_RESULT_LIMIT,
) -> None:
    if not results:
        await _maybe_await(message.reply_text("No matching listings found."))
        return

    visible_results = results[:result_limit]
    await _send_result_batch(message, visible_results)

    if len(results) > len(visible_results):
        token = _store_result_page(
            context,
            results=results,
            next_offset=len(visible_results),
            result_limit=result_limit,
        )
        await _maybe_await(
            message.reply_text(
                format_result_limit_notice(len(visible_results), len(results)),
                reply_markup=_more_results_markup(token, len(results) - len(visible_results)),
            )
        )


async def handle_more_results(update, context) -> None:
    callback_query = getattr(update, "callback_query", None)
    if callback_query is None:
        return
    if not _is_authorized(update, context):
        await _maybe_await(callback_query.answer(UNAUTHORIZED_MESSAGE))
        return

    data = getattr(callback_query, "data", "") or ""
    token = data.removeprefix(MORE_RESULTS_PREFIX)
    page = _get_result_page(context, token)
    if not token or page is None:
        await _maybe_await(callback_query.answer("Kết quả đã hết hạn. Vui lòng search lại."))
        return

    await _maybe_await(callback_query.answer("Đang gửi thêm kết quả..."))
    message = getattr(callback_query, "message", None)
    if message is None:
        return

    results = page["results"]
    offset = page["next_offset"]
    limit = page["result_limit"]
    next_results = results[offset : offset + limit]
    next_offset = offset + len(next_results)

    await _send_result_batch(message, next_results)

    remaining = len(results) - next_offset
    if remaining > 0:
        page["next_offset"] = next_offset
        await _maybe_await(
            message.reply_text(
                format_more_results_notice(next_offset, len(results)),
                reply_markup=_more_results_markup(token, remaining),
            )
        )
    else:
        _remove_result_page(context, token)
        await _maybe_await(message.reply_text("✅ Đã gửi hết kết quả."))


def format_search_results(results: list[SearchResult]) -> str:
    if not results:
        return "No matching listings found."

    formatted: list[str] = []
    for result in results:
        sections: list[str] = []
        if result.image_url:
            sections.append(f"📸 Ảnh sản phẩm:\n{result.image_url}")
        sections.append(f"🏷️ Thông tin:\n{result.listing_text}")
        if result.seller:
            sections.append(f"👤 Người đăng:\n{result.seller}")
        if result.posted_date:
            sections.append(f"📅 Ngày đăng:\n{result.posted_date}")
        formatted.append("\n\n".join(sections))

    return "\n\n".join(formatted)


def format_search_result_caption(result: SearchResult) -> str:
    sections = [f"🏷️ Thông tin:\n{result.listing_text}"]
    if result.seller:
        sections.append(f"👤 Người đăng:\n{result.seller}")
    if result.posted_date:
        sections.append(f"📅 Ngày đăng:\n{format_posted_date(result.posted_date)}")
    return "\n\n".join(sections)


def format_result_limit_notice(visible_count: int, total_count: int) -> str:
    return (
        f"📊 Tìm thấy {total_count} kết quả.\n"
        f"Hiển thị {visible_count} kết quả đầu tiên để tránh spam.\n"
        "Bấm “Xem thêm” nếu muốn nhận thêm kết quả.\n"
        "Gợi ý: thêm màu dial, năm, tình trạng hoặc khoảng giá để lọc chính xác hơn."
    )


def format_more_results_notice(visible_count: int, total_count: int) -> str:
    return (
        f"📊 Đã hiển thị {visible_count}/{total_count} kết quả.\n"
        "Bấm “Xem thêm” để nhận batch tiếp theo."
    )


def format_posted_date(value: str) -> str:
    normalized = value.split("·", maxsplit=1)[0].strip()
    for date_format in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(normalized[:19], date_format).strftime("%d/%m/%Y")
        except ValueError:
            continue
    return value


def build_application(settings: Settings, workflow: SearchWorkflow | None = None):
    from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

    application = Application.builder().token(settings.telegram_bot_token).build()
    application.bot_data[WORKFLOW_KEY] = workflow or PlaceholderSearchWorkflow()
    application.bot_data[ALLOWED_USER_IDS_KEY] = settings.telegram_allowed_user_ids
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(handle_more_results, pattern=f"^{MORE_RESULTS_PREFIX}"))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message)
    )
    return application


def run_bot(settings: Settings, workflow: SearchWorkflow | None = None) -> None:
    if workflow is None:
        from app.search import WatchFactsSearchWorkflow

        workflow = WatchFactsSearchWorkflow(settings)

    application = build_application(settings, workflow)
    logger.info("event=bot.starting")
    application.run_polling()


def _get_search_workflow(context) -> SearchWorkflow:
    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application is not None else {}
    return bot_data.get(WORKFLOW_KEY) or PlaceholderSearchWorkflow()


def _processing_min_seconds(context) -> float:
    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application is not None else {}
    value = bot_data.get(PROCESSING_MIN_SECONDS_KEY, DEFAULT_PROCESSING_MIN_SECONDS)
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return DEFAULT_PROCESSING_MIN_SECONDS


def _result_limit(context) -> int:
    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application is not None else {}
    value = bot_data.get(RESULT_LIMIT_KEY, DEFAULT_RESULT_LIMIT)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return DEFAULT_RESULT_LIMIT


def _allowed_user_ids(context) -> tuple[int, ...]:
    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application is not None else {}
    value = bot_data.get(ALLOWED_USER_IDS_KEY, ())
    if value is None:
        return ()
    return tuple(int(user_id) for user_id in value)


def _telegram_user_id(update) -> int | None:
    effective_user = getattr(update, "effective_user", None)
    user_id = getattr(effective_user, "id", None)
    if user_id is not None:
        return int(user_id)

    message = getattr(update, "message", None)
    from_user = getattr(message, "from_user", None) if message is not None else None
    user_id = getattr(from_user, "id", None)
    if user_id is not None:
        return int(user_id)

    callback_query = getattr(update, "callback_query", None)
    from_user = getattr(callback_query, "from_user", None) if callback_query is not None else None
    user_id = getattr(from_user, "id", None)
    if user_id is not None:
        return int(user_id)

    return None


def _is_authorized(update, context) -> bool:
    allowed_user_ids = _allowed_user_ids(context)
    if not allowed_user_ids:
        return True

    user_id = _telegram_user_id(update)
    return user_id in allowed_user_ids


async def _send_result_batch(message, results: list[SearchResult]) -> None:
    for result in results:
        caption = format_search_result_caption(result)
        if result.image_url:
            await _maybe_await(message.reply_photo(photo=result.image_url, caption=caption))
        else:
            await _maybe_await(message.reply_text(caption))


def _store_result_page(
    context,
    *,
    results: list[SearchResult],
    next_offset: int,
    result_limit: int,
) -> str:
    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application is not None else {}
    pages = bot_data.setdefault(RESULT_PAGES_KEY, {})
    token = secrets.token_urlsafe(8)
    pages[token] = {
        "results": results,
        "next_offset": next_offset,
        "result_limit": result_limit,
    }
    return token


def _get_result_page(context, token: str):
    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application is not None else {}
    pages = bot_data.get(RESULT_PAGES_KEY, {})
    return pages.get(token)


def _remove_result_page(context, token: str) -> None:
    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application is not None else {}
    pages = bot_data.get(RESULT_PAGES_KEY, {})
    pages.pop(token, None)


def _more_results_markup(token: str, remaining_count: int):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"Xem thêm {remaining_count}",
                    callback_data=f"{MORE_RESULTS_PREFIX}{token}",
                )
            ]
        ]
    )


async def _send_typing_action(message, context) -> None:
    application = getattr(context, "application", None)
    bot = getattr(application, "bot", None) if application is not None else None
    send_chat_action = getattr(bot, "send_chat_action", None)
    chat_id = getattr(message, "chat_id", None)
    if send_chat_action is None or chat_id is None:
        return
    await _maybe_await(send_chat_action(chat_id=chat_id, action="typing"))


async def _maybe_await(value) -> None:
    if inspect.isawaitable(value):
        return await value
    return value


async def _delete_message(message) -> None:
    if message is None:
        return

    delete = getattr(message, "delete", None)
    if delete is None:
        return
    await _maybe_await(delete())


async def _delete_processing_message(
    message,
    *,
    started_at: float,
    min_seconds: float,
) -> None:
    if started_at > 0:
        elapsed = time.monotonic() - started_at
        remaining = min_seconds - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)
    await _delete_message(message)
