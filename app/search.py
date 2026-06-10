from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable

from app.config import Settings
from app.db import Database
from app.dedupe import unique_latest_by_text, unique_latest_listings
from app.ai_refiner import evaluate_refinement_suggestion
from app.issues import detect_suspicious_result
from app.matcher_token_classification import parse_query_terms
from app.matcher_aliases import canonicalize_descriptor_tokens_as_set
from app.matcher import (
    extract_relevant_listing_text,
    filter_matching_listings,
    is_non_sale_request,
    listing_matches,
    normalize_text,
)
from app.parser import ListingCandidate, parse_listings
from app.result_scoring import rank_results_by_quality
from app.scraper import ScrapeResult, fetch_watchfacts_html
from app.search_result import SearchResult, search_results_to_dicts
from app.similarity import group_similar_results


FetchHtml = Callable[..., Awaitable[ScrapeResult]]
RefineResults = Callable[[str, list[SearchResult]], Awaitable[list[SearchResult]]]
logger = logging.getLogger(__name__)
SEARCH_CACHE_VERSION = "search-v10"
PRODUCT_REFERENCE_RE = re.compile(
    r"\b(?=[A-Za-z0-9/.-]*\d)[A-Za-z0-9]+(?:/[A-Za-z0-9]+)*\b",
    re.IGNORECASE,
)
MULTI_LIST_REFERENCE_THRESHOLD = 1
_IN_FLIGHT_SEARCHES: dict[str, asyncio.Task[list[SearchResult]]] = {}
_SEARCH_SEMAPHORES: dict[str, asyncio.Semaphore] = {}


