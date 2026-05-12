from __future__ import annotations

import asyncio
import inspect
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.config import DEFAULT_TELEGRAM_RESULT_LIMIT, Settings


logger = logging.getLogger(__name__)


START_MESSAGE = (
    "🔎 Dealer Scan Bot\n\n"
    "Gửi mã đồng hồ hoặc mô tả ngắn để quét WatchFacts.\n"
    "Bot sẽ gửi tóm tắt trước, rồi bạn bấm nút để nhận từng lượt kết quả.\n\n"
    "Ví dụ nhanh:\n"
    "• 7118/1a grey\n"
    "• 116500 black\n"
    "• 5712 blue\n\n"
    "Trong nhóm: gọi bot bằng @username hoặc trả lời tin nhắn của bot.\n\n"
    "Gõ /help để xem hướng dẫn đầy đủ."
)
HELP_MESSAGE = (
    "📘 Hướng dẫn sử dụng\n\n"
    "1️⃣ Gửi truy vấn\n"
    "• 7118/1a grey\n"
    "• 116500 black 2023\n"
    "• 228253a choco\n\n"
    "2️⃣ Nhận tóm tắt\n"
    "Bot báo tổng số kết quả và số kết quả mỗi lượt.\n\n"
    "3️⃣ Bấm nút\n"
    "• Xem kết quả: nhận lượt đầu\n"
    "• Xem thêm: nhận lượt tiếp theo\n\n"
    "👥 Trong nhóm\n"
    "• @bot_username 7118/1a grey\n"
    "• Hoặc trả lời tin nhắn của bot với truy vấn mới\n\n"
    "🧹 /cancel để xóa các nút kết quả đang chờ.\n"
    "⚙️ /settings để xem cấu hình bot hiện tại."
)
EMPTY_QUERY_MESSAGE = (
    "⚠️ Truy vấn đang trống\n\n"
    "Vui lòng gửi mã đồng hồ hoặc mô tả ngắn để bot tìm trên WatchFacts."
)
UNAUTHORIZED_MESSAGE = (
    "🔒 Không có quyền truy cập\n\n"
    "Tài khoản Telegram này chưa được phép sử dụng bot."
)
CANCEL_MESSAGE = (
    "🧹 Đã dọn phiên kết quả\n\n"
    "Các nút “Xem kết quả” / “Xem thêm” cũ sẽ hết hiệu lực.\n"
    "Gửi truy vấn mới để bắt đầu lại."
)
CANCEL_EMPTY_MESSAGE = (
    "🧹 Không có phiên kết quả nào đang chờ.\n\n"
    "Gửi truy vấn mới để tìm WatchFacts."
)
PROCESSING_MESSAGE = (
    "🔎 Đang quét WatchFacts\n"
    "⏳ Bot đang tìm mẫu tin phù hợp..."
)
QUEUED_MESSAGE = (
    "⏳ Đang chờ lượt quét\n"
    "Bot đang xử lý truy vấn khác. Truy vấn này sẽ tự chạy ngay khi có slot."
)
SEARCH_ERROR_MESSAGE = (
    "⚠️ Tìm kiếm thất bại\n\n"
    "Vui lòng thử lại sau hoặc kiểm tra nhật ký bot nếu lỗi tiếp diễn."
)
NO_RESULTS_MESSAGE = (
    "🔍 Không tìm thấy kết quả phù hợp\n\n"
    "Bạn có thể thử mã khác, thêm/bớt màu mặt số, năm, tình trạng hoặc khoảng giá."
)
WORKFLOW_KEY = "search_workflow"
PROCESSING_MIN_SECONDS_KEY = "processing_min_seconds"
DEFAULT_PROCESSING_MIN_SECONDS = 1.0
TELEGRAM_RESULT_LIMIT_KEY = "telegram_result_limit"
TELEGRAM_MAX_CONCURRENT_SEARCHES_KEY = "telegram_max_concurrent_searches"
RESULT_PAGES_KEY = "result_pages"
RESULT_REFINER_KEY = "result_refiner"
SEARCH_SEMAPHORE_KEY = "search_semaphore"
MORE_RESULTS_PREFIX = "more_results:"
ALLOWED_USER_IDS_KEY = "allowed_user_ids"
TELEGRAM_PHOTO_CAPTION_LIMIT = 1024
TELEGRAM_TEXT_MESSAGE_LIMIT = 4096


