from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.config import Settings
from app.db import Database
from app.dedupe import unique_listings
from app.matcher import filter_matching_listings
from app.parser import ListingCandidate, parse_listings
from app.scraper import ScrapeResult, fetch_watchfacts_html
from app.telegram_bot import SearchResult


FetchHtml = Callable[[Settings], Awaitable[ScrapeResult]]


class WatchFactsSearchWorkflow:
    def __init__(
        self,
        settings: Settings,
        *,
        database: Database | None = None,
        fetch_html: FetchHtml | None = None,
    ) -> None:
        self.settings = settings
        self.database = database or Database(settings.db_path)
        self.fetch_html = fetch_html or fetch_watchfacts_html

    async def search(self, query: str) -> list[SearchResult]:
        scrape_result = await self.fetch_html(self.settings)
        parsed = parse_listings(scrape_result.html)
        matched = filter_matching_listings(query, parsed)
        unique = unique_listings(matched)

        self.database.record_query_results(query, unique)
        return [_to_search_result(listing) for listing in unique]


def _to_search_result(listing: ListingCandidate) -> SearchResult:
    return SearchResult(
        listing_text=listing.listing_text,
        seller=listing.seller,
        posted_date=listing.posted_date,
        image_url=listing.image_url,
        source_url=listing.source_url,
    )
