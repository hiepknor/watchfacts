from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable

from app.config import Settings
from app.db import Database
from app.dedupe import unique_latest_listings
from app.matcher import extract_relevant_listing_text, filter_matching_listings
from app.parser import ListingCandidate, parse_listings
from app.scraper import ScrapeResult, fetch_watchfacts_html
from app.telegram_bot import SearchResult


FetchHtml = Callable[..., Awaitable[ScrapeResult]]
logger = logging.getLogger(__name__)
PRODUCT_REFERENCE_RE = re.compile(
    r"\b(?=[A-Za-z0-9/.-]*\d)[A-Za-z0-9]+(?:/[A-Za-z0-9]+)*\b",
    re.IGNORECASE,
)
MULTI_LIST_REFERENCE_THRESHOLD = 6


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
        logger.info("event=query.start query_length=%d", len(query))
        try:
            scrape_result = await self.fetch_html(self.settings, query=query)
            parsed = parse_listings(scrape_result.html)
            matched = parsed if scrape_result.server_filtered else filter_matching_listings(query, parsed)
            results = [_to_search_result(query, listing) for listing in matched]
            unique = unique_latest_listings(results)

            self.database.record_query_results(query, unique)
            logger.info(
                "event=query.end parsed_count=%d matched_count=%d result_count=%d",
                len(parsed),
                len(matched),
                len(unique),
            )
            return unique
        except Exception as exc:
            logger.error(
                "event=query.error error_type=%s",
                exc.__class__.__name__,
            )
            raise


def _to_search_result(query: str, listing: ListingCandidate) -> SearchResult:
    listing_text = extract_relevant_listing_text(query, listing.listing_text)
    return SearchResult(
        listing_text=listing_text,
        seller=listing.seller,
        posted_date=listing.posted_date,
        image_url=_product_image_url(listing),
        source_url=listing.source_url,
    )


def _product_image_url(listing: ListingCandidate) -> str | None:
    if _looks_like_multi_listing(listing.listing_text):
        return None
    return listing.image_url


def _looks_like_multi_listing(listing_text: str) -> bool:
    references = {
        token.casefold()
        for token in PRODUCT_REFERENCE_RE.findall(listing_text)
        if _looks_like_product_reference(token)
    }
    return len(references) > MULTI_LIST_REFERENCE_THRESHOLD


def _looks_like_product_reference(token: str) -> bool:
    normalized = token.casefold()
    if re.fullmatch(r"\d{1,2}/\d{2,4}y?", normalized):
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?[km]", normalized):
        return False
    if normalized.isdigit() and len(normalized) == 4:
        year = int(normalized)
        if 1900 <= year <= 2099:
            return False
    if any(currency in normalized for currency in ("hkd", "usd", "eur", "aed")):
        return False
    if len(normalized) < 4 and "/" not in normalized:
        return False
    return True
