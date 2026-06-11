from __future__ import annotations

import asyncio
import hashlib
import json
import inspect
import logging
import secrets
import time
import urllib.parse
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol

from app.config import DEFAULT_TELEGRAM_RESULT_LIMIT, DEFAULT_WATCHFACTS_URL, Settings
from app.db import (
    AIRefinementSuggestionRecord,
    Database,
    IssueRecord,
    SuspiciousIssueSummary,
)
from app.openwa_handoff import (
    OpenWAChatDraftResponse,
    OpenWAHandoffConfig,
    OpenWAHandoffConfigError,
    OpenWAHandoffResponseError,
    create_openwa_chat_draft,
)
from app.result_pages import ResultPageConfig, generate_result_page
from app.scraper import BrowserSessionError, BrowserSessionStatus
from app.search_result import SearchResult


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
    "⚙️ /settings để xem cấu hình bot hiện tại.\n"
    "🩺 /health để kiểm tra session WatchFacts.\n"
    "🧾 /issues để xem user feedback đang mở.\n"
    "🧪 /suspicious để xem auto QA flags severity cao.\n"
    "🤖 /ai_suggestions để review gợi ý OpenAI."
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
WATCHFACTS_SESSION_ERROR_MESSAGE = (
    "🔐 WatchFacts cần đăng nhập lại\n\n"
    "Session WatchFacts của bot đã hết hạn hoặc không còn hợp lệ.\n"
    "Mình đã báo cho owner để refresh session.\n\n"
    "⏳ Vui lòng thử lại sau khi owner cập nhật đăng nhập."
)
WATCHFACTS_OWNER_ALERT_MESSAGE = (
    "🚨 WatchFacts session cần xử lý\n\n"
    "Bot không còn truy cập được WatchFacts bằng session đã lưu.\n\n"
    "📌 Việc cần làm:\n"
    "1. Đăng nhập lại WatchFacts để tạo session mới.\n"
    "2. Cập nhật `data/watchfacts_state.json` trên server nếu login ở máy khác.\n"
    "3. Restart hoặc deploy lại bot.\n\n"
    "🔒 Bot không lưu mật khẩu WatchFacts và không tự đăng nhập lại."
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
HYBRID_AI_MODE_KEY = "hybrid_ai_mode"
OPENAI_MODEL_KEY = "openai_model"
WATCHFACTS_URL_KEY = "watchfacts_url"
RESULT_PAGES_KEY = "result_pages"
RESULT_PAGE_CONFIG_KEY = "result_page_config"
RESULT_REFINER_KEY = "result_refiner"
ISSUE_DATABASE_KEY = "issue_database"
FEEDBACK_CONTEXTS_KEY = "feedback_contexts"
OPENWA_HANDOFF_CONFIG_KEY = "openwa_handoff_config"
OPENWA_CHAT_DRAFT_CLIENT_KEY = "openwa_chat_draft_client"
WATCHFACTS_SESSION_CHECKER_KEY = "watchfacts_session_checker"
WATCHFACTS_SESSION_ALERT_LAST_SENT_KEY = "watchfacts_session_alert_last_sent"
SEARCH_SEMAPHORE_KEY = "search_semaphore"
MORE_RESULTS_PREFIX = "more_results:"
FEEDBACK_PREFIX = "feedback:"
OPENWA_CHAT_PREFIX = "openwa_chat:"
ALLOWED_USER_IDS_KEY = "allowed_user_ids"
TELEGRAM_PHOTO_CAPTION_LIMIT = 1024
TELEGRAM_TEXT_MESSAGE_LIMIT = 4096
WATCHFACTS_SESSION_ALERT_COOLDOWN_SECONDS = 30 * 60
MAX_FEEDBACK_CONTEXTS = 500
ISSUES_EXPORT_LIMIT = 30
OPENWA_MAX_SOURCE_URL_LENGTH = 2048
OPENWA_MAX_QUERY_TEXT_LENGTH = 500
OPENWA_MAX_SELLER_NAME_LENGTH = 255
OPENWA_MAX_PRODUCT_TITLE_LENGTH = 255


class SearchWorkflow(Protocol):
    async def search(self, query: str) -> list[SearchResult]:
        ...


RefineResults = Callable[..., Awaitable[list[SearchResult]]]
SessionChecker = Callable[[], Awaitable[BrowserSessionStatus]]
OpenWAChatDraftClient = Callable[[dict], Awaitable[OpenWAChatDraftResponse]]


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


async def health_command(update, context) -> None:
    message = getattr(update, "message", None)
    if await _reject_unauthorized(update, context, message):
        return
    if message is None:
        return

    checker = _watchfacts_session_checker(context)
    if checker is None:
        await _maybe_await(message.reply_text(format_health_message(None)))
        return

    await _send_typing_action(message, context)
    status = await checker()
    await _maybe_await(message.reply_text(format_health_message(status)))


async def issues_command(update, context) -> None:
    message = getattr(update, "message", None)
    if await _reject_unauthorized(update, context, message):
        return
    if message is None:
        return

    database = _issue_database(context)
    issues = database.list_open_feedback_issues(limit=10)
    await _maybe_await(message.reply_text(format_issues_message(issues)))


async def suspicious_command(update, context) -> None:
    message = getattr(update, "message", None)
    if await _reject_unauthorized(update, context, message):
        return
    if message is None:
        return

    min_severity = _suspicious_min_severity_arg(context)
    issues = _issue_database(context).list_open_suspicious_issues(
        limit=10,
        min_severity=min_severity,
    )
    await _maybe_await(
        message.reply_text(format_suspicious_issues_message(issues, min_severity))
    )


async def suspicious_summary_command(update, context) -> None:
    message = getattr(update, "message", None)
    if await _reject_unauthorized(update, context, message):
        return
    if message is None:
        return

    summary = _issue_database(context).summarize_open_suspicious_issues(limit=20)
    await _maybe_await(message.reply_text(format_suspicious_summary_message(summary)))


async def issue_command(update, context) -> None:
    message = getattr(update, "message", None)
    if await _reject_unauthorized(update, context, message):
        return
    if message is None:
        return

    issue_ref = _first_issue_arg(context)
    if issue_ref is None:
        await _maybe_await(
            message.reply_text(
                "🧾 Xem issue\n\n"
                "Vui lòng dùng dạng `/issue F1` hoặc `/issue S1`."
            )
        )
        return

    issue_type, issue_id = issue_ref
    issue = _issue_database(context).get_issue(issue_id, issue_type=issue_type)
    await _maybe_await(message.reply_text(format_issue_detail(issue)))


async def issues_export_command(update, context) -> None:
    message = getattr(update, "message", None)
    if await _reject_unauthorized(update, context, message):
        return
    if message is None:
        return

    payload = _issue_database(context).export_open_issues(limit=ISSUES_EXPORT_LIMIT)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    await _maybe_await(
        message.reply_text(
            _limit_telegram_text(
                "📤 Export issue regression\n\n"
                f"```json\n{text}\n```",
                TELEGRAM_TEXT_MESSAGE_LIMIT,
            )
        )
    )


async def suspicious_export_command(update, context) -> None:
    message = getattr(update, "message", None)
    if await _reject_unauthorized(update, context, message):
        return
    if message is None:
        return

    min_severity = _suspicious_min_severity_arg(context)
    payload = _issue_database(context).export_open_suspicious_issues(
        limit=ISSUES_EXPORT_LIMIT,
        min_severity=min_severity,
    )
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    await _maybe_await(
        message.reply_text(
            _limit_telegram_text(
                "📤 Export suspicious regression\n\n"
                f"```json\n{text}\n```",
                TELEGRAM_TEXT_MESSAGE_LIMIT,
            )
        )
    )


async def ai_suggestions_command(update, context) -> None:
    message = getattr(update, "message", None)
    if await _reject_unauthorized(update, context, message):
        return
    if message is None:
        return

    suggestions = _issue_database(context).list_ai_refinement_suggestions(
        limit=10,
        review_status="open",
    )
    await _maybe_await(message.reply_text(format_ai_suggestions_message(suggestions)))


async def ai_suggestion_command(update, context) -> None:
    message = getattr(update, "message", None)
    if await _reject_unauthorized(update, context, message):
        return
    if message is None:
        return

    suggestion_id = _first_int_arg(context)
    if suggestion_id is None:
        await _maybe_await(
            message.reply_text(
                "🤖 Xem gợi ý AI\n\n"
                "Vui lòng dùng dạng `/ai_suggestion 1`."
            )
        )
        return

    suggestion = _issue_database(context).get_ai_refinement_suggestion(suggestion_id)
    await _maybe_await(message.reply_text(format_ai_suggestion_detail(suggestion)))


async def ai_accept_command(update, context) -> None:
    await _mark_ai_suggestion_command(update, context, status="accepted")


async def ai_ignore_command(update, context) -> None:
    await _mark_ai_suggestion_command(update, context, status="ignored")


async def ai_suggestions_export_command(update, context) -> None:
    message = getattr(update, "message", None)
    if await _reject_unauthorized(update, context, message):
        return
    if message is None:
        return

    payload = _issue_database(context).export_reviewed_ai_suggestions(
        status="accepted",
        limit=ISSUES_EXPORT_LIMIT,
    )
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    await _maybe_await(
        message.reply_text(
            _limit_telegram_text(
                "📤 Export AI regression\n\n"
                f"```json\n{text}\n```",
                TELEGRAM_TEXT_MESSAGE_LIMIT,
            )
        )
    )


async def issue_done_command(update, context) -> None:
    await _mark_issue_command(update, context, status="fixed")


async def issue_ignore_command(update, context) -> None:
    await _mark_issue_command(update, context, status="ignored")


async def error_handler(update, context) -> None:
    error = getattr(context, "error", None)
    error_type = error.__class__.__name__ if error is not None else "Unknown"
    logger.info("event=telegram.error_handler error_type=%s", error_type)


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
    except BrowserSessionError as exc:
        await _delete_processing_message(
            processing_message,
            started_at=processing_started_at,
            min_seconds=_processing_min_seconds(context),
        )
        logger.error(
            "event=telegram.watchfacts_session_error error_type=%s query_length=%d",
            exc.__class__.__name__,
            len(query),
        )
        await _notify_watchfacts_session_owner(context)
        if message is not None:
            await _maybe_await(message.reply_text(WATCHFACTS_SESSION_ERROR_MESSAGE))
        return
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

    result_page = _generate_result_page(
        context,
        query=query,
        results=results,
    )
    result_page_url = result_page.url if result_page is not None else None
    token = ""
    if result_page_url is None:
        token = _store_result_page(
            context,
            query=query,
            results=results,
            next_offset=0,
            result_limit=result_limit,
        )
        page = _get_result_page(context, token)
        if page is not None:
            _prefetch_page_results(context, page, 0)
    await _maybe_await(
        message.reply_text(
            format_result_summary(
                len(results),
                result_limit,
                similar_count=sum(len(result.similar_results) for result in results),
                result_page_available=result_page_url is not None,
            ),
            reply_markup=_results_markup(
                token,
                min(result_limit, len(results)),
                label="Xem kết quả",
                result_page_url=result_page_url,
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

    await _send_result_batch(
        context,
        message,
        next_results,
        query=str(page["query"]),
        start_rank=offset + 1,
    )

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


async def handle_feedback(update, context) -> None:
    callback_query = getattr(update, "callback_query", None)
    if callback_query is None:
        return
    if not _is_authorized(update, context):
        await _maybe_await(callback_query.answer(UNAUTHORIZED_MESSAGE))
        return

    data = getattr(callback_query, "data", "") or ""
    try:
        token, reason = data.removeprefix(FEEDBACK_PREFIX).split(":", maxsplit=1)
    except ValueError:
        await _maybe_await(callback_query.answer("Feedback không hợp lệ."))
        return

    feedback_context = _get_feedback_context(context, token)
    if feedback_context is None:
        await _maybe_await(callback_query.answer("Feedback đã hết hạn. Vui lòng tìm lại."))
        return

    result = feedback_context["result"]
    try:
        _issue_database(context).record_result_feedback(
            query_text=str(feedback_context["query"]),
            result_rank=int(feedback_context["rank"]),
            reason=reason,
            listing_text=result.listing_text,
            raw_listing_text=result.raw_listing_text,
            seller=result.seller,
            posted_date=result.posted_date,
            source_url=result.source_url,
            telegram_user_id=_telegram_user_id(update),
        )
    except Exception as exc:
        logger.info(
            "event=telegram.feedback_failed error_type=%s",
            exc.__class__.__name__,
        )
        await _maybe_await(callback_query.answer("Chưa lưu được feedback. Thử lại sau."))
        return

    await _maybe_await(
        callback_query.answer(
            "📝 Đã ghi nhận. Mình đã lưu case này để owner review sau."
        )
    )


async def handle_openwa_chat_draft(update, context) -> None:
    callback_query = getattr(update, "callback_query", None)
    if callback_query is None:
        return
    if not _is_authorized(update, context):
        await _maybe_await(callback_query.answer(UNAUTHORIZED_MESSAGE))
        return

    data = getattr(callback_query, "data", "") or ""
    token = data.removeprefix(OPENWA_CHAT_PREFIX)
    draft_context = _get_feedback_context(context, token)
    if not token or draft_context is None:
        await _maybe_await(callback_query.answer("Chat draft đã hết hạn. Vui lòng tìm lại."))
        return

    config = _openwa_handoff_config(context)
    if config is None or not config.is_ready:
        await _maybe_await(callback_query.answer("OpenWA chat draft chưa được cấu hình."))
        await _reply_openwa_chat_draft_error(
            callback_query,
            "OpenWA chat draft chưa được cấu hình.",
        )
        return

    result = draft_context["result"]
    payload = build_openwa_chat_draft_payload(
        update,
        query=str(draft_context["query"]),
        rank=int(draft_context["rank"]),
        result=result,
        watchfacts_url=_watchfacts_url(context),
    )
    try:
        await _maybe_await(callback_query.answer("Đang tạo chat draft trong OpenWA..."))
        response = await _openwa_chat_draft_client(context)(payload)
    except OpenWAHandoffConfigError:
        await _reply_openwa_chat_draft_error(
            callback_query,
            "OpenWA chat draft chưa được cấu hình.",
        )
        return
    except OpenWAHandoffResponseError as exc:
        logger.info(
            "event=telegram.openwa_chat_draft_failed error_type=%s error=%s",
            exc.__class__.__name__,
            str(exc)[:500],
        )
        await _reply_openwa_chat_draft_error(
            callback_query,
            "OpenWA chưa trả về chat draft hợp lệ.",
        )
        return
    except Exception as exc:
        logger.info(
            "event=telegram.openwa_chat_draft_failed error_type=%s",
            exc.__class__.__name__,
        )
        await _reply_openwa_chat_draft_error(callback_query, "Chưa kết nối được OpenWA. Thử lại sau.")
        return

    await _reply_openwa_chat_draft_success(callback_query, response.dashboard_url)


def build_openwa_chat_draft_payload(
    update,
    *,
    query: str,
    rank: int,
    result: SearchResult,
    watchfacts_url: str | None = None,
) -> dict:
    return {
        "source": "watchfacts",
        "sourceResultId": _source_result_id(query, rank, result),
        "sourceUrl": _openwa_url(result.source_url, watchfacts_url),
        "queryText": _openwa_text(query, max_length=OPENWA_MAX_QUERY_TEXT_LENGTH),
        "listingText": result.listing_text,
        "rawListingText": result.raw_listing_text,
        "seller": {
            "name": _openwa_text(result.seller, max_length=OPENWA_MAX_SELLER_NAME_LENGTH),
            "phone": _openwa_phone(result.seller_phone),
            "watchfactsId": None,
            "profileUrl": None,
        },
        "product": {
            "title": _openwa_text(result.listing_text, max_length=OPENWA_MAX_PRODUCT_TITLE_LENGTH),
            "reference": None,
            "brand": None,
            "year": None,
            "condition": None,
            "set": None,
            "dial": None,
            "priceText": None,
            "imageUrl": _openwa_url(result.image_url, watchfacts_url),
        },
        "origin": {
            "telegramUserId": _telegram_user_id(update),
            "telegramUsername": _telegram_username(update),
            "telegramChatId": _telegram_chat_id(update),
            "telegramMessageId": _telegram_message_id(update),
        },
    }


def _openwa_text(value: str | None, *, max_length: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized[:max_length]


def _openwa_url(value: str | None, watchfacts_url: str | None) -> str | None:
    raw_value = _openwa_text(value, max_length=OPENWA_MAX_SOURCE_URL_LENGTH)
    if raw_value is None:
        return None

    candidate = raw_value
    parsed = urllib.parse.urlparse(candidate)
    if not (parsed.scheme in {"http", "https"} and parsed.netloc):
        base_url = (watchfacts_url or DEFAULT_WATCHFACTS_URL).strip()
        candidate = urllib.parse.urljoin(base_url, candidate)

    parsed_candidate = urllib.parse.urlparse(candidate)
    if parsed_candidate.scheme not in {"http", "https"} or not parsed_candidate.netloc:
        return None
    return candidate[:OPENWA_MAX_SOURCE_URL_LENGTH]


def _openwa_phone(value: str | None) -> str | None:
    if value is None:
        return None
    digits = "".join(character for character in value if character.isdigit())
    if len(digits) < 8 or len(digits) > 15 or digits.startswith("0"):
        return None
    return digits


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
    result_page_available: bool = False,
) -> str:
    first_batch_count = min(total_count, result_limit)
    similar_line = (
        f"🔁 Listing tương tự đã gộp: {similar_count}\n"
        if similar_count
        else "🔁 Không có listing tương tự bị gộp\n"
    )
    similar_hint = ""
    if similar_count:
        similar_hint = (
            "🔎 Listing tương tự sẽ hiện bên trong trang kết quả.\n"
            if result_page_available
            else "🔎 Similar listings sẽ hiện bên trong từng kết quả.\n"
        )
    if result_page_available:
        return (
            "✅ Đã tìm xong\n\n"
            f"📦 Kết quả chính: {total_count}\n"
            f"{similar_line}\n"
            "🔗 Bấm “Mở trang kết quả” để xem dạng card, lọc và copy.\n"
            f"{similar_hint}"
            "💡 Muốn gọn hơn: thêm màu dial, năm, tình trạng hoặc khoảng giá."
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
        f"📨 Kết quả mỗi lượt: {_result_limit(context)}\n"
        f"🤖 AI mode: {_hybrid_ai_mode(context)}\n"
        f"🧠 OpenAI model: {_openai_model(context)}\n"
        f"💬 OpenWA chat draft: {_openwa_handoff_status(context)}\n\n"
        "🔒 Mã bot, cookie và trạng thái trình duyệt không bao giờ hiển thị ở đây."
    )


def format_issues_message(issues: list[IssueRecord]) -> str:
    if not issues:
        return (
            "🧾 User feedback cần xử lý\n\n"
            "✅ Chưa có user feedback mở."
        )

    lines = ["🧾 User feedback cần xử lý", ""]
    for issue in issues:
        icon = _issue_icon(issue.reason, issue.issue_type)
        lines.extend(
            [
                f"#{_issue_key(issue)} {icon} {_issue_reason_label(issue.reason)}",
                f"🔎 Query: {issue.query_text}",
                f"🏷️ Bot gửi: {_limit_inline(issue.listing_text, 120)}",
            ]
        )
        if issue.seller:
            lines.append(f"👤 Seller: {issue.seller}")
        if issue.source_url:
            lines.append(f"🔗 Source: {issue.source_url}")
        if issue.severity is not None:
            lines.append(f"🧪 Severity: {issue.severity}")
        else:
            lines.append(f"📊 Report: {issue.report_count} lượt")
        lines.append("")
    lines.append("Dùng `/issue F1` để xem chi tiết; `/issues_export` để export.")
    lines.append("Auto QA flags nằm ở `/suspicious`.")
    return "\n".join(lines).strip()


def format_suspicious_issues_message(
    issues: list[IssueRecord],
    min_severity: int | None,
) -> str:
    scope = "all severity" if min_severity is None else f"severity ≥ {min_severity}"
    if not issues:
        return (
            "🧪 Auto suspicious cần review\n\n"
            f"✅ Không có auto QA flag mở cho {scope}."
        )

    lines = ["🧪 Auto suspicious cần review", f"Scope: {scope}", ""]
    for issue in issues:
        lines.extend(
            [
                f"#{_issue_key(issue)} {_issue_icon(issue.reason, issue.issue_type)} "
                f"{_issue_reason_label(issue.reason)}",
                f"🔎 Query: {issue.query_text}",
                f"📍 Rank: {issue.result_rank}",
                f"🧪 Severity: {issue.severity}",
                f"🏷️ Bot gửi: {_limit_inline(issue.listing_text, 120)}",
            ]
        )
        if issue.source_url:
            lines.append(f"🔗 Source: {issue.source_url}")
        lines.append("")
    lines.append("Dùng `/issue S1` để xem chi tiết; `/suspicious_summary` để xem breakdown.")
    lines.append("Mặc định `/suspicious` chỉ hiện severity cao; dùng `/suspicious all` để xem toàn bộ.")
    return "\n".join(lines).strip()


def format_suspicious_summary_message(summary: list[SuspiciousIssueSummary]) -> str:
    if not summary:
        return (
            "🧪 Auto suspicious summary\n\n"
            "✅ Không có auto QA flag mở."
        )

    lines = ["🧪 Auto suspicious summary", ""]
    for item in summary:
        lines.extend(
            [
                f"Severity {item.severity} · {_issue_reason_label(item.reason)}",
                f"📊 {item.issue_count} flags · {item.query_count} queries",
                f"🔎 Sample: {item.sample_query}",
                f"🧾 Latest: /issue S{item.latest_issue_id}",
                "",
            ]
        )
    lines.append("Review trước severity 3; dùng `/suspicious 3` hoặc `/suspicious_export 3`.")
    return "\n".join(lines).strip()


def format_issue_detail(issue: IssueRecord | None) -> str:
    if issue is None:
        return (
            "🧾 Issue không tồn tại\n\n"
            "Kiểm tra lại ID bằng `/issues`."
        )

    sections = [
        f"🧾 Issue #{_issue_key(issue)}",
        "",
        f"{_issue_icon(issue.reason, issue.issue_type)} Loại: {_issue_reason_label(issue.reason)}",
        f"📌 Trạng thái: {issue.issue_status}",
        f"🔎 Query: {issue.query_text}",
        f"📍 Rank: {issue.result_rank}",
        "",
        f"🏷️ Bot gửi:\n{issue.listing_text}",
    ]
    if issue.raw_listing_text:
        sections.extend(["", f"🧾 Raw candidate:\n{issue.raw_listing_text}"])
    if issue.seller:
        sections.append(f"👤 Seller: {issue.seller}")
    if issue.posted_date:
        sections.append(f"📅 Date: {issue.posted_date}")
    if issue.source_url:
        sections.append(f"🔗 Source: {issue.source_url}")
    if issue.severity is not None:
        sections.append(f"🧪 Severity: {issue.severity}")
    else:
        sections.append(f"📊 Report: {issue.report_count} lượt")
    if issue.issue_status == "open":
        sections.append("")
        sections.append(f"✅ Xong: /issue_done {_issue_key(issue)}")
        sections.append(f"🙈 Bỏ qua: /issue_ignore {_issue_key(issue)}")
    sections.append("")
    sections.append("🔒 Không hiển thị cookie, token hoặc browser state.")
    return _limit_telegram_text("\n".join(sections), TELEGRAM_TEXT_MESSAGE_LIMIT)


def format_issue_status_update(issue: IssueRecord | None, status: str) -> str:
    if issue is None:
        return (
            "🧾 Issue không tồn tại\n\n"
            "Kiểm tra lại ID bằng `/issues`."
        )

    label = "đã xử lý" if status == "fixed" else "đã bỏ qua"
    return (
        f"✅ Issue #{_issue_key(issue)} {label}.\n\n"
        f"📌 Trạng thái: {issue.issue_status}\n"
        f"🔎 Query: {issue.query_text}\n"
        f"🏷️ Bot gửi: {_limit_inline(issue.listing_text, 160)}"
    )


def format_ai_suggestions_message(
    suggestions: list[AIRefinementSuggestionRecord],
) -> str:
    if not suggestions:
        return (
            "🤖 Gợi ý AI cần review\n\n"
            "✅ Chưa có gợi ý OpenAI đang chờ duyệt."
        )

    lines = ["🤖 Gợi ý AI cần review", ""]
    for suggestion in suggestions:
        lines.extend(
            [
                f"#A{suggestion.id} {_ai_gate_icon(suggestion.gate_status)} {suggestion.gate_status}",
                f"🔎 Query: {suggestion.query_text}",
                f"🏷️ Bot gửi: {_limit_inline(suggestion.deterministic_text, 110)}",
                f"✨ AI đề xuất: {_limit_inline(suggestion.suggested_text, 110)}",
            ]
        )
        if suggestion.issue_type and suggestion.issue_id:
            lines.append(f"🔗 Issue: {_issue_ref_label(suggestion.issue_type, suggestion.issue_id)}")
        lines.append("")
    lines.append(
        "Dùng `/ai_suggestion 1` để xem chi tiết; "
        "`/ai_accept 1` để duyệt; `/ai_ignore 1` để bỏ qua."
    )
    return "\n".join(lines).strip()


def format_ai_suggestion_detail(
    suggestion: AIRefinementSuggestionRecord | None,
) -> str:
    if suggestion is None:
        return (
            "🤖 Gợi ý AI không tồn tại\n\n"
            "Kiểm tra lại ID bằng `/ai_suggestions`."
        )

    sections = [
        f"🤖 Gợi ý AI #A{suggestion.id}",
        "",
        f"📌 Trạng thái review: {suggestion.review_status}",
        f"🧪 Gate: {suggestion.gate_status}",
        f"🔎 Query: {suggestion.query_text}",
        f"📍 Rank: {suggestion.result_rank}",
        f"🧠 Model: {suggestion.model}",
        f"🧾 Prompt: {suggestion.prompt_version}",
        "",
        f"🏷️ Bot gửi:\n{suggestion.deterministic_text}",
        "",
        f"✨ AI đề xuất:\n{suggestion.suggested_text}",
    ]
    if suggestion.raw_listing_text:
        sections.extend(["", f"🧾 Raw candidate:\n{suggestion.raw_listing_text}"])
    if suggestion.source_url:
        sections.append(f"🔗 Source: {suggestion.source_url}")
    if suggestion.issue_type and suggestion.issue_id:
        sections.append(f"🔗 Issue: {_issue_ref_label(suggestion.issue_type, suggestion.issue_id)}")
    if suggestion.gate_reasons:
        sections.append(f"🧪 Reasons: {', '.join(suggestion.gate_reasons)}")
    if suggestion.review_notes:
        sections.append(f"📝 Notes: {suggestion.review_notes}")
    if suggestion.review_status == "open":
        sections.append("")
        sections.append(f"✅ Duyệt: /ai_accept {suggestion.id}")
        sections.append(f"🙈 Bỏ qua: /ai_ignore {suggestion.id}")
    sections.append("")
    sections.append("🔒 Không hiển thị cookie, token hoặc browser state.")
    return _limit_telegram_text("\n".join(sections), TELEGRAM_TEXT_MESSAGE_LIMIT)


def format_ai_suggestion_status_update(
    suggestion: AIRefinementSuggestionRecord | None,
    status: str,
) -> str:
    if suggestion is None:
        return (
            "🤖 Gợi ý AI không tồn tại\n\n"
            "Kiểm tra lại ID bằng `/ai_suggestions`."
        )

    label = "đã duyệt" if status == "accepted" else "đã bỏ qua"
    return (
        f"✅ Gợi ý AI #A{suggestion.id} {label}.\n\n"
        f"📌 Trạng thái: {suggestion.review_status}\n"
        f"🔎 Query: {suggestion.query_text}\n"
        f"✨ AI đề xuất: {_limit_inline(suggestion.suggested_text, 160)}"
    )


def format_health_message(status: BrowserSessionStatus | None) -> str:
    if status is None:
        return (
            "🩺 Kiểm tra hệ thống\n\n"
            "⚪ WatchFacts session: chưa cấu hình checker\n"
            "📨 Bot Telegram: đang phản hồi\n\n"
            "🔒 Không hiển thị cookie, token hoặc browser state."
        )

    if status.ok:
        session_line = "🟢 WatchFacts session: hợp lệ"
        action_line = "✅ Bot có thể dùng session hiện tại để quét WatchFacts."
    elif status.status == "missing":
        session_line = "🔴 WatchFacts session: chưa có file đăng nhập"
        action_line = "📌 Chạy `python scripts/ops/login.py` rồi cập nhật server."
    elif status.status == "expired":
        session_line = "🟠 WatchFacts session: đã hết hạn"
        action_line = "📌 Đăng nhập lại WatchFacts để tạo session mới."
    elif status.status == "http_error":
        session_line = "🟠 WatchFacts session: WatchFacts trả lỗi HTTP"
        action_line = "📌 Kiểm tra WatchFacts hoặc thử lại sau."
    else:
        session_line = "🟠 WatchFacts session: chưa kiểm tra được"
        action_line = "📌 Kiểm tra mạng/server hoặc thử lại sau."

    return (
        "🩺 Kiểm tra hệ thống\n\n"
        f"{session_line}\n"
        "📨 Bot Telegram: đang phản hồi\n\n"
        f"{action_line}\n\n"
        "🔒 Không hiển thị cookie, token hoặc browser state."
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
    application.bot_data[WATCHFACTS_SESSION_CHECKER_KEY] = _build_session_checker(settings)
    application.bot_data[ISSUE_DATABASE_KEY] = Database(settings.db_path)
    application.bot_data[ALLOWED_USER_IDS_KEY] = settings.telegram_allowed_user_ids
    application.bot_data[TELEGRAM_RESULT_LIMIT_KEY] = settings.telegram_result_limit
    application.bot_data[TELEGRAM_MAX_CONCURRENT_SEARCHES_KEY] = (
        settings.telegram_max_concurrent_searches
    )
    application.bot_data[HYBRID_AI_MODE_KEY] = settings.hybrid_ai_mode
    application.bot_data[OPENAI_MODEL_KEY] = settings.openai_model
    application.bot_data[WATCHFACTS_URL_KEY] = settings.watchfacts_url
    application.bot_data[RESULT_PAGE_CONFIG_KEY] = ResultPageConfig.from_settings(settings)
    openwa_config = OpenWAHandoffConfig.from_settings(settings)
    application.bot_data[OPENWA_HANDOFF_CONFIG_KEY] = openwa_config
    if openwa_config.is_ready:
        application.bot_data[OPENWA_CHAT_DRAFT_CLIENT_KEY] = (
            lambda payload: create_openwa_chat_draft(openwa_config, payload)
        )
    application.bot_data[SEARCH_SEMAPHORE_KEY] = asyncio.Semaphore(
        settings.telegram_max_concurrent_searches
    )
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("health", health_command))
    application.add_handler(CommandHandler("issues", issues_command))
    application.add_handler(CommandHandler("suspicious", suspicious_command))
    application.add_handler(CommandHandler("suspicious_summary", suspicious_summary_command))
    application.add_handler(CommandHandler("issue", issue_command))
    application.add_handler(CommandHandler("issues_export", issues_export_command))
    application.add_handler(CommandHandler("suspicious_export", suspicious_export_command))
    application.add_handler(CommandHandler("ai_suggestions", ai_suggestions_command))
    application.add_handler(CommandHandler("ai_suggestion", ai_suggestion_command))
    application.add_handler(CommandHandler("ai_accept", ai_accept_command))
    application.add_handler(CommandHandler("ai_ignore", ai_ignore_command))
    application.add_handler(
        CommandHandler("ai_suggestions_export", ai_suggestions_export_command)
    )
    application.add_handler(CommandHandler("issue_done", issue_done_command))
    application.add_handler(CommandHandler("issue_ignore", issue_ignore_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CallbackQueryHandler(handle_more_results, pattern=f"^{MORE_RESULTS_PREFIX}"))
    application.add_handler(CallbackQueryHandler(handle_feedback, pattern=f"^{FEEDBACK_PREFIX}"))
    application.add_handler(CallbackQueryHandler(handle_openwa_chat_draft, pattern=f"^{OPENWA_CHAT_PREFIX}"))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message)
    )
    application.add_error_handler(error_handler)
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


def _issue_database(context) -> Database:
    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application is not None else {}
    database = bot_data.get(ISSUE_DATABASE_KEY)
    if isinstance(database, Database):
        return database
    raise RuntimeError("Issue database is not configured")


def _watchfacts_session_checker(context) -> SessionChecker | None:
    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application is not None else {}
    value = bot_data.get(WATCHFACTS_SESSION_CHECKER_KEY)
    return value if callable(value) else None


def _openwa_handoff_config(context) -> OpenWAHandoffConfig | None:
    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application is not None else {}
    value = bot_data.get(OPENWA_HANDOFF_CONFIG_KEY)
    return value if isinstance(value, OpenWAHandoffConfig) else None


def _openwa_chat_draft_client(context) -> OpenWAChatDraftClient:
    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application is not None else {}
    value = bot_data.get(OPENWA_CHAT_DRAFT_CLIENT_KEY)
    if callable(value):
        return value

    config = _openwa_handoff_config(context)
    if config is None:
        raise OpenWAHandoffConfigError("OpenWA chat draft handoff is not configured")

    async def create(payload: dict) -> OpenWAChatDraftResponse:
        return await create_openwa_chat_draft(config, payload)

    return create


def _watchfacts_url(context) -> str:
    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application is not None else {}
    value = bot_data.get(WATCHFACTS_URL_KEY)
    return str(value).strip() if value else DEFAULT_WATCHFACTS_URL


def _result_page_config(context) -> ResultPageConfig | None:
    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application is not None else {}
    value = bot_data.get(RESULT_PAGE_CONFIG_KEY)
    return value if isinstance(value, ResultPageConfig) else None


def _openwa_handoff_ready(context) -> bool:
    config = _openwa_handoff_config(context)
    return bool(config and config.is_ready)


def _allowed_user_ids(context) -> tuple[int, ...]:
    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application is not None else {}
    value = bot_data.get(ALLOWED_USER_IDS_KEY, ())
    if value is None:
        return ()
    return tuple(int(user_id) for user_id in value)


def _first_issue_arg(context) -> tuple[str | None, int] | None:
    args = getattr(context, "args", None) or []
    if not args:
        return None
    raw = str(args[0]).strip().lstrip("#")
    issue_type: str | None = None
    if raw[:1].casefold() == "f":
        issue_type = "feedback"
        raw = raw[1:]
    elif raw[:1].casefold() == "s":
        issue_type = "suspicious"
        raw = raw[1:]
    try:
        return issue_type, int(raw)
    except ValueError:
        return None


def _suspicious_min_severity_arg(context) -> int | None:
    args = getattr(context, "args", None) or []
    if not args:
        return 3
    raw = str(args[0]).strip().casefold()
    if raw in {"all", "*"}:
        return None
    try:
        value = int(raw)
    except ValueError:
        return 3
    if value < 1:
        return 1
    if value > 3:
        return 3
    return value


def _first_int_arg(context) -> int | None:
    args = getattr(context, "args", None) or []
    if not args:
        return None
    raw = str(args[0]).strip().lstrip("#")
    if raw[:1].casefold() == "a":
        raw = raw[1:]
    try:
        return int(raw)
    except ValueError:
        return None


async def _mark_issue_command(update, context, *, status: str) -> None:
    message = getattr(update, "message", None)
    if await _reject_unauthorized(update, context, message):
        return
    if message is None:
        return

    issue_ref = _first_issue_arg(context)
    if issue_ref is None:
        command = "/issue_done" if status == "fixed" else "/issue_ignore"
        await _maybe_await(
            message.reply_text(
                "🧾 Cập nhật issue\n\n"
                f"Vui lòng dùng dạng `{command} F1` hoặc `{command} S1`."
            )
        )
        return

    issue_type, issue_id = issue_ref
    notes = _issue_notes_arg(context)
    issue = _issue_database(context).mark_issue_status(
        issue_id,
        issue_type=issue_type,
        status=status,
        notes=notes,
    )
    await _maybe_await(message.reply_text(format_issue_status_update(issue, status)))


async def _mark_ai_suggestion_command(update, context, *, status: str) -> None:
    message = getattr(update, "message", None)
    if await _reject_unauthorized(update, context, message):
        return
    if message is None:
        return

    suggestion_id = _first_int_arg(context)
    if suggestion_id is None:
        command = "/ai_accept" if status == "accepted" else "/ai_ignore"
        await _maybe_await(
            message.reply_text(
                "🤖 Cập nhật gợi ý AI\n\n"
                f"Vui lòng dùng dạng `{command} 1`."
            )
        )
        return

    suggestion = _issue_database(context).mark_ai_refinement_suggestion_status(
        suggestion_id,
        status=status,
        notes=_issue_notes_arg(context),
    )
    await _maybe_await(
        message.reply_text(format_ai_suggestion_status_update(suggestion, status))
    )


def _issue_notes_arg(context) -> str | None:
    args = getattr(context, "args", None) or []
    notes = " ".join(str(arg).strip() for arg in args[1:]).strip()
    return notes or None


def _issue_key(issue: IssueRecord) -> str:
    prefix = "F" if issue.issue_type == "feedback" else "S"
    return f"{prefix}{issue.id}"


def _issue_ref_label(issue_type: str, issue_id: int) -> str:
    prefix = "F" if issue_type == "feedback" else "S"
    return f"#{prefix}{issue_id}"


def _ai_gate_icon(status: str) -> str:
    return "✅" if status == "accepted" else "🧪"


def _issue_icon(reason: str, issue_type: str) -> str:
    if reason == "missing_info":
        return "⚠️"
    if reason == "wrong_result":
        return "❌"
    if issue_type == "suspicious":
        return "🧪"
    return "📝"


def _issue_reason_label(reason: str) -> str:
    return {
        "missing_info": "Thiếu thông tin",
        "wrong_result": "Sai kết quả",
        "correct": "Đúng",
        "ends_with_currency": "Có thể thiếu giá sau currency",
        "ends_with_price_marker": "Có thể thiếu giá sau ký hiệu giá",
        "raw_much_longer": "Raw dài hơn nhiều so với kết quả",
        "missing_price_after_currency": "Thiếu số tiền sau currency",
        "missing_price_evidence": "Có thể thiếu giá",
    }.get(reason, reason)


def _limit_inline(value: str, limit: int) -> str:
    return _limit_telegram_text(" ".join(value.split()), limit)


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


def _telegram_username(update) -> str | None:
    user = getattr(update, "effective_user", None)
    if user is None:
        callback_query = getattr(update, "callback_query", None)
        user = getattr(callback_query, "from_user", None) if callback_query is not None else None
    if user is None:
        message = getattr(update, "message", None)
        user = getattr(message, "from_user", None) if message is not None else None
    username = getattr(user, "username", None) if user is not None else None
    return str(username) if username else None


def _telegram_chat_id(update) -> int | None:
    message = getattr(update, "message", None)
    if message is None:
        callback_query = getattr(update, "callback_query", None)
        message = getattr(callback_query, "message", None) if callback_query is not None else None
    chat_id = getattr(message, "chat_id", None)
    return int(chat_id) if chat_id is not None else None


def _telegram_message_id(update) -> int | None:
    message = getattr(update, "message", None)
    if message is None:
        callback_query = getattr(update, "callback_query", None)
        message = getattr(callback_query, "message", None) if callback_query is not None else None
    message_id = getattr(message, "message_id", None)
    return int(message_id) if message_id is not None else None


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


async def _notify_watchfacts_session_owner(context) -> None:
    owner_ids = _allowed_user_ids(context)
    if not owner_ids:
        logger.info("event=telegram.watchfacts_session_alert_skipped reason=no_owner")
        return

    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application is not None else {}
    now = time.monotonic()
    last_sent_value = bot_data.get(WATCHFACTS_SESSION_ALERT_LAST_SENT_KEY)
    if (
        last_sent_value is not None
        and now - float(last_sent_value) < WATCHFACTS_SESSION_ALERT_COOLDOWN_SECONDS
    ):
        logger.info("event=telegram.watchfacts_session_alert_skipped reason=cooldown")
        return

    bot = _context_bot(context)
    send_message = getattr(bot, "send_message", None)
    if send_message is None:
        logger.info("event=telegram.watchfacts_session_alert_skipped reason=no_bot")
        return

    sent_count = 0
    for owner_id in owner_ids:
        try:
            await _maybe_await(
                send_message(chat_id=owner_id, text=WATCHFACTS_OWNER_ALERT_MESSAGE)
            )
            sent_count += 1
        except Exception as exc:
            logger.info(
                "event=telegram.watchfacts_session_alert_failed error_type=%s",
                exc.__class__.__name__,
            )
    if sent_count:
        bot_data[WATCHFACTS_SESSION_ALERT_LAST_SENT_KEY] = now


async def _send_result_batch(
    context,
    message,
    results: list[SearchResult],
    *,
    query: str,
    start_rank: int,
) -> None:
    for offset, result in enumerate(results):
        rank = start_rank + offset
        caption = format_search_result_caption(result)
        reply_markup = _feedback_markup(context, query=query, result=result, rank=rank)
        if result.image_url:
            try:
                await _maybe_await(
                    message.reply_photo(
                        photo=result.image_url,
                        caption=_limit_telegram_text(
                            caption,
                            TELEGRAM_PHOTO_CAPTION_LIMIT,
                        ),
                        reply_markup=reply_markup,
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
                _limit_telegram_text(caption, TELEGRAM_TEXT_MESSAGE_LIMIT),
                reply_markup=reply_markup,
            )
        )


def _limit_telegram_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    if limit <= 1:
        return value[:limit]
    return f"{value[: limit - 1].rstrip()}…"


def _feedback_markup(context, *, query: str, result: SearchResult, rank: int):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    token = _store_feedback_context(context, query=query, result=result, rank=rank)
    rows = [
        [
            InlineKeyboardButton(
                "⚠️ Thiếu thông tin",
                callback_data=f"{FEEDBACK_PREFIX}{token}:missing_info",
            ),
            InlineKeyboardButton(
                "❌ Sai kết quả",
                callback_data=f"{FEEDBACK_PREFIX}{token}:wrong_result",
            ),
        ]
    ]
    if _openwa_handoff_ready(context):
        rows.append(
            [
                InlineKeyboardButton(
                    "💬 Gửi tin nhắn",
                    callback_data=f"{OPENWA_CHAT_PREFIX}{token}",
                )
            ]
        )
    return InlineKeyboardMarkup(
        rows
    )


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


def _generate_result_page(
    context,
    *,
    query: str,
    results: list[SearchResult],
):
    config = _result_page_config(context)
    if config is None or not config.enabled:
        return None
    try:
        return generate_result_page(
            query,
            results,
            config=config,
            total_count=len(results),
        )
    except Exception as exc:
        logger.warning(
            "event=telegram.result_page_failed error_type=%s query_length=%d",
            exc.__class__.__name__,
            len(query),
        )
        return None


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


def _store_feedback_context(
    context,
    *,
    query: str,
    result: SearchResult,
    rank: int,
) -> str:
    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application is not None else {}
    contexts = bot_data.setdefault(FEEDBACK_CONTEXTS_KEY, {})
    while len(contexts) >= MAX_FEEDBACK_CONTEXTS:
        oldest_key = next(iter(contexts))
        contexts.pop(oldest_key, None)
    token = secrets.token_urlsafe(8)
    contexts[token] = {
        "query": query,
        "result": result,
        "rank": rank,
    }
    return token


def _get_feedback_context(context, token: str):
    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application is not None else {}
    contexts = bot_data.get(FEEDBACK_CONTEXTS_KEY, {})
    return contexts.get(token)


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
        refined = await _call_result_refiner(
            refiner,
            str(page["query"]),
            raw_results,
            start_rank=offset + 1,
        )
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
    mode = settings.hybrid_ai_mode
    if mode == "off" or not settings.openai_api_key:
        return None

    from app.db import Database
    from app.ai_refiner import evaluate_refinement_suggestion, refine_search_results

    database = Database(settings.db_path)

    async def refine(
        query: str,
        results: list[SearchResult],
        *,
        start_rank: int = 1,
    ) -> list[SearchResult]:
        start = time.perf_counter()
        refined = await refine_search_results(query, results, settings, database=database)
        latency_ms = int((time.perf_counter() - start) * 1000)

        if mode in {"shadow", "review"}:
            for rank, (original, suggested) in enumerate(
                zip(results, refined),
                start=start_rank,
            ):
                if suggested.listing_text == original.listing_text:
                    continue
                gate = evaluate_refinement_suggestion(query, original, suggested)
                _record_ai_refinement_suggestion(
                    database,
                    settings,
                    query,
                    rank,
                    original,
                    suggested,
                    mode=mode,
                    gate=gate,
                    latency_ms=latency_ms,
                )
            return results

        if mode == "guarded":
            guarded: list[SearchResult] = []
            for rank, (original, suggested) in enumerate(
                zip(results, refined),
                start=start_rank,
            ):
                gate = evaluate_refinement_suggestion(query, original, suggested)
                if suggested.listing_text != original.listing_text:
                    _record_ai_refinement_suggestion(
                        database,
                        settings,
                        query,
                        rank,
                        original,
                        suggested,
                        mode=mode,
                        gate=gate,
                        latency_ms=latency_ms,
                    )
                guarded.append(suggested if gate.status == "accepted" else original)
            guarded.extend(results[len(guarded) :])
            return guarded

        return results

    return refine


async def _call_result_refiner(
    refiner: RefineResults,
    query: str,
    results: list[SearchResult],
    *,
    start_rank: int,
) -> list[SearchResult]:
    try:
        signature = inspect.signature(refiner)
    except (TypeError, ValueError):
        return await refiner(query, results)
    if "start_rank" in signature.parameters:
        return await refiner(query, results, start_rank=start_rank)
    return await refiner(query, results)


def _record_ai_refinement_suggestion(
    database,
    settings: Settings,
    query: str,
    rank: int,
    original: SearchResult,
    suggested: SearchResult,
    *,
    mode: str,
    gate,
    latency_ms: int,
) -> None:
    try:
        database.record_ai_refinement_suggestion(
            query_text=query,
            result_rank=rank,
            mode=mode,
            model=settings.openai_model,
            deterministic_text=original.listing_text,
            suggested_text=suggested.listing_text,
            raw_listing_text=original.raw_listing_text,
            source_url=original.source_url,
            gate_status=gate.status,
            gate_reasons=gate.reasons,
            latency_ms=latency_ms,
        )
    except Exception as exc:
        logger.info(
            "event=telegram.ai_suggestion_record_failed error_type=%s",
            exc.__class__.__name__,
        )


def _build_session_checker(settings: Settings) -> SessionChecker:
    from app.scraper import check_watchfacts_session

    async def check() -> BrowserSessionStatus:
        return await check_watchfacts_session(settings)

    return check


def _results_markup(
    token: str,
    count: int,
    *,
    label: str,
    result_page_url: str | None = None,
):
    if result_page_url:
        return _result_page_markup(result_page_url)
    return _telegram_results_markup(token, count, label=label)


def _result_page_markup(result_page_url: str):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    rows = [
        [
            InlineKeyboardButton(
                "🔗 Mở trang kết quả",
                url=result_page_url,
            )
        ]
    ]
    return InlineKeyboardMarkup(
        rows
    )


def _telegram_results_markup(token: str, count: int, *, label: str):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    rows = [
        [
            InlineKeyboardButton(
                f"{label} {count}",
                callback_data=f"{MORE_RESULTS_PREFIX}{token}",
            )
        ]
    ]
    return InlineKeyboardMarkup(
        rows
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


def _hybrid_ai_mode(context) -> str:
    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application is not None else {}
    value = bot_data.get(HYBRID_AI_MODE_KEY, "off")
    return str(value or "off")


def _openai_model(context) -> str:
    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application is not None else {}
    value = bot_data.get(OPENAI_MODEL_KEY, "disabled")
    return str(value or "disabled")


def _openwa_handoff_status(context) -> str:
    config = _openwa_handoff_config(context)
    if config is None or not config.enabled:
        return "disabled"
    if not config.is_ready:
        return "missing config"
    return "enabled"


def _source_result_id(query: str, rank: int, result: SearchResult) -> str:
    payload = {
        "query": query,
        "rank": rank,
        "listingText": result.listing_text,
        "rawListingText": result.raw_listing_text,
        "sourceUrl": result.source_url,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"watchfacts:{digest[:24]}"


async def _reply_openwa_chat_draft_success(callback_query, dashboard_url: str) -> None:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    message = getattr(callback_query, "message", None)
    if message is None:
        return
    await _maybe_await(
        message.reply_text(
            "✅ Đã tạo chat draft trong OpenWA.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Mở OpenWA", url=dashboard_url)]]
            ),
        )
    )


async def _reply_openwa_chat_draft_error(callback_query, text: str) -> None:
    message = getattr(callback_query, "message", None)
    if message is None:
        return
    await _maybe_await(message.reply_text(f"⚠️ {text}"))


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
