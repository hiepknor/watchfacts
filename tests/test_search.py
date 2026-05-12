from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

from app.config import Settings
from app.db import Database
from app.scraper import ScrapeResult
from app.search import WatchFactsSearchWorkflow
from app.telegram_bot import SearchResult


FIXTURE = Path(__file__).parent / "fixtures" / "watchfacts_listing.html"


def make_settings(tmp_path) -> Settings:
    return Settings(
        telegram_bot_token="token",
        telegram_allowed_user_ids=(),
        telegram_result_limit=5,
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
    fetch_calls: list[tuple[Settings, str | None]] = []

    async def fetch_html(received_settings: Settings, *, query: str | None = None) -> ScrapeResult:
        fetch_calls.append((received_settings, query))
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )

    results = asyncio.run(workflow.search("228253a choco"))

    assert fetch_calls == [(settings, "228253a choco")]
    assert len(results) == 1
    assert results[0].listing_text == "Rolex 228253A choco N2 467000hkd"
    assert results[0].seller == "HK STOCKS"
    assert results[0].image_url == "https://watchfacts.example/images/228253a.jpg"

    with sqlite3.connect(settings.db_path) as connection:
        query_row = connection.execute(
            "SELECT query_text, normalized_query, result_count FROM queries"
        ).fetchone()
        listing_count = connection.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        result_count = connection.execute(
            "SELECT COUNT(*) FROM query_results"
        ).fetchone()[0]

    assert query_row == ("228253a choco", "228253a choco", 1)
    assert listing_count == 1
    assert result_count == 1


def test_search_workflow_persists_no_result_queries(tmp_path) -> None:
    settings = make_settings(tmp_path)

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(html=FIXTURE.read_text(), final_url=settings.watchfacts_url)

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("does not exist"))

    assert results == []
    with sqlite3.connect(settings.db_path) as connection:
        query_row = connection.execute(
            "SELECT query_text, normalized_query, result_count FROM queries"
        ).fetchone()

    assert query_row == ("does not exist", "does not exist", 0)


