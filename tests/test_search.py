from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

import app.search as search_module
from app.config import Settings
from app.db import Database
from app.scraper import ScrapeResult
from app.search import SEARCH_CACHE_VERSION, WatchFactsSearchWorkflow
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


def test_search_workflow_preserves_seller_phone_from_watchfacts_json(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "5712G Used 2015 - 76k usdt",
          "companyName": "Issac",
          "companyWhatsapp": "17826241887",
          "number": 3074930,
          "createdOn": "2026-06-02 03:18:08",
          "listings": [
            {
              "title": "5712G Used 2015 - 76k usdt",
              "frontImage": "https://watchfacts.example/5712g.jpg"
            }
          ]
        }
      ]
    }
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )

    results = asyncio.run(workflow.search("5712g"))

    assert len(results) == 1
    assert results[0].seller == "Issac"
    assert results[0].seller_phone == "17826241887"


def test_search_workflow_serves_repeated_query_from_cache(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = FIXTURE.read_text()
    fetch_count = 0

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        nonlocal fetch_count
        fetch_count += 1
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )

    first = asyncio.run(workflow.search("228253a choco"))
    second = asyncio.run(workflow.search("  228253A   CHOCO "))

    assert fetch_count == 1
    assert second == first
    with sqlite3.connect(settings.db_path) as connection:
        query_count = connection.execute("SELECT COUNT(*) FROM queries").fetchone()[0]
        cache_count = connection.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0]

    assert query_count == 2
    assert cache_count == 1


def test_search_workflow_refetches_after_cache_expiry(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = FIXTURE.read_text()
    fetch_count = 0

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        nonlocal fetch_count
        fetch_count += 1
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )

    asyncio.run(workflow.search("228253a choco"))
    with sqlite3.connect(settings.db_path) as connection:
        connection.execute(
            "UPDATE search_cache SET expires_at = '2000-01-01T00:00:00+00:00'"
        )
    asyncio.run(workflow.search("228253a choco"))

    assert fetch_count == 2


def test_search_cache_key_includes_search_cache_version(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path)

    first_key = search_module._search_cache_key("5712g", settings)
    monkeypatch.setattr(search_module, "SEARCH_CACHE_VERSION", f"{SEARCH_CACHE_VERSION}-test")
    second_key = search_module._search_cache_key("5712g", settings)

    assert first_key != second_key


