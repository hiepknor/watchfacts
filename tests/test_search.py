from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from app.config import Settings
from app.db import Database
from app.scraper import ScrapeResult
from app.search import WatchFactsSearchWorkflow


FIXTURE = Path(__file__).parent / "fixtures" / "watchfacts_listing.html"


def make_settings(tmp_path) -> Settings:
    return Settings(
        telegram_bot_token="token",
        watchfacts_url="https://watchfacts.example/simon-match-making",
        headless=True,
        enable_crawl4ai=True,
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        logs_dir=tmp_path / "logs",
        db_path=tmp_path / "data" / "bot.db",
        browser_state_path=tmp_path / "data" / "watchfacts_state.json",
    )


def test_search_workflow_scrapes_parses_matches_dedupes_and_persists(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = FIXTURE.read_text()
    fetch_calls: list[Settings] = []

    async def fetch_html(received_settings: Settings) -> ScrapeResult:
        fetch_calls.append(received_settings)
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )

    results = asyncio.run(workflow.search("228253a choco"))

    assert fetch_calls == [settings]
    assert len(results) == 1
    assert results[0].listing_text == "Rolex 228253A choco N2 467000hkd"
    assert results[0].seller == "HK STOCKS"

    with sqlite3.connect(settings.db_path) as connection:
        query_row = connection.execute(
            "SELECT query_text, normalized_query, result_count FROM queries"
        ).fetchone()
        listing_count = connection.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        result_count = connection.execute("SELECT COUNT(*) FROM query_results").fetchone()[0]

    assert query_row == ("228253a choco", "228253a choco", 1)
    assert listing_count == 1
    assert result_count == 1


def test_search_workflow_persists_no_result_queries(tmp_path) -> None:
    settings = make_settings(tmp_path)

    async def fetch_html(_: Settings) -> ScrapeResult:
        return ScrapeResult(html=FIXTURE.read_text(), final_url=settings.watchfacts_url)

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("does not exist"))

    assert results == []
    with sqlite3.connect(settings.db_path) as connection:
        query_row = connection.execute(
            "SELECT query_text, normalized_query, result_count FROM queries"
        ).fetchone()

    assert query_row == ("does not exist", "does not exist", 0)