@dataclass(frozen=True)
class SearchResult:
    listing_text: str
    seller: str | None = None
    posted_date: str | None = None
    image_url: str | None = None
    source_url: str | None = None
    similar_results: tuple["SearchResult", ...] = ()


class SearchWorkflow(Protocol):
    async def search(self, query: str) -> list[SearchResult]:
        ...


RefineResults = Callable[[str, list[SearchResult]], Awaitable[list[SearchResult]]]


class PlaceholderSearchWorkflow:
    async def search(self, query: str) -> list[SearchResult]:
        return [
            SearchResult(
                listing_text=(
                    "Luồng tìm kiếm chưa được cấu hình. "
                    f"Truy vấn đã nhận: {query}"
                )
            )
        ]


async def start_command(update, context) -> None:
    message = getattr(update, "message", None)
    if await _reject_unauthorized(update, context, message):
        return
    if message is not None:
        await _maybe_await(message.reply_text(START_MESSAGE))


async def help_command(update, context) -> None:
    message = getattr(update, "message", None)
    if await _reject_unauthorized(update, context, message):
        return
    if message is not None:
        await _maybe_await(message.reply_text(HELP_MESSAGE))


async def settings_command(update, context) -> None:
    message = getattr(update, "message", None)
    if await _reject_unauthorized(update, context, message):
        return
    if message is not None:
        await _maybe_await(message.reply_text(format_settings_message(context)))


async def cancel_command(update, context) -> None:
    message = getattr(update, "message", None)
    if await _reject_unauthorized(update, context, message):
        return
    cleared_count = _clear_result_pages(context)
    if message is not None:
        await _maybe_await(
            message.reply_text(CANCEL_MESSAGE if cleared_count else CANCEL_EMPTY_MESSAGE)
        )


async def handle_text_message(update, context) -> None:
    message = getattr(update, "message", None)
    if await _reject_unauthorized(update, context, message):
        return

    query = _query_text_from_message(update, context, message)
    if query is None:
        return

    if not query:
        if message is not None:
            await _maybe_await(message.reply_text(EMPTY_QUERY_MESSAGE))
        return

    processing_message = None
    queued_message = None
    processing_started_at = 0.0
    if message is not None:
        await _send_typing_action(message, context)
        search_semaphore = _search_semaphore(context)
        if search_semaphore.locked():
            queued_message = await _maybe_await(message.reply_text(QUEUED_MESSAGE))
        processing_message = await _maybe_await(
            message.reply_text(PROCESSING_MESSAGE)
        )
        processing_started_at = time.monotonic()

    workflow = _get_search_workflow(context)
    try:
        async with _search_semaphore(context):
            await _delete_message(queued_message)
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
            await _maybe_await(message.reply_text(SEARCH_ERROR_MESSAGE))
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
            query=query,
            result_limit=_result_limit(context),
        )


