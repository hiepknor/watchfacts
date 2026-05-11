from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Protocol

from app.config import Settings


logger = logging.getLogger(__name__)


START_MESSAGE = "Send a WatchFacts search query, for example: 228253a choco"
EMPTY_QUERY_MESSAGE = "Please send a non-empty WatchFacts search query."
WORKFLOW_KEY = "search_workflow"


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

    workflow = _get_search_workflow(context)
    try:
        results = await workflow.search(query)
    except Exception as exc:
        logger.exception(
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
        await _maybe_await(message.reply_text(format_search_results(results)))


def format_search_results(results: list[SearchResult]) -> str:
    if not results:
        return "No matching listings found."

    formatted: list[str] = []
    for result in results:
        lines = [result.listing_text]
        if result.seller:
            lines.append(f"Seller: {result.seller}")
        if result.posted_date:
            lines.append(f"Posted: {result.posted_date}")
        if result.image_url:
            lines.append(f"Image: {result.image_url}")
        if result.source_url:
            lines.append(f"Source: {result.source_url}")
        formatted.append("\n".join(lines))

    return "\n\n".join(formatted)


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


async def _maybe_await(value) -> None:
    if inspect.isawaitable(value):
        await value