def test_search_workflow_keeps_server_filtered_results_without_strict_refilter(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "matched_term": null,
      "listings": [
        {
          "title": "2017 Patek 5712/1A Fullset Retail Ready $116",
          "companyName": "Khoa Ng",
          "repostedAt": "2026-04-22 18:23:39",
          "number": 40881,
          "listings": [
            {
              "title": "2017 Patek 5712/1A Fullset Retail Ready $116",
              "frontImage": "https://watchfacts.example/5712.jpg",
              "dialColor": "blue"
            }
          ]
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("5712 blue"))

    assert len(results) == 1
    assert results[0].listing_text == "2017 Patek 5712/1A Fullset Retail Ready $116"
    assert results[0].seller == "Khoa Ng"
    assert results[0].posted_date == "April 22, 2026"
    assert results[0].image_url == "https://watchfacts.example/5712.jpg"
    assert results[0].source_url == "/flash-sales/40881"


def test_search_workflow_omits_bundle_images_for_multi_listing_cards(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    <html>
      <body>
        <div class="product">
          <a href="/flash-sales/1">
            <img src="https://watchfacts.example/watch-bundle.jpg" />
          </a>
          <div class="product-description">
            <a class="title-link" href="/flash-sales/1">
              124200 pistachio $60000 N12
              126303g black oys $128000 N8
              126331g sundust jub $155500 N3
              126334 blue jub $116500 N2
              7118/1200A blue N2/2026y 725k hkd
              7300/1200R white 03/2026 $366k
              5726/1A blue N9/2025y 1.065m hkd
            </a>
          </div>
          <span data-field="seller">Forest</span>
          <time data-field="posted-date">April 23, 2026</time>
        </div>
      </body>
    </html>
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("7118/1200a blue"))

    assert len(results) == 1
    assert results[0].listing_text == "7118/1200A blue N2/2026y 725k hkd"
    assert results[0].image_url is None


def test_search_workflow_scopes_variant_reference_and_omits_bundle_image(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    <html>
      <body>
        <div class="product">
          <a href="/flash-sales/2">
            <img src="https://watchfacts.example/wrong-first-product.jpg" />
          </a>
          <div class="product-description">
            <a class="title-link" href="/flash-sales/2">
              PP 7130G-016 Paper of 2022 USD31000
              PP7010G-013, 2025 model, full set price: US$63,000
              5726/1A-014 2021 Full Set: US$115,000
            </a>
          </div>
          <span data-field="seller">HL</span>
          <time data-field="posted-date">May 9, 2026</time>
        </div>
      </body>
    </html>
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("5726/1a"))

    assert len(results) == 1
    assert results[0].listing_text == "5726/1A-014 2021 Full Set: US$115,000"
    assert results[0].seller == "HL"
    assert results[0].image_url is None


def test_search_workflow_logs_counts_without_query_or_state_path(tmp_path, caplog) -> None:
    settings = make_settings(tmp_path)

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(html=FIXTURE.read_text(), final_url=settings.watchfacts_url)

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    with caplog.at_level(logging.INFO, logger="app.search"):
        asyncio.run(workflow.search("228253a choco"))

    log_text = caplog.text
    assert "event=query.start query_length=13" in log_text
    assert "event=query.end parsed_count=2 matched_count=1 result_count=1" in log_text
    assert "228253a choco" not in log_text
    assert str(settings.browser_state_path) not in log_text
    assert settings.telegram_bot_token not in log_text


def test_search_workflow_logs_error_type_without_query_or_state_path(tmp_path, caplog) -> None:
    settings = make_settings(tmp_path)

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        raise RuntimeError("network unavailable")

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    with caplog.at_level(logging.INFO, logger="app.search"):
        try:
            asyncio.run(workflow.search("228253a choco"))
        except RuntimeError:
            pass

    log_text = caplog.text
    assert "event=query.error error_type=RuntimeError" in log_text
    assert "228253a choco" not in log_text
    assert str(settings.browser_state_path) not in log_text
    assert settings.telegram_bot_token not in log_text


def test_search_workflow_refines_results_with_local_llm_when_enabled(tmp_path) -> None:
    settings = Settings(
        telegram_bot_token="token",
        telegram_allowed_user_ids=(),
        telegram_result_limit=5,
        watchfacts_url="https://watchfacts.example/simon-match-making",
        headless=True,
        enable_crawl4ai=True,
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        logs_dir=tmp_path / "logs",
        db_path=tmp_path / "data" / "bot.db",
        browser_state_path=tmp_path / "data" / "watchfacts_state.json",
        local_llm_enabled=True,
    )
    html = """
    <html>
      <body>
        <div class="product">
          <div class="product-description">
            <a class="title-link" href="/flash-sales/3">
              FPJ quantieme perpetuel platinum 2022 used Fullset $298,500USD - [ ]
              FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd - [ ]
              FPJ Rose Gold CS opendate watch with card $130,000USD
            </a>
          </div>
          <span data-field="seller">Member 9058</span>
          <time data-field="posted-date">March 28, 2026</time>
        </div>
      </body>
    </html>
    """
    refine_calls: list[tuple[str, list[SearchResult]]] = []

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    async def refine_results(query: str, results: list[SearchResult]) -> list[SearchResult]:
        refine_calls.append((query, results))
        return [
            SearchResult(
                listing_text="FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd",
                seller=results[0].seller,
                posted_date=results[0].posted_date,
                image_url=results[0].image_url,
                source_url=results[0].source_url,
            )
        ]

    workflow = WatchFactsSearchWorkflow(
        settings,
        fetch_html=fetch_html,
        refine_results=refine_results,
    )

    results = asyncio.run(workflow.search("Fpj Elegante Titanium"))

    assert refine_calls
    assert results[0].listing_text == "FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd"


def test_search_workflow_dedupes_again_after_local_llm_refine(tmp_path) -> None:
    settings = Settings(
        telegram_bot_token="token",
        telegram_allowed_user_ids=(),
        telegram_result_limit=5,
        watchfacts_url="https://watchfacts.example/simon-match-making",
        headless=True,
        enable_crawl4ai=True,
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        logs_dir=tmp_path / "logs",
        db_path=tmp_path / "data" / "bot.db",
        browser_state_path=tmp_path / "data" / "watchfacts_state.json",
        local_llm_enabled=True,
    )
    html = """
    {
      "listings": [
        {
          "title": "FPJ Elegante Titanium White 48mm 2022 Used Fullset 119,000usd - [ ] FPJ Rose Gold CS opendate watch",
          "companyName": "Member 9058",
          "repostedAt": "2026-03-22 10:00:00",
          "number": 10
        },
        {
          "title": "FPJ quantieme perpetuel platinum - [ ] FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd - [ ] FPJ Rose Gold CS",
          "companyName": "Member 9058",
          "repostedAt": "2026-03-28 10:00:00",
          "number": 11
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    async def refine_results(_: str, results: list[SearchResult]) -> list[SearchResult]:
        return [
            SearchResult(
                listing_text="FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd",
                seller=result.seller,
                posted_date=result.posted_date,
                image_url=result.image_url,
                source_url=result.source_url,
            )
            for result in results
        ]

    workflow = WatchFactsSearchWorkflow(
        settings,
        fetch_html=fetch_html,
        refine_results=refine_results,
    )

    results = asyncio.run(workflow.search("Fpj Elegante Titanium"))

    assert len(results) == 1
    assert results[0].posted_date == "March 28, 2026"
    assert results[0].source_url == "/flash-sales/11"


def test_search_workflow_final_dedupe_keeps_newest_when_text_matches_across_sellers(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "FPJ Elegante titanium ti",
          "companyName": "A",
          "repostedAt": "2026-04-03 10:00:00",
          "number": 20
        },
        {
          "title": "FPJ Elegante titanium ti",
          "companyName": "Chris",
          "repostedAt": "2026-04-05 10:00:00",
          "number": 21
        },
        {
          "title": "FPJ Elegante titanium ti",
          "companyName": "KI",
          "repostedAt": "2026-03-30 10:00:00",
          "number": 22
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("Fpj Elegante Titanium"))

    assert len(results) == 1
    assert results[0].seller == "Chris"
    assert results[0].posted_date == "April 5, 2026"
    assert results[0].source_url == "/flash-sales/21"