async def send_search_results(
    context,
    message,
    results: list[SearchResult],
    *,
    query: str,
    result_limit: int = DEFAULT_TELEGRAM_RESULT_LIMIT,
) -> None:
    if not results:
        await _maybe_await(message.reply_text(NO_RESULTS_MESSAGE))
        return

    token = _store_result_page(
        context,
        query=query,
        results=results,
        next_offset=0,
        result_limit=result_limit,
    )
    await _maybe_await(
        message.reply_text(
            format_result_summary(
                len(results),
                result_limit,
                similar_count=sum(len(result.similar_results) for result in results),
            ),
            reply_markup=_results_markup(
                token,
                min(result_limit, len(results)),
                label="Xem kết quả",
            ),
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
        await _maybe_await(callback_query.answer("Kết quả đã hết hạn. Vui lòng tìm lại."))
        return

    await _maybe_await(callback_query.answer("Đang gửi thêm kết quả..."))
    message = getattr(callback_query, "message", None)
    if message is None:
        return

    results = page["results"]
    offset = page["next_offset"]
    limit = page["result_limit"]
    next_results = await _refined_page_results(context, page, offset)
    next_offset = offset + len(next_results)

    await _send_result_batch(message, next_results)

    remaining = len(results) - next_offset
    if remaining > 0:
        page["next_offset"] = next_offset
        _prefetch_page_results(context, page, next_offset)
        await _maybe_await(
            message.reply_text(
                format_more_results_notice(next_offset, len(results)),
                reply_markup=_results_markup(token, min(remaining, limit), label="Xem thêm"),
            )
        )
    else:
        _remove_result_page(context, token)
        await _maybe_await(message.reply_text("✅ Đã gửi hết kết quả."))


def format_search_results(results: list[SearchResult]) -> str:
    if not results:
        return NO_RESULTS_MESSAGE

    formatted: list[str] = []
    for result in results:
        sections: list[str] = []
        if result.image_url:
            sections.append(f"📸 {result.image_url}")
        sections.append(f"🏷️ {result.listing_text}")
        if result.seller:
            sections.append(f"👤 {result.seller}")
        if result.posted_date:
            sections.append(f"📅 {result.posted_date}")
        sections.extend(_format_similar_results(result))
        formatted.append("\n\n".join(sections))

    return "\n\n".join(formatted)


def format_search_result_caption(result: SearchResult) -> str:
    sections = [f"🏷️ {result.listing_text}"]
    if result.seller:
        sections.append(f"👤 {result.seller}")
    if result.posted_date:
        sections.append(f"📅 {format_posted_date(result.posted_date)}")
    sections.extend(_format_similar_results(result))
    return "\n\n".join(sections)


def format_result_summary(
    total_count: int,
    result_limit: int,
    *,
    similar_count: int = 0,
) -> str:
    first_batch_count = min(total_count, result_limit)
    similar_line = (
        f"🔁 Listing tương tự đã gộp: {similar_count}\n"
        if similar_count
        else "🔁 Không có listing tương tự bị gộp\n"
    )
    similar_hint = (
        "🔎 Similar listings sẽ hiện bên trong từng kết quả.\n"
        if similar_count
        else ""
    )
    return (
        "✅ Đã tìm xong\n\n"
        f"📦 Kết quả chính: {total_count}\n"
        f"{similar_line}"
        f"📨 Lượt đầu: {first_batch_count} kết quả\n\n"
        "👇 Bấm “Xem kết quả” để bắt đầu nhận danh sách.\n"
        f"{similar_hint}"
        "💡 Muốn gọn hơn: thêm màu dial, năm, tình trạng hoặc khoảng giá."
    )


def format_more_results_notice(visible_count: int, total_count: int) -> str:
    return (
        f"📊 Đã hiển thị {visible_count}/{total_count} kết quả.\n"
        "Bấm “Xem thêm” để nhận lượt tiếp theo."
    )


def _format_similar_results(result: SearchResult) -> list[str]:
    if not result.similar_results:
        return []

    lines = ["🔁 Similar listings:"]
    for similar in result.similar_results[:5]:
        parts = []
        if similar.seller:
            parts.append(similar.seller)
        if similar.posted_date:
            parts.append(format_posted_date(similar.posted_date))
        if similar.source_url:
            parts.append(similar.source_url)
        lines.append(f"- {' | '.join(parts) if parts else similar.listing_text}")
    if len(result.similar_results) > 5:
        lines.append(f"- +{len(result.similar_results) - 5} more")
    return ["\n".join(lines)]


def format_settings_message(context) -> str:
    allowed_user_ids = _allowed_user_ids(context)
    access_mode = "Chỉ chủ bot" if allowed_user_ids else "Công khai"
    owner_count = len(allowed_user_ids)
    return (
        "⚙️ Cấu hình bot\n\n"
        f"🔐 Quyền truy cập: {access_mode}\n"
        f"👤 ID chủ bot: {owner_count if owner_count else 'Không giới hạn'}\n"
        f"📨 Kết quả mỗi lượt: {_result_limit(context)}\n\n"
        "🔒 Mã bot, cookie và trạng thái trình duyệt không bao giờ hiển thị ở đây."
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
    refiner = _build_result_refiner(settings)
    if refiner is not None:
        application.bot_data[RESULT_REFINER_KEY] = refiner
    application.bot_data[ALLOWED_USER_IDS_KEY] = settings.telegram_allowed_user_ids
    application.bot_data[TELEGRAM_RESULT_LIMIT_KEY] = settings.telegram_result_limit
    application.bot_data[TELEGRAM_MAX_CONCURRENT_SEARCHES_KEY] = (
        settings.telegram_max_concurrent_searches
    )
    application.bot_data[SEARCH_SEMAPHORE_KEY] = asyncio.Semaphore(
        settings.telegram_max_concurrent_searches
    )
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
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
    value = bot_data.get(TELEGRAM_RESULT_LIMIT_KEY, DEFAULT_TELEGRAM_RESULT_LIMIT)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return DEFAULT_TELEGRAM_RESULT_LIMIT


def _max_concurrent_searches(context) -> int:
    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application is not None else {}
    value = bot_data.get(TELEGRAM_MAX_CONCURRENT_SEARCHES_KEY, 1)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def _search_semaphore(context) -> asyncio.Semaphore:
    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application is not None else {}
    semaphore = bot_data.get(SEARCH_SEMAPHORE_KEY)
    if isinstance(semaphore, asyncio.Semaphore):
        return semaphore
    semaphore = asyncio.Semaphore(_max_concurrent_searches(context))
    bot_data[SEARCH_SEMAPHORE_KEY] = semaphore
    return semaphore


def _result_refiner(context) -> RefineResults | None:
    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application is not None else {}
    value = bot_data.get(RESULT_REFINER_KEY)
    return value if callable(value) else None


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


def _query_text_from_message(update, context, message) -> str | None:
    text = getattr(message, "text", None) if message is not None else None
    query = text.strip() if text else ""
    if not _is_group_message(message):
        return query

    mention = _bot_mention(context)
    normalized_query = query.casefold()
    if mention and normalized_query.startswith(mention.casefold()):
        return query[len(mention) :].strip(" \t\n\r:,-")

    if _is_reply_to_bot(context, message):
        return query

    return None


def _is_group_message(message) -> bool:
    chat = getattr(message, "chat", None)
    chat_type = getattr(chat, "type", None) or getattr(message, "chat_type", None)
    return chat_type in {"group", "supergroup"}


def _bot_mention(context) -> str | None:
    bot = _context_bot(context)
    username = getattr(bot, "username", None)
    if not username:
        return None
    return f"@{str(username).lstrip('@')}"


def _is_reply_to_bot(context, message) -> bool:
    reply_to_message = getattr(message, "reply_to_message", None)
    if reply_to_message is None:
        return False

    reply_user = getattr(reply_to_message, "from_user", None)
    if reply_user is None:
        return False

    bot = _context_bot(context)
    bot_id = getattr(bot, "id", None)
    reply_user_id = getattr(reply_user, "id", None)
    if bot_id is not None and reply_user_id == bot_id:
        return True

    bot_username = getattr(bot, "username", None)
    reply_username = getattr(reply_user, "username", None)
    return bool(
        bot_username
        and reply_username
        and str(reply_username).casefold() == str(bot_username).casefold()
    )


def _context_bot(context):
    bot = getattr(context, "bot", None)
    if bot is not None:
        return bot
    application = getattr(context, "application", None)
    return getattr(application, "bot", None) if application is not None else None


async def _reject_unauthorized(update, context, message) -> bool:
    if _is_authorized(update, context):
        return False
    if message is not None:
        await _maybe_await(message.reply_text(UNAUTHORIZED_MESSAGE))
    return True


async def _send_result_batch(message, results: list[SearchResult]) -> None:
    for result in results:
        caption = format_search_result_caption(result)
        if result.image_url:
            try:
                await _maybe_await(
                    message.reply_photo(
                        photo=result.image_url,
                        caption=_limit_telegram_text(
                            caption,
                            TELEGRAM_PHOTO_CAPTION_LIMIT,
                        ),
                    )
                )
                continue
            except Exception as exc:
                logger.info(
                    "event=telegram.photo_fallback error_type=%s",
                    exc.__class__.__name__,
                )

        await _maybe_await(
            message.reply_text(
                _limit_telegram_text(caption, TELEGRAM_TEXT_MESSAGE_LIMIT)
            )
        )


def _limit_telegram_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    if limit <= 1:
        return value[:limit]
    return f"{value[: limit - 1].rstrip()}…"


def _store_result_page(
    context,
    *,
    query: str,
    results: list[SearchResult],
    next_offset: int,
    result_limit: int,
) -> str:
    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application is not None else {}
    pages = bot_data.setdefault(RESULT_PAGES_KEY, {})
    token = secrets.token_urlsafe(8)
    pages[token] = {
        "query": query,
        "results": results,
        "next_offset": next_offset,
        "result_limit": result_limit,
        "refined_results": {},
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
    page = pages.pop(token, None)
    _cancel_prefetch_task(page)


def _clear_result_pages(context) -> int:
    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application is not None else {}
    pages = bot_data.get(RESULT_PAGES_KEY, {})
    for page in pages.values():
        _cancel_prefetch_task(page)
    cleared_count = len(pages)
    pages.clear()
    return cleared_count


async def _refined_page_results(context, page, offset: int) -> list[SearchResult]:
    task = page.get("prefetch_task")
    if task is not None and page.get("prefetch_offset") == offset:
        try:
            await task
        except Exception as exc:
            logger.info("event=telegram.prefetch_failed error_type=%s", exc.__class__.__name__)
        finally:
            page.pop("prefetch_task", None)
            page.pop("prefetch_offset", None)

    result_limit = int(page["result_limit"])
    raw_results = page["results"][offset : offset + result_limit]
    refined_results = page.setdefault("refined_results", {})
    missing = [
        (offset + index, result)
        for index, result in enumerate(raw_results)
        if offset + index not in refined_results
    ]
    if missing:
        await _refine_and_store_page_results(context, page, offset)

    return [
        refined_results.get(offset + index, result)
        for index, result in enumerate(raw_results)
    ]


def _prefetch_page_results(context, page, offset: int) -> None:
    if _result_refiner(context) is None:
        return
    if page.get("prefetch_task") is not None:
        return
    page["prefetch_offset"] = offset
    page["prefetch_task"] = asyncio.create_task(
        _refine_and_store_page_results(context, page, offset)
    )


async def _refine_and_store_page_results(context, page, offset: int) -> None:
    refiner = _result_refiner(context)
    if refiner is None:
        return

    result_limit = int(page["result_limit"])
    raw_results = page["results"][offset : offset + result_limit]
    if not raw_results:
        return

    try:
        refined = await refiner(str(page["query"]), raw_results)
    except Exception as exc:
        logger.info("event=telegram.refine_fallback error_type=%s", exc.__class__.__name__)
        refined = raw_results

    refined_results = page.setdefault("refined_results", {})
    for index, result in enumerate(refined[: len(raw_results)]):
        refined_results[offset + index] = result


def _cancel_prefetch_task(page) -> None:
    if not page:
        return
    task = page.get("prefetch_task")
    if task is not None and not task.done():
        task.cancel()


def _build_result_refiner(settings: Settings) -> RefineResults | None:
    if not settings.local_llm_enabled:
        return None

    from app.db import Database
    from app.llm_matcher import refine_search_results

    database = Database(settings.db_path)

    async def refine(query: str, results: list[SearchResult]) -> list[SearchResult]:
        return await refine_search_results(query, results, settings, database=database)

    return refine


def _results_markup(token: str, count: int, *, label: str):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"{label} {count}",
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