def test_search_workflow_coalesces_concurrent_same_query_fetches(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = FIXTURE.read_text()
    fetch_count = 0

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        nonlocal fetch_count
        fetch_count += 1
        await asyncio.sleep(0.01)
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    workflow = WatchFactsSearchWorkflow(
        settings,
        database=Database(settings.db_path),
        fetch_html=fetch_html,
    )

    async def run_searches() -> tuple[list[SearchResult], list[SearchResult]]:
        first, second = await asyncio.gather(
            workflow.search("228253a choco"),
            workflow.search("228253a choco"),
        )
        return first, second

    first, second = asyncio.run(run_searches())

    assert fetch_count == 1
    assert first == second
    with sqlite3.connect(settings.db_path) as connection:
        query_count = connection.execute("SELECT COUNT(*) FROM queries").fetchone()[0]

    assert query_count == 2


def test_search_workflow_limits_search_runtime_concurrent_distinct_queries(tmp_path) -> None:
    settings = Settings(
        telegram_bot_token="",
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
        runtime_mode="search",
        search_max_concurrent_searches=1,
    )
    active_fetches = 0
    max_active_fetches = 0

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        nonlocal active_fetches, max_active_fetches
        active_fetches += 1
        max_active_fetches = max(max_active_fetches, active_fetches)
        await asyncio.sleep(0.01)
        active_fetches -= 1
        return ScrapeResult(html="{}", final_url=settings.watchfacts_url)

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    async def run_searches() -> None:
        await asyncio.gather(
            workflow.search("5712g"),
            workflow.search("5712r"),
        )

    asyncio.run(run_searches())

    assert max_active_fetches == 1


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


def test_server_filtered_color_query_filters_text_mismatches(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "15510OR 2022 New 92k",
          "companyName": "Seller NonBlue",
          "number": 111,
          "frontImage": "https://watchfacts.example/15510or-noblue.jpg"
        },
        {
          "title": "15510OR Blue dial 2024 Fullset 94k",
          "companyName": "Seller Blue",
          "number": 222,
          "frontImage": "https://watchfacts.example/15510or-blue.jpg"
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
    results = asyncio.run(workflow.search("15510or blue"))

    assert [result.listing_text for result in results] == [
        "15510OR Blue dial 2024 Fullset 94k",
    ]


def test_server_filtered_color_query_uses_dial_color_match_text_for_server_json(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "15510OR New 92k",
          "dialColor": "blue",
          "companyName": "Seller MetaBlue",
          "number": 111
        },
        {
          "title": "15510OR New 96k",
          "companyName": "Seller NoColor",
          "number": 222
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
    results = asyncio.run(workflow.search("15510or blue"))

    assert len(results) == 1
    assert results[0].listing_text == "15510OR New 92k"
    assert results[0].source_url == "/flash-sales/111"


def test_search_workflow_drops_server_filtered_non_sale_requests(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "Lookingfor 228235A choco new 2026",
          "companyName": "Buyer",
          "number": 111
        },
        {
          "title": "228235A Choco New 3/26 $58,000 USD",
          "companyName": "Seller",
          "number": 222
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

    results = asyncio.run(workflow.search("228235a choco"))

    assert [result.listing_text for result in results] == [
        "228235A Choco New 3/26 $58,000 USD"
    ]


def test_search_workflow_refilters_server_filtered_non_color_descriptor_queries(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "228235A Choco New 3/26 $58,000 USD",
          "companyName": "Seller",
          "number": 111
        },
        {
          "title": "228235A Sundust 436k hkd 12/25y",
          "companyName": "Other",
          "number": 222
        },
        {
          "title": "228235A Cho N4 $465K",
          "companyName": "Third",
          "number": 333
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

    results = asyncio.run(workflow.search("228235a cho"))

    assert [result.listing_text for result in results] == [
        "228235A Choco New 3/26 $58,000 USD",
        "228235A Cho N4 $465K",
    ]


def test_search_workflow_refilters_server_filtered_non_color_variant_descriptors(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "228349RBR Blue OMBRE ROM 2025 Used HKD 525K",
          "companyName": "Seller",
          "number": 111
        },
        {
          "title": "228349RBR A METE 2024 $610000",
          "companyName": "Member 1000",
          "number": 222
        },
        {
          "title": "228349 pave N12 720000",
          "companyName": "Other",
          "number": 333
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

    results = asyncio.run(workflow.search("228349rbr mete"))

    assert [result.listing_text for result in results] == [
        "228349RBR A METE 2024 $610000"
    ]


def test_search_workflow_falls_back_to_image_backed_reference_matches(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "228349 r br Blue OMBRE ROM 2025 Used HKD 525K",
          "companyName": "Seller",
          "number": 111,
          "frontImage": "https://watchfacts.example/228349rbr.jpg"
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

    results = asyncio.run(workflow.search("228349rbr mete"))

    assert [result.listing_text for result in results] == [
        "228349 r br Blue OMBRE ROM 2025 Used HKD 525K"
    ]
    assert results[0].image_url == "https://watchfacts.example/228349rbr.jpg"


def test_search_workflow_refilters_server_filtered_non_color_variant_descriptor_alias(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "228349RBR A METE 2024 $610000",
          "companyName": "Member 1000",
          "number": 222
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

    results = asyncio.run(workflow.search("228349rbr meteorite"))

    assert [result.listing_text for result in results] == [
        "228349RBR A METE 2024 $610000"
    ]


def test_server_filtered_query_matching_policy() -> None:
    assert (
        search_module._server_filtered_query_matching_policy(
            "228349rbr mete",
            search_module._color_descriptors("228349rbr mete"),
        )
        == "strict_non_color_descriptor"
    )
    assert (
        search_module._server_filtered_query_matching_policy(
            "228349rbr meteorite",
            search_module._color_descriptors("228349rbr meteorite"),
        )
        == "strict_non_color_descriptor"
    )
    assert (
        search_module._server_filtered_query_requires_local_matching(
            "228349rbr mete",
            search_module._color_descriptors("228349rbr mete"),
        )
        is True
    )

    assert (
        search_module._server_filtered_query_matching_policy(
            "228235A choco",
            search_module._color_descriptors("228235A choco"),
        )
        == "strict_color_alias"
    )
    assert (
        search_module._server_filtered_query_requires_local_matching(
            "228235A choco",
            search_module._color_descriptors("228235A choco"),
        )
        is True
    )

    assert (
        search_module._server_filtered_query_matching_policy(
            "116500 panda",
            search_module._color_descriptors("116500 panda"),
        )
        == "coarse_pass_through_alias"
    )
    assert (
        search_module._server_filtered_query_requires_local_matching(
            "116500 panda",
            search_module._color_descriptors("116500 panda"),
        )
        is False
    )

    assert (
        search_module._server_filtered_query_matching_policy(
            "126500ln white",
            search_module._color_descriptors("126500ln white"),
        )
        == "strict_non_color_descriptor"
    )
    assert (
        search_module._server_filtered_query_requires_local_matching(
            "126500ln white",
            search_module._color_descriptors("126500ln white"),
        )
        is True
    )


def test_search_workflow_refilters_server_filtered_alias_plus_noncolor_descriptor(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "116500 panda 30.5k",
          "companyName": "Dealer A",
          "number": 2
        },
        {
          "title": "116500 mete 31.5k",
          "companyName": "Dealer B",
          "number": 3
        },
        {
          "title": "116500 white dial 31.5k",
          "companyName": "Dealer C",
          "number": 4
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

    results = asyncio.run(workflow.search("116500 panda mete"))

    assert [result.listing_text for result in results] == [
        "116500 mete 31.5k"
    ]


def test_search_workflow_demotes_missing_price_result_when_priced_results_exist(
    tmp_path,
) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "5205r 2026",
          "companyName": "H",
          "repostedAt": "2026-05-18 10:00:00",
          "number": 1
        },
        {
          "title": "5205R 2026-04 $428000",
          "companyName": "Sally",
          "repostedAt": "2026-05-17 10:00:00",
          "number": 2
        },
        {
          "title": "5205r 2026/3 $435,000",
          "companyName": "Hugh",
          "repostedAt": "2026-03-16 10:00:00",
          "number": 3
        },
        {
          "title": "5205R 2026-04 436k HKD",
          "companyName": "Mr Et",
          "repostedAt": "2026-05-10 10:00:00",
          "number": 4
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

    results = asyncio.run(workflow.search("5205r 2026"))

    assert [result.listing_text for result in results] == [
        "5205R 2026-04 $428000",
        "5205R 2026-04 436k HKD",
        "5205r 2026/3 $435,000",
        "5205r 2026",
    ]


def test_search_workflow_expands_sparse_year_query_and_refilters_locally(tmp_path) -> None:
    settings = make_settings(tmp_path)
    fetch_queries: list[str | None] = []

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        fetch_queries.append(query)
        if query == "126500ln white 2026":
            html = """
            {
              "listings": [
                {
                  "title": "Rolex 126500ln white 2026 n1 HKD 273000",
                  "companyName": "AP",
                  "repostedAt": "2026-03-08 10:00:00",
                  "number": 1
                }
              ]
            }
            """
        else:
            html = """
            {
              "listings": [
                {
                  "title": "126500LN White N3/2026 HK$279000 without box",
                  "companyName": "Dealer A",
                  "repostedAt": "2026-03-03 10:00:00",
                  "number": 2
                },
                {
                  "title": "126500LN Black N3/2026 HK$236000",
                  "companyName": "Dealer B",
                  "repostedAt": "2026-03-03 10:00:00",
                  "number": 3
                }
              ]
            }
            """
        return ScrapeResult(
            html=html,
            final_url="https://watchfacts.example/simon-search-matches",
            server_filtered=True,
        )

    workflow = WatchFactsSearchWorkflow(settings, fetch_html=fetch_html)

    results = asyncio.run(workflow.search("126500ln white 2026"))

    assert fetch_queries == ["126500ln white 2026", "126500ln white"]
    assert [result.listing_text for result in results] == [
        "Rolex 126500ln white 2026 n1 HKD 273000",
        "126500LN White N3/2026 HK$279000 without box",
    ]


def test_search_workflow_drops_server_filtered_conflicting_color_descriptor(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "126500LN White N3/2026 HK$279000 without box",
          "companyName": "Dealer A",
          "repostedAt": "2026-03-03 10:00:00",
          "number": 2
        },
        {
          "title": "126500LN Black N3/2026 HK$236000",
          "companyName": "Dealer B",
          "repostedAt": "2026-03-03 10:00:00",
          "number": 3
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

    results = asyncio.run(workflow.search("126500ln white 2026"))

    assert [result.listing_text for result in results] == [
        "126500LN White N3/2026 HK$279000 without box",
    ]


def test_search_workflow_keeps_server_filtered_panda_alias_results(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    {
      "listings": [
        {
          "title": "116500 panda 30.5k",
          "companyName": "Dealer A",
          "repostedAt": "2026-04-01 10:00:00",
          "number": 2
        },
        {
          "title": "116500 white dial 31.5k",
          "companyName": "Dealer B",
          "repostedAt": "2026-04-01 10:00:00",
          "number": 3
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

    results = asyncio.run(workflow.search("116500 panda"))

    assert [result.listing_text for result in results] == [
        "116500 panda 30.5k",
        "116500 white dial 31.5k",
    ]


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


def test_search_workflow_records_suspicious_incomplete_results(tmp_path) -> None:
    settings = make_settings(tmp_path)
    html = """
    <html>
      <body>
        <div class="product">
          <div class="product-description">
            <a class="title-link" href="/flash-sales/9927122">
              ✅PP ❣️5711R Watch and Service paper, HKD 605000
              ❣️5712R 2016/ HKD
              ❣️5134R Service paper, HKD 130000
            </a>
          </div>
          <span data-field="seller">AM.Timepiece TONY</span>
          <time data-field="posted-date">February 14, 2026</time>
        </div>
      </body>
    </html>
    """

    async def fetch_html(_: Settings, *, query: str | None = None) -> ScrapeResult:
        return ScrapeResult(html=html, final_url=settings.watchfacts_url)

    database = Database(settings.db_path)
    workflow = WatchFactsSearchWorkflow(
        settings,
        database=database,
        fetch_html=fetch_html,
    )

    results = asyncio.run(workflow.search("5712r"))
    issues = database.list_open_suspicious_issues()

    assert results[0].listing_text == "5712R 2016/ HKD"
    assert {issue.issue_type for issue in issues} == {"suspicious"}
    assert {issue.reason for issue in issues} >= {
        "ends_with_currency",
        "missing_price_after_currency",
    }
    assert {issue.listing_text for issue in issues} == {"5712R 2016/ HKD"}


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


def test_search_workflow_refines_results_with_openai_when_enabled(tmp_path) -> None:
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
        hybrid_ai_mode="guarded",
        openai_api_key="sk-test",
        openai_model="test-model",
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


def test_search_workflow_dedupes_again_after_openai_refine(tmp_path) -> None:
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
        hybrid_ai_mode="guarded",
        openai_api_key="sk-test",
        openai_model="test-model",
    )
    html = """
    {
      "listings": [
        {
          "title": "FPJ quantieme perpetuel - [ ] FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd - [ ] FPJ Rose Gold CS opendate watch",
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


def test_search_workflow_records_shadow_ai_suggestions_without_changing_results(
    tmp_path,
) -> None:
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
        hybrid_ai_mode="shadow",
        openai_api_key="sk-test",
        openai_model="test-model",
    )
    html = """
    {
      "listings": [
        {
          "title": "FPJ quantieme perpetuel / FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd / FPJ Rose Gold CS",
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
                raw_listing_text=result.raw_listing_text,
            )
            for result in results
        ]

    database = Database(settings.db_path)
    workflow = WatchFactsSearchWorkflow(
        settings,
        database=database,
        fetch_html=fetch_html,
        refine_results=refine_results,
    )

    results = asyncio.run(workflow.search("Fpj Elegante Titanium"))
    suggestions = database.list_ai_refinement_suggestions()

    assert results[0].listing_text.endswith("FPJ Rose Gold CS")
    assert suggestions[0].mode == "shadow"
    assert suggestions[0].gate_status == "accepted"
    assert suggestions[0].deterministic_text == results[0].listing_text
    assert suggestions[0].suggested_text == (
        "FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd"
    )


def test_search_workflow_records_guarded_ai_suggestions_and_applies_safe_result(
    tmp_path,
) -> None:
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
        hybrid_ai_mode="guarded",
        openai_api_key="sk-test",
        openai_model="test-model",
    )
    html = """
    {
      "listings": [
        {
          "title": "FPJ quantieme perpetuel / FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd / FPJ Rose Gold CS",
          "companyName": "Seller",
          "number": 12
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
                raw_listing_text=result.raw_listing_text,
            )
            for result in results
        ]

    database = Database(settings.db_path)
    workflow = WatchFactsSearchWorkflow(
        settings,
        database=database,
        fetch_html=fetch_html,
        refine_results=refine_results,
    )

    results = asyncio.run(workflow.search("Fpj Elegante Titanium"))
    suggestions = database.list_ai_refinement_suggestions()

    assert results[0].listing_text == (
        "FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd"
    )
    assert suggestions[0].mode == "guarded"
    assert suggestions[0].gate_status == "accepted"


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
