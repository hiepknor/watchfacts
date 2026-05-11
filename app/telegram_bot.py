from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.config import Settings


logger = logging.getLogger(__name__)


START_MESSAGE = "Send a WatchFacts search query, for example: 228253a choco"
EMPTY_QUERY_MESSAGE = "Please send a non-empty WatchFacts search query."
PROCESSING_MESSAGE = (
    "🔎 Đang quét WatchFacts\n"
    "⏳ Bot đang tìm listing phù hợp..."
)
WORKFLOW_KEY = "search_workflow"
PROCESSING_MIN_SECONDS_KEY = "processing_min_seconds"
DEFAULT_PROCESSING_MIN_SECONDS = 1.0


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
    if message is not None:
        await _maybe_await(message.reply_text(START_MESSAGE))


async def handle_text_message(update, context) -> None:
    message = getattr(update, "message", None)
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
        await send_search_results(message, results)


async def send_search_results(message, results: list[SearchResult]) -> None:
    if not results:
        await _maybe_await(message.reply_text("No matching listings found."))
        return

    for result in results:
        caption = format_search_result_caption(result)
        if result.image_url:
            await _maybe_await(message.reply_photo(photo=result.image_url, caption=caption))
        else:
            await _maybe_await(message.reply_text(caption))


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


def format_posted_date(value: str) -> str:
    normalized = value.split("·", maxsplit=1)[0].strip()
    for date_format in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(normalized[:19], date_format).strftime("%d/%m/%Y")
        except ValueError:
            continue
    return value


def build_application(settings: Settings, workflow: SearchWorkflow | None = None):
    from telegram.ext import Application, CommandHandler, MessageHandler, filters

    application = Application.builder().token(settings.telegram_bot_token).build()
    application.bot_data[WORKFLOW_KEY] = workflow or PlaceholderSearchWorkflow()
    application.add_handler(CommandHandler("start", start_command))
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
