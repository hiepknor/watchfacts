from __future__ import annotations

from typing import Any

from app.application.search_use_case import RefineResults, SearchUseCase
from app.config import Settings
from app.db import Database
from app.infrastructure.ai_suggestion_repository import AiSuggestionRepository
from app.infrastructure.issue_repository import IssueRepository
from app.infrastructure.search_cache_repository import SearchCacheRepository
from app.integrations.scraper import fetch_watchfacts_html
from app.searching.search import FetchHtml, WatchFactsSearchWorkflow


def build_search_workflow(
    settings: Settings,
    *,
    database: Database | None = None,
    ai_suggestion_repository: Any = None,
    issue_repository: Any = None,
    search_cache_repository: Any = None,
    fetch_html: FetchHtml | None = None,
    refine_results: RefineResults | None = None,
) -> WatchFactsSearchWorkflow:
    """Compose the search workflow from concrete infrastructure adapters."""

    active_database = database if database is not None else Database(settings.db_path)
    return WatchFactsSearchWorkflow(
        settings,
        ai_suggestion_repository=(
            ai_suggestion_repository
            if ai_suggestion_repository is not None
            else AiSuggestionRepository(active_database)
        ),
        issue_repository=(
            issue_repository
            if issue_repository is not None
            else IssueRepository(active_database)
        ),
        search_cache_repository=(
            search_cache_repository
            if search_cache_repository is not None
            else SearchCacheRepository(active_database)
        ),
        fetch_html=fetch_html if fetch_html is not None else fetch_watchfacts_html,
        refine_results=refine_results,
    )


def build_search_use_case(
    settings: Settings,
    **kwargs: Any,
) -> SearchUseCase:
    return SearchUseCase(build_search_workflow(settings, **kwargs))
