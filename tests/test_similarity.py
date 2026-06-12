from __future__ import annotations

from app.similarity import group_similar_results
from app.telegram_bot import SearchResult, format_search_result_caption


def test_group_similar_results_preserves_alternate_sellers_as_similar() -> None:
    primary = SearchResult(
        "FPJ Elegante Titanium 48mm 2019 full set 780000",
        seller="ASN",
        posted_date="February 26, 2026",
        source_url="/flash-sales/7711702",
    )
    similar_a = SearchResult(
        "FPJ Elegante titanium 48mm 2019 fullset 780000 hkd",
        seller="A",
        posted_date="March 28, 2026",
        source_url="/flash-sales/8053343",
    )
    similar_chris = SearchResult(
        "FPJ Elegante titanium ti 48mm2019 used 780000",
        seller="Chris",
        posted_date="April 5, 2026",
        source_url="/flash-sales/636563",
    )

    grouped = group_similar_results([primary, similar_a, similar_chris])

    assert grouped == [
        SearchResult(
            primary.listing_text,
            seller=primary.seller,
            posted_date=primary.posted_date,
            source_url=primary.source_url,
            similar_results=(similar_a, similar_chris),
        )
    ]
    caption = format_search_result_caption(grouped[0])
    assert "Similar listings" in caption
    assert "A | 28/03/2026 | /flash-sales/8053343" in caption
    assert "Chris | 05/04/2026 | /flash-sales/636563" in caption


def test_group_similar_results_keeps_different_years_separate() -> None:
    older = SearchResult("FPJ Elegante Titanium 48mm 2019 full set 780000")
    newer = SearchResult("FPJ Elegante Titanium 48mm 2022 full set 780000")

    assert group_similar_results([older, newer]) == [older, newer]


def test_group_similar_results_keeps_different_prices_separate() -> None:
    lower = SearchResult("FPJ Elegante Titanium 48mm 2019 full set 780000")
    higher = SearchResult("FPJ Elegante Titanium 48mm 2019 full set 800000")

    assert group_similar_results([lower, higher]) == [lower, higher]


def test_group_similar_results_ignores_query_reference_when_comparing_prices() -> None:
    lower = SearchResult("116500 panda 30.5k")
    higher = SearchResult("116500 panda 31.5k")

    assert group_similar_results([lower, higher], query="116500 panda") == [
        lower,
        higher,
    ]


def test_group_similar_results_keeps_new_and_used_separate() -> None:
    used = SearchResult("FPJ Elegante Titanium 48mm 2019 used 780000")
    new = SearchResult("FPJ Elegante Titanium 48mm 2019 new 780000")

    assert group_similar_results([used, new]) == [used, new]


def test_group_similar_results_prefers_cleaner_primary() -> None:
    noisy = SearchResult(
        "FPJ quantieme perpetuel platinum 2022 used Fullset $298,500USD - [ ] "
        "FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd - [ ] "
        "FPJ Rose Gold CS opendate watch with card $130,000USD",
        seller="Member 9058",
        posted_date="March 28, 2026",
    )
    clean = SearchResult(
        "FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd - [ ]",
        seller="Member 9058",
        posted_date="March 22, 2026",
    )

    grouped = group_similar_results([noisy, clean], query="Fpj Elegante Titanium")

    assert len(grouped) == 1
    assert grouped[0].listing_text == clean.listing_text
    assert grouped[0].similar_results == (noisy,)


def test_group_similar_results_prefers_scored_primary() -> None:
    weak = SearchResult(
        "Patek 5205R blue full set $428000 green strap",
        seller="A",
        posted_date="May 17, 2026",
    )
    local = SearchResult(
        "Patek 5205R green full set $428000",
        seller="B",
        posted_date="May 17, 2026",
    )

    grouped = group_similar_results([weak, local], query="5205r green")

    assert len(grouped) == 1
    assert grouped[0].listing_text == local.listing_text
    assert grouped[0].similar_results == (weak,)