class WatchFactsSearchWorkflow:
    def __init__(
        self,
        settings: Settings,
        *,
        database: Database | None = None,
        fetch_html: FetchHtml | None = None,
        refine_results: RefineResults | None = None,
    ) -> None:
        self.settings = settings
        self.database = database or Database(settings.db_path)
        self.fetch_html = fetch_html or fetch_watchfacts_html
        self.refine_results = refine_results

    async def search(self, query: str) -> list[SearchResult]:
        logger.info("event=query.start query_length=%d", len(query))
        cache_key = _search_cache_key(query, self.settings)
        in_flight_key = f"{self.settings.db_path.resolve()}:{cache_key}"
        try:
            cached_results = self._get_cached_results(cache_key)
            if cached_results is not None:
                results, cache_metrics = cached_results
                self.database.record_query_results(
                    query,
                    results,
                    image_missing_count=cache_metrics["image_missing_count"],
                    server_filtered_hit_count=cache_metrics["server_filtered_hit_count"],
                    playwright_fallback_count=cache_metrics["playwright_fallback_count"],
                )
                logger.info("event=query.cache_hit result_count=%d", len(results))
                return results

            task = _IN_FLIGHT_SEARCHES.get(in_flight_key)
            if task is None:
                task = asyncio.create_task(self._search_uncached(query, cache_key))
                _IN_FLIGHT_SEARCHES[in_flight_key] = task
                owner = True
            else:
                owner = False
                logger.info("event=query.coalesced")

            try:
                results = await task
            finally:
                if owner:
                    _IN_FLIGHT_SEARCHES.pop(in_flight_key, None)

            if not owner:
                cache_metrics = self.database.get_search_cache_quality_metrics(cache_key)
                self.database.record_query_results(
                    query,
                    results,
                    image_missing_count=self._count_missing_images(results),
                    server_filtered_hit_count=cache_metrics["server_filtered_hit_count"],
                    playwright_fallback_count=cache_metrics["playwright_fallback_count"],
                )
            return results
        except Exception as exc:
            logger.error(
                "event=query.error error_type=%s",
                exc.__class__.__name__,
            )
            raise

    async def _search_uncached(self, query: str, cache_key: str) -> list[SearchResult]:
        semaphore = _search_concurrency_semaphore(self.settings)
        if semaphore is None:
            return await self._search_uncached_inner(query, cache_key)
        async with semaphore:
            return await self._search_uncached_inner(query, cache_key)

    async def _search_uncached_inner(
        self,
        query: str,
        cache_key: str,
    ) -> list[SearchResult]:
        scrape_result = await self.fetch_html(self.settings, query=query)
        server_filtered_hit_count = int(scrape_result.server_filtered)
        playwright_fallback_count = int(scrape_result.used_playwright_fallback)
        parsed = parse_listings(scrape_result.html)
        matched = (
            _filter_server_filtered_listings(query, parsed)
            if scrape_result.server_filtered
            else filter_matching_listings(query, parsed)
        )
        parsed_count = len(parsed)
        if _should_expand_year_query(query, len(matched)):
            expanded_query = _query_without_year_descriptors(query)
            if expanded_query is not None:
                expanded_scrape_result = await self.fetch_html(
                    self.settings,
                    query=expanded_query,
                )
                server_filtered_hit_count += int(expanded_scrape_result.server_filtered)
                playwright_fallback_count += int(
                    expanded_scrape_result.used_playwright_fallback
                )
                expanded_parsed = parse_listings(expanded_scrape_result.html)
                parsed_count += len(expanded_parsed)
                matched = _merge_listing_candidates(
                    matched,
                    filter_matching_listings(query, expanded_parsed),
                )
        results = [_to_search_result(query, listing) for listing in matched]
        unique = unique_latest_listings(results)
        if self.refine_results is not None and self.settings.hybrid_ai_mode != "off":
            unique = await self._handle_hybrid_refinement(query, unique)
        unique = unique_latest_by_text(unique)
        unique = rank_results_by_quality(unique, query=query)
        unique = group_similar_results(unique, query=query)

        self.database.record_query_results(
            query,
            unique,
            image_missing_count=self._count_missing_images(unique),
            server_filtered_hit_count=server_filtered_hit_count,
            playwright_fallback_count=playwright_fallback_count,
        )
        self._record_suspicious_results(query, unique)
        self._record_cached_results(
            cache_key=cache_key,
            query=query,
            results=unique,
            image_missing_count=self._count_missing_images(unique),
            server_filtered_hit_count=server_filtered_hit_count,
            playwright_fallback_count=playwright_fallback_count,
        )
        logger.info(
            "event=query.end parsed_count=%d matched_count=%d result_count=%d",
            parsed_count,
            len(matched),
            len(unique),
        )
        return unique

    @staticmethod
    def _count_missing_images(results: list[SearchResult]) -> int:
        return sum(1 for result in results if not result.image_url)

    def _get_cached_results(
        self, cache_key: str
    ) -> tuple[list[SearchResult], dict[str, int]] | None:
        cache_record = self.database.get_fresh_search_cache_row(cache_key)
        if cache_record is None:
            logger.info("event=query.cache_miss")
            return None
        payload, image_missing_count, server_filtered_hit_count, playwright_fallback_count = (
            cache_record
        )
        try:
            return _deserialize_results(payload), {
                "image_missing_count": image_missing_count,
                "server_filtered_hit_count": server_filtered_hit_count,
                "playwright_fallback_count": playwright_fallback_count,
            }
        except (TypeError, ValueError, json.JSONDecodeError):
            logger.info("event=query.cache_decode_failed")
            return None

    def _record_cached_results(
        self,
        cache_key: str,
        query: str,
        results: list[SearchResult],
        *,
        image_missing_count: int,
        server_filtered_hit_count: int,
        playwright_fallback_count: int,
    ) -> None:
        self.database.record_search_cache(
            cache_key=cache_key,
            query_text=query,
            result_json=_serialize_results(results),
            result_count=len(results),
            image_missing_count=image_missing_count,
            server_filtered_hit_count=server_filtered_hit_count,
            playwright_fallback_count=playwright_fallback_count,
            ttl_seconds=self.settings.search_cache_ttl_seconds,
        )

    async def _refine_results(
        self,
        query: str,
        results: list[SearchResult],
    ) -> list[SearchResult]:
        if self.refine_results is not None:
            return await self.refine_results(query, results)
        return results

    async def _handle_hybrid_refinement(
        self,
        query: str,
        results: list[SearchResult],
    ) -> list[SearchResult]:
        mode = self.settings.hybrid_ai_mode
        if mode == "off":
            return results

        start = time.perf_counter()
        refined = await self._refine_results(query, results)
        latency_ms = int((time.perf_counter() - start) * 1000)
        if mode in {"shadow", "review"}:
            self._record_ai_refinement_suggestions(
                query,
                results,
                refined,
                mode=mode,
                latency_ms=latency_ms,
            )
            return results

        if mode != "guarded":
            return results

        guarded: list[SearchResult] = []
        for original, suggested in zip(results, refined):
            gate = evaluate_refinement_suggestion(query, original, suggested)
            if suggested.listing_text != original.listing_text:
                self._record_ai_refinement_suggestion(
                    query,
                    len(guarded) + 1,
                    original,
                    suggested,
                    mode=mode,
                    gate=gate,
                    latency_ms=latency_ms,
                )
            guarded.append(suggested if gate.status == "accepted" else original)
        guarded.extend(results[len(guarded) :])
        return unique_latest_listings(guarded)

    def _record_ai_refinement_suggestions(
        self,
        query: str,
        deterministic: list[SearchResult],
        refined: list[SearchResult],
        *,
        mode: str,
        latency_ms: int,
    ) -> None:
        for rank, (original, suggested) in enumerate(
            zip(deterministic, refined),
            start=1,
        ):
            if suggested.listing_text == original.listing_text:
                continue
            gate = evaluate_refinement_suggestion(query, original, suggested)
            self._record_ai_refinement_suggestion(
                query,
                rank,
                original,
                suggested,
                mode=mode,
                gate=gate,
                latency_ms=latency_ms,
            )

    def _record_ai_refinement_suggestion(
        self,
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
            self.database.record_ai_refinement_suggestion(
                query_text=query,
                result_rank=rank,
                mode=mode,
                model=self.settings.openai_model,
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
                "event=query.ai_suggestion_record_failed error_type=%s",
                exc.__class__.__name__,
            )

    def _record_suspicious_results(
        self,
        query: str,
        results: list[SearchResult],
    ) -> None:
        for rank, result in enumerate(results, start=1):
            for issue in detect_suspicious_result(
                listing_text=result.listing_text,
                raw_listing_text=result.raw_listing_text,
            ):
                try:
                    self.database.record_suspicious_result(
                        query_text=query,
                        result_rank=rank,
                        reason=issue.reason,
                        severity=issue.severity,
                        listing_text=result.listing_text,
                        raw_listing_text=result.raw_listing_text,
                        source_url=result.source_url,
                    )
                except Exception as exc:
                    logger.info(
                        "event=query.suspicious_record_failed error_type=%s",
                        exc.__class__.__name__,
                    )


def _to_search_result(query: str, listing: ListingCandidate) -> SearchResult:
    listing_text = extract_relevant_listing_text(query, listing.listing_text)
    return SearchResult(
        listing_text=listing_text,
        seller=listing.seller,
        seller_phone=listing.seller_phone,
        posted_date=listing.posted_date,
        image_url=_product_image_url(
            listing,
            listing_text=listing_text,
            query=query,
        ),
        source_url=listing.source_url,
        raw_listing_text=listing.listing_text,
    )


def _product_image_url(
    listing: ListingCandidate,
    *,
    listing_text: str | None = None,
    query: str = "",
) -> str | None:
    if listing.image_url is None:
        return None

    candidate_text = listing_text or listing.listing_text
    if _looks_like_multi_listing_for_image(listing.listing_text):
        if (
            not _looks_like_multi_listing_for_image(candidate_text)
            and _query_is_color_specific(query)
        ):
            return listing.image_url
        return None
    return listing.image_url


def _looks_like_multi_listing_for_image(listing_text: str) -> bool:
    references = {
        token.casefold()
        for token in PRODUCT_REFERENCE_RE.findall(listing_text)
        if (
            _looks_like_product_reference(token)
            and not _looks_like_bundle_year_reference(token)
        )
    }
    return len(references) > MULTI_LIST_REFERENCE_THRESHOLD


def _looks_like_bundle_year_reference(token: str) -> bool:
    normalized = token.casefold()
    return bool(re.fullmatch(r"[a-z]+\d+/\d{2,4}y?", normalized))


def _query_is_color_specific(query: str) -> bool:
    return bool(_color_descriptors(query))


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


def _should_expand_year_query(query: str, matched_count: int) -> bool:
    return matched_count < 5 and _query_without_year_descriptors(query) is not None


COLOR_DESCRIPTOR_GROUP = {
    "black",
    "blue",
    "champ",
    "champagne",
    "cho",
    "choco",
    "chocolate",
    "gray",
    "green",
    "grey",
    "purple",
    "red",
    "silver",
    "white",
}
CANONICAL_COLOR_DESCRIPTOR_GROUP = canonicalize_descriptor_tokens_as_set(
    COLOR_DESCRIPTOR_GROUP,
)
SERVER_FILTERED_STRICT_DESCRIPTOR_ALIASES = canonicalize_descriptor_tokens_as_set(
    (
        "choco",
        "chocolate",
        "cho",
    )
)
SERVER_FILTERED_ALIAS_EXPANSION_DESCRIPTORS = canonicalize_descriptor_tokens_as_set(
    (
        "panda",
    )
)
SERVER_FILTERED_MATCH_POLICY_COARSE_NO_DESCRIPTOR = "coarse_no_descriptor"
SERVER_FILTERED_MATCH_POLICY_COARSE_PASS_THROUGH_ALIAS = "coarse_pass_through_alias"
SERVER_FILTERED_MATCH_POLICY_COARSE_COLOR_ONLY = "coarse_color_only"
SERVER_FILTERED_MATCH_POLICY_STRICT_NON_COLOR_DESCRIPTOR = "strict_non_color_descriptor"
SERVER_FILTERED_MATCH_POLICY_STRICT_COLOR_ALIAS = "strict_color_alias"
SERVER_FILTERED_IMAGE_BACKED_MISSING_DESCRIPTOR_LIMIT = 6


def _filter_server_filtered_listings(
    query: str,
    listings: list[ListingCandidate],
) -> list[ListingCandidate]:
    query_colors = _color_descriptors(query)
    filtered: list[ListingCandidate] = []
    for listing in listings:
        if is_non_sale_request(listing.listing_text):
            continue
        if query_colors and _has_conflicting_color_descriptor(query_colors, listing.listing_text):
            continue
        filtered.append(listing)

    if not _server_filtered_query_requires_local_matching(query, query_colors):
        return filtered

    matching_query = _server_filtered_matching_query(query, query_colors)
    strict_matches = filter_matching_listings(matching_query, filtered)
    if strict_matches:
        return strict_matches

    return _merge_listing_candidates(
        strict_matches,
        _server_filtered_image_backed_fallback_matches(query, filtered),
    )


def _server_filtered_matching_query(
    query: str,
    query_colors: set[str],
) -> str:
    policy = _server_filtered_query_matching_policy(query, query_colors)
    if policy not in {
        SERVER_FILTERED_MATCH_POLICY_STRICT_NON_COLOR_DESCRIPTOR,
        SERVER_FILTERED_MATCH_POLICY_STRICT_COLOR_ALIAS,
    }:
        return query

    reference_terms, descriptor_tokens = parse_query_terms(query)
    effective_descriptors = [
        token
        for token in descriptor_tokens
        if token not in SERVER_FILTERED_ALIAS_EXPANSION_DESCRIPTORS
    ]
    if len(effective_descriptors) == len(descriptor_tokens):
        return query

    reference_text = " ".join(" ".join(reference_term) for reference_term in reference_terms)
    if effective_descriptors:
        if reference_text:
            return f"{reference_text} {' '.join(effective_descriptors)}"
        return " ".join(effective_descriptors)
    return reference_text


def _server_filtered_image_backed_fallback_matches(
    query: str,
    listings: list[ListingCandidate],
) -> list[ListingCandidate]:
    reference_query = _server_filtered_reference_query(query)
    if not reference_query:
        return []

    relaxed: list[ListingCandidate] = []
    for listing in listings:
        if not listing.image_url:
            continue
        if listing_matches(reference_query, listing.listing_text):
            relaxed.append(listing)
            if len(relaxed) >= SERVER_FILTERED_IMAGE_BACKED_MISSING_DESCRIPTOR_LIMIT:
                break
    return relaxed


def _server_filtered_reference_query(query: str) -> str:
    reference_terms, _ = parse_query_terms(query)
    return " ".join(" ".join(reference_term) for reference_term in reference_terms)


def _server_filtered_query_requires_local_matching(
    query: str,
    query_colors: set[str],
) -> bool:
    return (
        _server_filtered_query_matching_policy(query, query_colors)
        in {
            SERVER_FILTERED_MATCH_POLICY_STRICT_NON_COLOR_DESCRIPTOR,
            SERVER_FILTERED_MATCH_POLICY_STRICT_COLOR_ALIAS,
        }
    )


def _server_filtered_query_matching_policy(
    query: str,
    query_colors: set[str],
) -> str:
    reference_terms, descriptor_tokens = parse_query_terms(query)
    if not descriptor_tokens:
        return SERVER_FILTERED_MATCH_POLICY_COARSE_NO_DESCRIPTOR

    descriptor_set = set(descriptor_tokens)
    descriptor_set_without_alias = (
        descriptor_set
        - SERVER_FILTERED_ALIAS_EXPANSION_DESCRIPTORS
    )

    if descriptor_set_without_alias - query_colors:
        return SERVER_FILTERED_MATCH_POLICY_STRICT_NON_COLOR_DESCRIPTOR

    if descriptor_set_without_alias and not descriptor_set_without_alias - query_colors:
        if not _server_filtered_reference_terms_are_specific(reference_terms):
            return SERVER_FILTERED_MATCH_POLICY_COARSE_COLOR_ONLY
        if descriptor_set_without_alias & SERVER_FILTERED_STRICT_DESCRIPTOR_ALIASES:
            return SERVER_FILTERED_MATCH_POLICY_STRICT_COLOR_ALIAS
        return SERVER_FILTERED_MATCH_POLICY_STRICT_NON_COLOR_DESCRIPTOR

    if descriptor_set & SERVER_FILTERED_ALIAS_EXPANSION_DESCRIPTORS:
        return SERVER_FILTERED_MATCH_POLICY_COARSE_PASS_THROUGH_ALIAS

    if not (descriptor_set & query_colors):
        return SERVER_FILTERED_MATCH_POLICY_STRICT_NON_COLOR_DESCRIPTOR

    if descriptor_set:
        if descriptor_set & SERVER_FILTERED_STRICT_DESCRIPTOR_ALIASES:
            return SERVER_FILTERED_MATCH_POLICY_STRICT_COLOR_ALIAS
    return SERVER_FILTERED_MATCH_POLICY_COARSE_COLOR_ONLY


def _server_filtered_reference_terms_are_specific(
    reference_terms: list[tuple[str, ...]],
) -> bool:
    for reference_term in reference_terms:
        reference = "".join(reference_term)
        if any(character.isalpha() for character in reference):
            return True
        if "/" in reference:
            return True
        if len(reference) >= 5:
            return True
    return False


def _color_descriptors(value: str) -> set[str]:
    return (
        canonicalize_descriptor_tokens_as_set(normalize_text(value).split())
        & CANONICAL_COLOR_DESCRIPTOR_GROUP
    )


def _has_conflicting_color_descriptor(query_colors: set[str], listing_text: str) -> bool:
    listing_colors = _color_descriptors(listing_text)
    return bool(listing_colors and query_colors.isdisjoint(listing_colors))


def _query_without_year_descriptors(query: str) -> str | None:
    parts = query.split()
    filtered = [part for part in parts if not _is_year_descriptor(part)]
    if len(filtered) == len(parts) or not filtered:
        return None
    return " ".join(filtered)


def _is_year_descriptor(value: str) -> bool:
    normalized = normalize_text(value)
    if not re.fullmatch(r"\d{4}", normalized):
        return False
    year = int(normalized)
    return 1900 <= year <= 2099


def _merge_listing_candidates(
    primary: list[ListingCandidate],
    extra: list[ListingCandidate],
) -> list[ListingCandidate]:
    seen = {_listing_candidate_key(listing) for listing in primary}
    merged = list(primary)
    for listing in extra:
        key = _listing_candidate_key(listing)
        if key in seen:
            continue
        seen.add(key)
        merged.append(listing)
    return merged


def _listing_candidate_key(listing: ListingCandidate) -> tuple[str, str, str, str]:
    return (
        normalize_text(listing.listing_text),
        normalize_text(listing.seller or ""),
        normalize_text(listing.posted_date or ""),
        listing.source_url or "",
    )


def _search_cache_key(query: str, settings: Settings) -> str:
    payload = {
        "version": SEARCH_CACHE_VERSION,
        "query": normalize_text(query),
        "watchfacts_url": settings.watchfacts_url,
        "hybrid_ai_mode": settings.hybrid_ai_mode,
        "openai_model": settings.openai_model if settings.hybrid_ai_mode != "off" else "",
        "openai_max_refines": (
            settings.openai_max_refines if settings.hybrid_ai_mode != "off" else 0
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _search_concurrency_semaphore(settings: Settings) -> asyncio.Semaphore | None:
    if settings.runtime_mode != "search":
        return None
    limit = max(1, settings.search_max_concurrent_searches)
    key = f"{settings.db_path.resolve()}:{limit}"
    semaphore = _SEARCH_SEMAPHORES.get(key)
    if semaphore is None:
        semaphore = asyncio.Semaphore(limit)
        _SEARCH_SEMAPHORES[key] = semaphore
    return semaphore


def _serialize_results(results: list[SearchResult]) -> str:
    return json.dumps(
        search_results_to_dicts(results),
        separators=(",", ":"),
    )


def _deserialize_results(payload: str) -> list[SearchResult]:
    data = json.loads(payload)
    if not isinstance(data, list):
        raise ValueError("cached search result payload must be a list")
    return [_search_result_from_dict(item) for item in data]


def _search_result_from_dict(item: object) -> SearchResult:
    if not isinstance(item, dict):
        raise ValueError("cached search result item must be an object")
    similar = item.get("similar_results", ())
    if not isinstance(similar, list):
        raise ValueError("cached similar results must be a list")
    return SearchResult(
        listing_text=str(item.get("listing_text") or ""),
        seller=_optional_str(item.get("seller")),
        posted_date=_optional_str(item.get("posted_date")),
        image_url=_optional_str(item.get("image_url")),
        source_url=_optional_str(item.get("source_url")),
        similar_results=tuple(_search_result_from_dict(value) for value in similar),
        raw_listing_text=_optional_str(item.get("raw_listing_text")),
        seller_phone=_optional_str(item.get("seller_phone")),
    )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None
