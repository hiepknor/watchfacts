from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from app.config import Settings
from app.db import Database
from app.infrastructure import (
    AiSuggestionRepository,
    IssueRepository,
    SearchCacheRepository,
)
from app.searching.dedupe import latest_dedupe_key, unique_latest_by_text, unique_latest_listings
from app.integrations.ai_refiner import evaluate_refinement_suggestion
from app.searching.fuzzy_diagnostics import score_fuzzy_match
from app.searching.issues import detect_suspicious_result
from app.searching.matcher_token_classification import parse_query_terms
from app.searching.matcher_aliases import canonicalize_descriptor_tokens_as_set
from app.searching.matcher import (
    extract_relevant_listing_text,
    filter_matching_listings,
    is_non_sale_request,
    listing_matches,
    normalize_text,
)
from app.searching.parser import ListingCandidate, parse_listings
from app.searching.query_intent import (
    QueryIntentMetadata,
    QueryPlan,
    build_query_plan,
    classify_query_intent,
)
from app.searching.matcher_rulebook import (
    BRAND_RETRIEVAL_RULES,
    BrandRetrievalRule,
    RETRIEVAL_EXPANSION_RULES,
    RetrievalExpansionRule,
)
from app.searching.result_scoring import (
    descriptor_context_segment_reason_codes,
    price_evidence_reason,
    rank_results_by_quality,
    score_result,
)
from app.integrations.scraper import ScrapeResult, fetch_watchfacts_html
from app.searching.search_result import SearchResult, search_results_to_dicts
from app.searching.similarity import group_similar_results


FetchHtml = Callable[..., Awaitable[ScrapeResult]]
RefineResults = Callable[[str, list[SearchResult]], Awaitable[list[SearchResult]]]
logger = logging.getLogger("app.search")
SEARCH_CACHE_VERSION = "search-v33"
PRODUCT_REFERENCE_RE = re.compile(
    r"\b(?=[A-Za-z0-9/.-]*\d)[A-Za-z0-9]+(?:/[A-Za-z0-9]+)*\b",
    re.IGNORECASE,
)
MULTI_LIST_REFERENCE_THRESHOLD = 1
WATCHFACTS_SOURCE_TRUNCATION_THRESHOLD = 200
_IN_FLIGHT_SEARCHES: dict[str, asyncio.Task[list[SearchResult]]] = {}
_SEARCH_SEMAPHORES: dict[str, asyncio.Semaphore] = {}


@dataclass(frozen=True)
class ImageAttribution:
    image_url: str | None
    reason: str


@dataclass(frozen=True)
class RetrievalTiming:
    query: str
    queue_index: int
    cache_status: str
    fetch_ms: int
    parse_ms: int
    match_ms: int
    total_ms: int
    parsed_count: int
    matched_count: int
    empty: bool
    server_filtered: bool
    playwright_fallback: bool
    unique_result_count: int = 0
    top_result_count: int = 0
    dominant: bool = False
    failed: bool = False
    error_type: str | None = None
    reason_codes: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, object]:
        return {
            "query": self.query,
            "queue_index": self.queue_index,
            "cache_status": self.cache_status,
            "fetch_ms": self.fetch_ms,
            "parse_ms": self.parse_ms,
            "match_ms": self.match_ms,
            "total_ms": self.total_ms,
            "parsed_count": self.parsed_count,
            "matched_count": self.matched_count,
            "unique_result_count": self.unique_result_count,
            "top_result_count": self.top_result_count,
            "empty": self.empty,
            "server_filtered": self.server_filtered,
            "playwright_fallback": self.playwright_fallback,
            "dominant": self.dominant,
            "failed": self.failed,
            "error_type": self.error_type,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class RetrievalFetchResult:
    index: int
    query: str
    fetch_ms: int
    scrape_result: ScrapeResult | None = None
    error_type: str | None = None
    exception: Exception | None = None

    @property
    def failed(self) -> bool:
        return self.exception is not None


@dataclass(frozen=True)
class SearchDiagnostics:
    parsed_count: int | None
    matched_count: int | None
    search_result_count: int | None
    unique_latest_count: int | None
    unique_text_count: int | None
    final_count: int
    server_filtered: bool
    playwright_fallback: bool
    cache_hit: bool
    source_truncation_suspected: bool | None
    raw_candidate_count: int | None = None
    deduped_drop_count: int | None = None
    weak_match_count: int | None = None
    ambiguous_candidate_count: int | None = None
    fuzzy_score_min: int | None = None
    fuzzy_score_avg: float | None = None
    query_intent: str | None = None
    query_plan: QueryPlan | None = None
    retrieval_query_count: int | None = None
    retrieval_queries: tuple[str, ...] = ()
    retrieval_reason_codes: tuple[str, ...] = ()
    required_descriptor_tokens: tuple[str, ...] = ()
    optional_descriptor_tokens: tuple[str, ...] = ()
    intent_reason_codes: tuple[str, ...] = ()
    guardrail_action_counts: dict[str, int] | None = None
    rejection_reasons: dict[str, int] | None = None
    stage_timings_ms: dict[str, int] | None = None
    retrieval_timings: tuple[RetrievalTiming, ...] = ()

    def to_payload(self) -> dict[str, object]:
        return {
            "raw_candidate_count": self.raw_candidate_count,
            "parsed_count": self.parsed_count,
            "matched_count": self.matched_count,
            "search_result_count": self.search_result_count,
            "unique_latest_count": self.unique_latest_count,
            "unique_text_count": self.unique_text_count,
            "deduped_drop_count": self.deduped_drop_count,
            "weak_match_count": self.weak_match_count,
            "ambiguous_candidate_count": self.ambiguous_candidate_count,
            "fuzzy_score_min": self.fuzzy_score_min,
            "fuzzy_score_avg": self.fuzzy_score_avg,
            "query_intent": self.query_intent,
            "query_plan": self.query_plan.to_payload() if self.query_plan else None,
            "retrieval_query_count": self.retrieval_query_count,
            "retrieval_queries": list(self.retrieval_queries),
            "retrieval_reason_codes": list(self.retrieval_reason_codes),
            "required_descriptor_tokens": list(self.required_descriptor_tokens),
            "optional_descriptor_tokens": list(self.optional_descriptor_tokens),
            "intent_reason_codes": list(self.intent_reason_codes),
            "guardrail_action_counts": self.guardrail_action_counts or {},
            "final_count": self.final_count,
            "server_filtered": self.server_filtered,
            "playwright_fallback": self.playwright_fallback,
            "cache_hit": self.cache_hit,
            "source_truncation_suspected": self.source_truncation_suspected,
            "rejection_reasons": self.rejection_reasons or {},
            "stage_timings_ms": self.stage_timings_ms or {},
            "retrieval_timings": [
                timing.to_payload() for timing in self.retrieval_timings
            ],
        }


@dataclass(frozen=True)
class SearchAuditEvent:
    query: str
    stage: str
    candidate_id: str
    rank: int | None = None
    seller: str | None = None
    posted_date: str | None = None
    source_url: str | None = None
    has_image: bool | None = None
    text: str | None = None
    reason_codes: tuple[str, ...] = ()
    decision: str | None = None
    query_intent: str | None = None
    fuzzy_score: int | None = None
    guardrail_action: str | None = None
    stable_audit_id: str | None = None
    kept_audit_id: str | None = None


@dataclass(frozen=True)
class RetrievalPlan:
    fetch_queries: tuple[str, ...]
    local_filter_queries: tuple[str, ...]
    reason_codes: tuple[str, ...]
    fallback_fetch_queries: tuple[str, ...] = ()
    fallback_min_matched_count: int | None = None
    fallback_reason_code: str | None = None
    strict_local_filter: bool = False


def _stage_elapsed_ms(started_at: float) -> int:
    return max(0, int((time.perf_counter() - started_at) * 1000))


def _add_stage_timing(
    stage_timings_ms: dict[str, int],
    stage: str,
    started_at: float,
) -> None:
    _add_stage_timing_value(stage_timings_ms, stage, _stage_elapsed_ms(started_at))


def _add_stage_timing_value(
    stage_timings_ms: dict[str, int],
    stage: str,
    elapsed_ms: int,
) -> None:
    stage_timings_ms[stage] = stage_timings_ms.get(stage, 0) + elapsed_ms


def _mark_dominant_retrieval_timing(
    timings: list[RetrievalTiming],
) -> tuple[RetrievalTiming, ...]:
    if not timings:
        return ()
    max_total_ms = max(timing.total_ms for timing in timings)
    dominant_marked = False
    marked: list[RetrievalTiming] = []
    for timing in timings:
        dominant = not dominant_marked and timing.total_ms == max_total_ms
        if dominant:
            dominant_marked = True
        marked.append(replace(timing, dominant=dominant))
    return tuple(marked)


class WatchFactsSearchWorkflow:
    def __init__(
        self,
        settings: Settings,
        *,
        database: Database | None = None,
        ai_suggestion_repository: AiSuggestionRepository | None = None,
        issue_repository: IssueRepository | None = None,
        search_cache_repository: SearchCacheRepository | None = None,
        fetch_html: FetchHtml | None = None,
        refine_results: RefineResults | None = None,
    ) -> None:
        self.settings = settings
        self.database = database or Database(settings.db_path)
        self.ai_suggestion_repository = ai_suggestion_repository or AiSuggestionRepository(
            self.database
        )
        self.issue_repository = issue_repository or IssueRepository(self.database)
        self.search_cache_repository = search_cache_repository or SearchCacheRepository(
            self.database
        )
        self.fetch_html = fetch_html or fetch_watchfacts_html
        self.refine_results = refine_results
        self.last_search_diagnostics: SearchDiagnostics | None = None
        self.last_search_audit_events: tuple[SearchAuditEvent, ...] = ()

    async def search(self, query: str) -> list[SearchResult]:
        logger.info("event=query.start query_length=%d", len(query))
        search_started_at = time.perf_counter()
        stage_timings_ms: dict[str, int] = {}
        query_intent = classify_query_intent(query)
        query_plan = build_query_plan(query)
        retrieval_plan = _build_retrieval_plan(query, query_plan)
        cache_key = _search_cache_key(query, self.settings)
        in_flight_key = f"{self.settings.db_path.resolve()}:{cache_key}"
        try:
            cache_read_started_at = time.perf_counter()
            cached_results = self._get_cached_results(cache_key)
            _add_stage_timing(stage_timings_ms, "cache_read", cache_read_started_at)
            if cached_results is not None:
                results, cache_metrics = cached_results
                self.last_search_audit_events = ()
                persist_started_at = time.perf_counter()
                self.search_cache_repository.record_query_results(
                    query,
                    results,
                    image_missing_count=cache_metrics["image_missing_count"],
                    server_filtered_hit_count=cache_metrics["server_filtered_hit_count"],
                    playwright_fallback_count=cache_metrics["playwright_fallback_count"],
                )
                _add_stage_timing(stage_timings_ms, "persist", persist_started_at)
                stage_timings_ms["total"] = _stage_elapsed_ms(search_started_at)
                self.last_search_diagnostics = SearchDiagnostics(
                    parsed_count=None,
                    matched_count=None,
                    search_result_count=None,
                    unique_latest_count=None,
                    unique_text_count=None,
                    final_count=len(results),
                    server_filtered=cache_metrics["server_filtered_hit_count"] > 0,
                    playwright_fallback=cache_metrics["playwright_fallback_count"] > 0,
                    cache_hit=True,
                    source_truncation_suspected=None,
                    query_intent=query_intent.kind,
                    query_plan=query_plan,
                    retrieval_query_count=len(retrieval_plan.fetch_queries),
                    retrieval_queries=retrieval_plan.fetch_queries,
                    retrieval_reason_codes=retrieval_plan.reason_codes,
                    required_descriptor_tokens=query_intent.required_descriptor_tokens,
                    optional_descriptor_tokens=query_intent.optional_descriptor_tokens,
                    intent_reason_codes=query_intent.reason_codes,
                    stage_timings_ms=dict(stage_timings_ms),
                )
                logger.info("event=query.cache_hit result_count=%d", len(results))
                return results

            in_flight_wait_started_at: float | None = None
            task = _IN_FLIGHT_SEARCHES.get(in_flight_key)
            if task is None:
                task = asyncio.create_task(
                    self._search_uncached(
                        query,
                        cache_key,
                        stage_timings_ms=stage_timings_ms,
                        search_started_at=search_started_at,
                    )
                )
                _IN_FLIGHT_SEARCHES[in_flight_key] = task
                owner = True
            else:
                owner = False
                in_flight_wait_started_at = time.perf_counter()
                logger.info("event=query.coalesced")

            try:
                results = await task
            finally:
                if owner:
                    _IN_FLIGHT_SEARCHES.pop(in_flight_key, None)

            if not owner:
                if in_flight_wait_started_at is not None:
                    _add_stage_timing(
                        stage_timings_ms,
                        "in_flight_wait",
                        in_flight_wait_started_at,
                    )
                cache_metrics = self.search_cache_repository.get_quality_metrics(cache_key)
                persist_started_at = time.perf_counter()
                self.search_cache_repository.record_query_results(
                    query,
                    results,
                    image_missing_count=self._count_missing_images(results),
                    server_filtered_hit_count=cache_metrics["server_filtered_hit_count"],
                    playwright_fallback_count=cache_metrics["playwright_fallback_count"],
                )
                _add_stage_timing(stage_timings_ms, "persist", persist_started_at)
                if self.last_search_diagnostics is None:
                    self.last_search_audit_events = ()
                    stage_timings_ms["total"] = _stage_elapsed_ms(search_started_at)
                    self.last_search_diagnostics = SearchDiagnostics(
                        parsed_count=None,
                        matched_count=None,
                        search_result_count=None,
                        unique_latest_count=None,
                        unique_text_count=None,
                        final_count=len(results),
                        server_filtered=cache_metrics["server_filtered_hit_count"] > 0,
                        playwright_fallback=cache_metrics["playwright_fallback_count"] > 0,
                        cache_hit=True,
                        source_truncation_suspected=None,
                        query_intent=query_intent.kind,
                        query_plan=query_plan,
                        retrieval_query_count=len(retrieval_plan.fetch_queries),
                        retrieval_queries=retrieval_plan.fetch_queries,
                        retrieval_reason_codes=retrieval_plan.reason_codes,
                        required_descriptor_tokens=query_intent.required_descriptor_tokens,
                        optional_descriptor_tokens=query_intent.optional_descriptor_tokens,
                        intent_reason_codes=query_intent.reason_codes,
                        stage_timings_ms=dict(stage_timings_ms),
                    )
            return results
        except Exception as exc:
            logger.error(
                "event=query.error error_type=%s",
                exc.__class__.__name__,
            )
            raise

    async def _search_uncached(
        self,
        query: str,
        cache_key: str,
        *,
        stage_timings_ms: dict[str, int] | None = None,
        search_started_at: float | None = None,
    ) -> list[SearchResult]:
        semaphore = _search_concurrency_semaphore(self.settings)
        if semaphore is None:
            return await self._search_uncached_inner(
                query,
                cache_key,
                stage_timings_ms=stage_timings_ms,
                search_started_at=search_started_at,
            )
        wait_started_at = time.perf_counter()
        async with semaphore:
            stage_timings_ms = dict(stage_timings_ms or {})
            _add_stage_timing(stage_timings_ms, "concurrency_wait", wait_started_at)
            return await self._search_uncached_inner(
                query,
                cache_key,
                stage_timings_ms=stage_timings_ms,
                search_started_at=search_started_at,
            )

    async def _search_uncached_inner(
        self,
        query: str,
        cache_key: str,
        *,
        stage_timings_ms: dict[str, int] | None = None,
        search_started_at: float | None = None,
    ) -> list[SearchResult]:
        stage_timings_ms = dict(stage_timings_ms or {})
        search_started_at = search_started_at or time.perf_counter()
        query_intent = classify_query_intent(query)
        query_plan = build_query_plan(query)
        retrieval_plan = _build_retrieval_plan(query, query_plan)
        local_filter_queries = retrieval_plan.local_filter_queries
        retrieval_queries = list(retrieval_plan.fetch_queries)
        retrieval_reason_codes = list(retrieval_plan.reason_codes)
        audit_events: list[SearchAuditEvent] = []
        server_filtered_hit_count = 0
        playwright_fallback_count = 0
        parsed_count = 0
        matched: list[ListingCandidate] = []
        weak_match_count = 0
        ambiguous_candidate_count = 0
        retrieval_fetch_error_count = 0
        retrieval_fetch_success_count = 0
        first_fetch_exception: Exception | None = None
        retrieval_timings: list[RetrievalTiming] = []
        candidate_branch_by_key: dict[tuple[str, str, str, str], str] = {}

        async def fetch_and_process_retrieval_branches(
            retrieval_branch_queries: tuple[str, ...],
            *,
            start_index: int,
            reason_codes: tuple[str, ...],
        ) -> None:
            nonlocal ambiguous_candidate_count
            nonlocal first_fetch_exception
            nonlocal matched
            nonlocal parsed_count
            nonlocal playwright_fallback_count
            nonlocal retrieval_fetch_error_count
            nonlocal retrieval_fetch_success_count
            nonlocal server_filtered_hit_count
            nonlocal weak_match_count

            fetch_results = await self._fetch_retrieval_branches(
                retrieval_branch_queries,
                start_index=start_index,
            )
            for fetch_result in fetch_results:
                retrieval_index = fetch_result.index
                retrieval_query = fetch_result.query
                _add_stage_timing_value(
                    stage_timings_ms,
                    "watchfacts_fetch",
                    fetch_result.fetch_ms,
                )
                if fetch_result.failed:
                    retrieval_fetch_error_count += 1
                    first_fetch_exception = (
                        first_fetch_exception or fetch_result.exception
                    )
                    error_type = fetch_result.error_type or "Exception"
                    self._audit_retrieval_fetch_error(
                        audit_events,
                        query=query,
                        query_intent=query_intent,
                        candidate_id=(
                            "raw:1"
                            if retrieval_index == 1
                            else f"raw:{retrieval_index}"
                        ),
                        reason_codes=reason_codes,
                        error_type=error_type,
                    )
                    retrieval_timings.append(
                        RetrievalTiming(
                            query=retrieval_query,
                            queue_index=retrieval_index,
                            cache_status="miss",
                            fetch_ms=fetch_result.fetch_ms,
                            parse_ms=0,
                            match_ms=0,
                            total_ms=fetch_result.fetch_ms,
                            parsed_count=0,
                            matched_count=0,
                            empty=True,
                            server_filtered=False,
                            playwright_fallback=False,
                            failed=True,
                            error_type=error_type,
                            reason_codes=(
                                *reason_codes,
                                f"retrieval.fetch_error:{error_type}",
                            ),
                        )
                    )
                    continue
                scrape_result = fetch_result.scrape_result
                if scrape_result is None:
                    continue
                retrieval_fetch_success_count += 1
                self._audit_raw_scrape(
                    audit_events,
                    query=query,
                    query_intent=query_intent,
                    scrape_result=scrape_result,
                    candidate_id=(
                        "raw:1" if retrieval_index == 1 else f"raw:{retrieval_index}"
                    ),
                    reason_codes=reason_codes,
                )
                server_filtered_hit_count += int(scrape_result.server_filtered)
                playwright_fallback_count += int(scrape_result.used_playwright_fallback)
                parse_started_at = time.perf_counter()
                parsed = parse_listings(scrape_result.html)
                self._audit_listing_candidates(
                    audit_events,
                    query=query,
                    query_intent=query_intent,
                    stage="parsed",
                    listings=parsed,
                    candidate_prefix=(
                        None
                        if retrieval_index == 1
                        else f"retrieval-{retrieval_index}-parsed"
                    ),
                )
                parse_ms = _stage_elapsed_ms(parse_started_at)
                _add_stage_timing_value(stage_timings_ms, "parse", parse_ms)
                match_started_at = time.perf_counter()
                retrieval_matched = _filter_retrieved_listings(
                    local_filter_queries,
                    parsed,
                    server_filtered=scrape_result.server_filtered,
                    strict_local_filter=retrieval_plan.strict_local_filter,
                )
                self._audit_listing_candidates(
                    audit_events,
                    query=query,
                    query_intent=query_intent,
                    stage="matched",
                    listings=retrieval_matched,
                    candidate_prefix=(
                        None
                        if retrieval_index == 1
                        else f"retrieval-{retrieval_index}-matched"
                    ),
                )
                weak_count, ambiguous_count = self._audit_match_confidence(
                    audit_events,
                    query=query,
                    query_intent=query_intent,
                    parsed=parsed,
                    matched=retrieval_matched,
                    candidate_prefix=(
                        "candidate"
                        if retrieval_index == 1
                        else f"retrieval-{retrieval_index}"
                    ),
                )
                weak_match_count += weak_count
                ambiguous_candidate_count += ambiguous_count
                match_ms = _stage_elapsed_ms(match_started_at)
                _add_stage_timing_value(stage_timings_ms, "match", match_ms)
                parsed_count += len(parsed)
                _record_retrieval_contributions(
                    candidate_branch_by_key,
                    retrieval_matched,
                    branch_query=retrieval_query,
                )
                matched = _merge_listing_candidates(matched, retrieval_matched)
                retrieval_timings.append(
                    RetrievalTiming(
                        query=retrieval_query,
                        queue_index=retrieval_index,
                        cache_status="miss",
                        fetch_ms=fetch_result.fetch_ms,
                        parse_ms=parse_ms,
                        match_ms=match_ms,
                        total_ms=fetch_result.fetch_ms + parse_ms + match_ms,
                        parsed_count=len(parsed),
                        matched_count=len(retrieval_matched),
                        empty=not retrieval_matched,
                        server_filtered=scrape_result.server_filtered,
                        playwright_fallback=scrape_result.used_playwright_fallback,
                        reason_codes=reason_codes,
                    )
                )

        await fetch_and_process_retrieval_branches(
            retrieval_plan.fetch_queries,
            start_index=1,
            reason_codes=retrieval_plan.reason_codes,
        )

        if retrieval_fetch_success_count == 0 and first_fetch_exception is not None:
            raise first_fetch_exception

        if retrieval_plan.fallback_fetch_queries:
            fallback_threshold = retrieval_plan.fallback_min_matched_count
            should_fetch_fallback = (
                fallback_threshold is None or len(matched) < fallback_threshold
            )
            if should_fetch_fallback:
                fallback_queries = _dedupe_retrieval_queries(
                    list(retrieval_plan.fallback_fetch_queries),
                    existing=tuple(retrieval_queries),
                )
                if fallback_queries:
                    fallback_reason_codes = _dedupe_strings(
                        [
                            *retrieval_plan.reason_codes,
                            "retrieval.conditional_fallback_fetched",
                            *(
                                [retrieval_plan.fallback_reason_code]
                                if retrieval_plan.fallback_reason_code is not None
                                else []
                            ),
                        ]
                    )
                    retrieval_reason_codes.extend(fallback_reason_codes)
                    start_index = len(retrieval_queries) + 1
                    retrieval_queries.extend(fallback_queries)
                    await fetch_and_process_retrieval_branches(
                        fallback_queries,
                        start_index=start_index,
                        reason_codes=fallback_reason_codes,
                    )
            else:
                retrieval_reason_codes.append("retrieval.conditional_fallback_skipped")

        if (
            len(local_filter_queries) == 1
            and _should_expand_year_query(local_filter_queries[0], len(matched))
        ):
            expanded_query = _query_without_year_descriptors(local_filter_queries[0])
            if expanded_query is not None:
                retrieval_started_at = time.perf_counter()
                fetch_started_at = time.perf_counter()
                expanded_scrape_result = await self.fetch_html(
                    self.settings,
                    query=expanded_query,
                )
                if expanded_query not in retrieval_queries:
                    retrieval_queries.append(expanded_query)
                expanded_queue_index = retrieval_queries.index(expanded_query) + 1
                retrieval_reason_codes.append("retrieval.expand_without_year_descriptor")
                fetch_ms = _stage_elapsed_ms(fetch_started_at)
                _add_stage_timing_value(stage_timings_ms, "watchfacts_fetch", fetch_ms)
                self._audit_raw_scrape(
                    audit_events,
                    query=query,
                    query_intent=query_intent,
                    scrape_result=expanded_scrape_result,
                    candidate_id="raw:expanded",
                    reason_codes=("expanded_year_query",),
                )
                server_filtered_hit_count += int(expanded_scrape_result.server_filtered)
                playwright_fallback_count += int(
                    expanded_scrape_result.used_playwright_fallback
                )
                parse_started_at = time.perf_counter()
                expanded_parsed = parse_listings(expanded_scrape_result.html)
                self._audit_listing_candidates(
                    audit_events,
                    query=query,
                    query_intent=query_intent,
                    stage="parsed",
                    listings=expanded_parsed,
                    candidate_prefix="expanded-parsed",
                )
                parsed_count += len(expanded_parsed)
                parse_ms = _stage_elapsed_ms(parse_started_at)
                _add_stage_timing_value(stage_timings_ms, "parse", parse_ms)
                match_started_at = time.perf_counter()
                expanded_matched = filter_matching_listings(
                    local_filter_queries[0],
                    expanded_parsed,
                )
                self._audit_listing_candidates(
                    audit_events,
                    query=query,
                    query_intent=query_intent,
                    stage="matched",
                    listings=expanded_matched,
                    candidate_prefix="expanded-matched",
                )
                matched = _merge_listing_candidates(matched, expanded_matched)
                expanded_weak_count, expanded_ambiguous_count = (
                    self._audit_match_confidence(
                        audit_events,
                        query=query,
                        query_intent=query_intent,
                        parsed=expanded_parsed,
                        matched=expanded_matched,
                        candidate_prefix="expanded",
                    )
                )
                weak_match_count += expanded_weak_count
                ambiguous_candidate_count += expanded_ambiguous_count
                match_ms = _stage_elapsed_ms(match_started_at)
                _add_stage_timing_value(stage_timings_ms, "match", match_ms)
                _record_retrieval_contributions(
                    candidate_branch_by_key,
                    expanded_matched,
                    branch_query=expanded_query,
                )
                retrieval_timings.append(
                    RetrievalTiming(
                        query=expanded_query,
                        queue_index=expanded_queue_index,
                        cache_status="miss",
                        fetch_ms=fetch_ms,
                        parse_ms=parse_ms,
                        match_ms=match_ms,
                        total_ms=_stage_elapsed_ms(retrieval_started_at),
                        parsed_count=len(expanded_parsed),
                        matched_count=len(expanded_matched),
                        empty=not expanded_matched,
                        server_filtered=expanded_scrape_result.server_filtered,
                        playwright_fallback=(
                            expanded_scrape_result.used_playwright_fallback
                        ),
                        reason_codes=("retrieval.expand_without_year_descriptor",),
                    )
                )
        result_pipeline_started_at = time.perf_counter()
        results: list[SearchResult] = []
        result_branch_by_key: dict[tuple[str, str, str, str], str] = {}
        for listing in matched:
            result = _to_search_result(query, listing)
            results.append(result)
            branch_query = candidate_branch_by_key.get(_listing_candidate_key(listing))
            if branch_query is not None:
                result_branch_by_key[_search_result_branch_key(result)] = branch_query
        self._audit_search_results(
            audit_events,
            query=query,
            query_intent=query_intent,
            stage="converted",
            results=results,
        )
        unique = unique_latest_listings(results)
        latest_drop_count = self._audit_dedupe_drops(
            audit_events,
            query=query,
            before=results,
            after=unique,
            key_for_result=lambda result: latest_dedupe_key(
                result.listing_text,
                seller=result.seller,
            ),
            reason_code="dedupe.latest_listing",
        )
        unique_latest_count = len(unique)
        if self.refine_results is not None and self.settings.hybrid_ai_mode != "off":
            unique = await self._handle_hybrid_refinement(query, unique)
            self._audit_search_results(
                audit_events,
                query=query,
                query_intent=query_intent,
                stage="refined",
                results=unique,
            )
        before_text_dedupe = list(unique)
        unique = unique_latest_by_text(unique)
        text_drop_count = self._audit_dedupe_drops(
            audit_events,
            query=query,
            before=before_text_dedupe,
            after=unique,
            key_for_result=lambda result: normalize_text(result.listing_text),
            reason_code="dedupe.text",
        )
        unique_text_count = len(unique)
        unique = rank_results_by_quality(unique, query=query)
        blocked_final_count = self._audit_and_filter_blocked_final_results(
            audit_events,
            query=query,
            query_intent=query_intent,
            results=unique,
        )
        unique = group_similar_results(unique, query=query)
        fuzzy_scores = [
            score_fuzzy_match(query, result.listing_text).overall_score
            for result in unique
        ]
        self._audit_search_results(
            audit_events,
            query=query,
            query_intent=query_intent,
            stage="final",
            results=unique,
        )
        retrieval_timings = _add_retrieval_result_contribution_counts(
            retrieval_timings,
            unique,
            result_branch_by_key,
        )
        deduped_drop_count = latest_drop_count + text_drop_count
        rejection_reasons = {
            "dedupe.latest_listing": latest_drop_count,
            "dedupe.text": text_drop_count,
            "guardrail.blocked_final": blocked_final_count,
        }
        if retrieval_fetch_error_count > 0:
            rejection_reasons["retrieval.fetch_error"] = retrieval_fetch_error_count
        _add_stage_timing(
            stage_timings_ms,
            "result_pipeline",
            result_pipeline_started_at,
        )
        self.last_search_audit_events = tuple(audit_events)

        persist_started_at = time.perf_counter()
        self.search_cache_repository.record_query_results(
            query,
            unique,
            image_missing_count=self._count_missing_images(unique),
            server_filtered_hit_count=server_filtered_hit_count,
            playwright_fallback_count=playwright_fallback_count,
        )
        self._record_suspicious_results(query, unique)
        if retrieval_fetch_error_count == 0:
            self._record_cached_results(
                cache_key=cache_key,
                query=query,
                results=unique,
                image_missing_count=self._count_missing_images(unique),
                server_filtered_hit_count=server_filtered_hit_count,
                playwright_fallback_count=playwright_fallback_count,
            )
        _add_stage_timing(stage_timings_ms, "persist", persist_started_at)
        stage_timings_ms["total"] = _stage_elapsed_ms(search_started_at)
        self.last_search_diagnostics = SearchDiagnostics(
            parsed_count=parsed_count,
            matched_count=len(matched),
            search_result_count=len(results),
            unique_latest_count=unique_latest_count,
            unique_text_count=unique_text_count,
            final_count=len(unique),
            server_filtered=server_filtered_hit_count > 0,
            playwright_fallback=playwright_fallback_count > 0,
            cache_hit=False,
            source_truncation_suspected=(
                parsed_count >= WATCHFACTS_SOURCE_TRUNCATION_THRESHOLD
            ),
            raw_candidate_count=sum(
                1 for event in audit_events if event.stage == "raw"
            ),
            deduped_drop_count=deduped_drop_count,
            weak_match_count=weak_match_count,
            ambiguous_candidate_count=ambiguous_candidate_count,
            fuzzy_score_min=min(fuzzy_scores) if fuzzy_scores else None,
            fuzzy_score_avg=(
                round(sum(fuzzy_scores) / len(fuzzy_scores), 2)
                if fuzzy_scores
                else None
            ),
            query_intent=query_intent.kind,
            query_plan=query_plan,
            retrieval_query_count=len(retrieval_queries),
            retrieval_queries=tuple(retrieval_queries),
            retrieval_reason_codes=_dedupe_strings(retrieval_reason_codes),
            required_descriptor_tokens=query_intent.required_descriptor_tokens,
            optional_descriptor_tokens=query_intent.optional_descriptor_tokens,
            intent_reason_codes=query_intent.reason_codes,
            guardrail_action_counts=_guardrail_action_counts(audit_events),
            rejection_reasons=rejection_reasons,
            stage_timings_ms=dict(stage_timings_ms),
            retrieval_timings=_mark_dominant_retrieval_timing(retrieval_timings),
        )
        logger.info(
            "event=query.end parsed_count=%d matched_count=%d result_count=%d",
            parsed_count,
            len(matched),
            len(unique),
        )
        return unique

    async def _fetch_retrieval_branches(
        self,
        retrieval_queries: tuple[str, ...],
        *,
        start_index: int = 1,
    ) -> list[RetrievalFetchResult]:
        limit = max(1, self.settings.search_retrieval_concurrency)
        if limit == 1 or len(retrieval_queries) <= 1:
            return [
                await self._fetch_retrieval_branch(
                    index=index,
                    retrieval_query=retrieval_query,
                    capture_errors=False,
                )
                for index, retrieval_query in enumerate(
                    retrieval_queries,
                    start=start_index,
                )
            ]

        semaphore = asyncio.Semaphore(limit)

        async def fetch_with_limit(
            index: int,
            retrieval_query: str,
        ) -> RetrievalFetchResult:
            async with semaphore:
                return await self._fetch_retrieval_branch(
                    index=index,
                    retrieval_query=retrieval_query,
                    capture_errors=True,
                )

        return list(
            await asyncio.gather(
                *(
                    fetch_with_limit(index, retrieval_query)
                    for index, retrieval_query in enumerate(
                        retrieval_queries,
                        start=start_index,
                    )
                )
            )
        )

    async def _fetch_retrieval_branch(
        self,
        *,
        index: int,
        retrieval_query: str,
        capture_errors: bool,
    ) -> RetrievalFetchResult:
        fetch_started_at = time.perf_counter()
        try:
            scrape_result = await self.fetch_html(
                self.settings,
                query=retrieval_query,
            )
        except Exception as exc:
            if not capture_errors:
                raise
            return RetrievalFetchResult(
                index=index,
                query=retrieval_query,
                fetch_ms=_stage_elapsed_ms(fetch_started_at),
                error_type=exc.__class__.__name__,
                exception=exc,
            )
        return RetrievalFetchResult(
            index=index,
            query=retrieval_query,
            fetch_ms=_stage_elapsed_ms(fetch_started_at),
            scrape_result=scrape_result,
        )

    @staticmethod
    def _count_missing_images(results: list[SearchResult]) -> int:
        return sum(1 for result in results if not result.image_url)

    @staticmethod
    def _audit_retrieval_fetch_error(
        audit_events: list[SearchAuditEvent],
        *,
        query: str,
        query_intent: QueryIntentMetadata,
        candidate_id: str,
        reason_codes: tuple[str, ...],
        error_type: str,
    ) -> None:
        audit_events.append(
            SearchAuditEvent(
                query=query,
                stage="raw",
                candidate_id=candidate_id,
                text=f"error_type={error_type}",
                reason_codes=(
                    *reason_codes,
                    f"retrieval.fetch_error:{error_type}",
                ),
                decision="error",
                query_intent=query_intent.kind,
                guardrail_action="none",
                stable_audit_id=_short_hash(f"{query}:{candidate_id}:{error_type}"),
            )
        )

    @staticmethod
    def _audit_raw_scrape(
        audit_events: list[SearchAuditEvent],
        *,
        query: str,
        query_intent: QueryIntentMetadata,
        scrape_result: ScrapeResult,
        candidate_id: str,
        reason_codes: tuple[str, ...] = (),
    ) -> None:
        reasons = [
            *reason_codes,
            "server_filtered" if scrape_result.server_filtered else "client_filtered",
        ]
        if scrape_result.used_playwright_fallback:
            reasons.append("playwright_fallback")
        audit_events.append(
            SearchAuditEvent(
                query=query,
                stage="raw",
                candidate_id=candidate_id,
                source_url=scrape_result.final_url,
                text=f"html_chars={len(scrape_result.html)}",
                reason_codes=tuple(reasons),
                decision="include",
                query_intent=query_intent.kind,
                guardrail_action="none",
                stable_audit_id=_short_hash(
                    f"{query}:{candidate_id}:{scrape_result.final_url}"
                ),
            )
        )

    @staticmethod
    def _audit_listing_candidates(
        audit_events: list[SearchAuditEvent],
        *,
        query: str,
        query_intent: QueryIntentMetadata,
        stage: str,
        listings: list[ListingCandidate],
        candidate_prefix: str | None = None,
    ) -> None:
        prefix = candidate_prefix or stage
        for index, listing in enumerate(listings, start=1):
            audit_events.append(
                SearchAuditEvent(
                    query=query,
                    stage=stage,
                    candidate_id=f"{prefix}:{index}",
                    rank=index,
                    seller=listing.seller,
                    posted_date=listing.posted_date,
                    source_url=listing.source_url,
                    has_image=bool(listing.image_url),
                    text=listing.listing_text,
                    decision="include",
                    query_intent=query_intent.kind,
                    fuzzy_score=score_fuzzy_match(query, listing.listing_text).overall_score,
                    guardrail_action="none",
                    stable_audit_id=_listing_audit_id(listing),
                )
            )

    @staticmethod
    def _audit_search_results(
        audit_events: list[SearchAuditEvent],
        *,
        query: str,
        query_intent: QueryIntentMetadata,
        stage: str,
        results: list[SearchResult],
    ) -> None:
        for index, result in enumerate(results, start=1):
            fuzzy_score = score_fuzzy_match(query, result.listing_text).overall_score
            audit_events.append(
                SearchAuditEvent(
                    query=query,
                    stage=stage,
                    candidate_id=f"{stage}:{index}",
                    rank=index,
                    seller=result.seller,
                    posted_date=result.posted_date,
                    source_url=result.source_url,
                    has_image=bool(result.image_url),
                    text=result.raw_listing_text or result.listing_text,
                    reason_codes=(f"stable_audit_id:{_stable_audit_id(result)}",),
                    decision="include",
                    query_intent=query_intent.kind,
                    fuzzy_score=fuzzy_score,
                    guardrail_action="none",
                    stable_audit_id=_stable_audit_id(result),
                )
            )

    @staticmethod
    def _audit_dedupe_drops(
        audit_events: list[SearchAuditEvent],
        *,
        query: str,
        before: list[SearchResult],
        after: list[SearchResult],
        key_for_result: Callable[[SearchResult], str],
        reason_code: str,
    ) -> int:
        query_intent = classify_query_intent(query)
        kept_result_ids = {id(result) for result in after}
        kept_by_key = {key_for_result(result): result for result in after}
        dropped = 0
        for index, result in enumerate(before, start=1):
            if id(result) in kept_result_ids:
                continue
            kept_result = kept_by_key.get(key_for_result(result))
            stable_audit_id = _stable_audit_id(result)
            kept_audit_id = (
                _stable_audit_id(kept_result) if kept_result is not None else None
            )
            dropped += 1
            audit_events.append(
                SearchAuditEvent(
                    query=query,
                    stage="dedupe_drop",
                    candidate_id=f"dedupe_drop:{reason_code}:{index}",
                    rank=index,
                    seller=result.seller,
                    posted_date=result.posted_date,
                    source_url=result.source_url,
                    has_image=bool(result.image_url),
                    text=result.raw_listing_text or result.listing_text,
                    reason_codes=(
                        reason_code,
                        f"dedupe_key_hash:{_short_hash(key_for_result(result))}",
                        (
                            f"kept_audit_id:{kept_audit_id}"
                            if kept_audit_id is not None
                            else "kept_audit_id:unknown"
                        ),
                    ),
                    decision="deduped",
                    query_intent=query_intent.kind,
                    fuzzy_score=score_fuzzy_match(query, result.listing_text).overall_score,
                    guardrail_action="none",
                    stable_audit_id=stable_audit_id,
                    kept_audit_id=kept_audit_id,
                )
            )
        return dropped

    @staticmethod
    def _audit_and_filter_blocked_final_results(
        audit_events: list[SearchAuditEvent],
        *,
        query: str,
        query_intent: QueryIntentMetadata,
        results: list[SearchResult],
    ) -> int:
        kept: list[SearchResult] = []
        blocked = 0
        for index, result in enumerate(results, start=1):
            score = score_result(result, original_rank=index - 1, query=query)
            if not _should_block_short_model_phrase_miss(result, score.reasons, query):
                kept.append(result)
                continue

            blocked += 1
            fuzzy_score = score_fuzzy_match(query, result.listing_text).overall_score
            audit_events.append(
                SearchAuditEvent(
                    query=query,
                    stage="blocked_final",
                    candidate_id=f"blocked_final:{index}",
                    rank=index,
                    seller=result.seller,
                    posted_date=result.posted_date,
                    source_url=result.source_url,
                    has_image=bool(result.image_url),
                    text=result.raw_listing_text or result.listing_text,
                    reason_codes=(
                        "guardrail.brand_model_phrase_missing",
                        "blocked.short_model_phrase_missing",
                    ),
                    decision="exclude",
                    query_intent=query_intent.kind,
                    fuzzy_score=fuzzy_score,
                    guardrail_action="block_from_final",
                    stable_audit_id=_stable_audit_id(result),
                )
            )

        if blocked:
            results[:] = kept
        return blocked

    @staticmethod
    def _audit_match_confidence(
        audit_events: list[SearchAuditEvent],
        *,
        query: str,
        query_intent: QueryIntentMetadata | None = None,
        parsed: list[ListingCandidate],
        matched: list[ListingCandidate],
        candidate_prefix: str = "confidence",
    ) -> tuple[int, int]:
        intent = query_intent or classify_query_intent(query)
        _, descriptor_tokens = parse_query_terms(query)
        matched_keys = {_listing_candidate_key(listing) for listing in matched}
        weak_count = 0
        ambiguous_count = 0
        for index, listing in enumerate(matched, start=1):
            score = score_fuzzy_match(query, listing.listing_text)
            weak_reasons = _weak_match_reasons(score, descriptor_tokens=descriptor_tokens)
            if not weak_reasons:
                continue
            weak_count += 1
            guardrail_action = _weak_match_guardrail_action(intent, weak_reasons)
            audit_events.append(
                SearchAuditEvent(
                    query=query,
                    stage="weak_match",
                    candidate_id=f"{candidate_prefix}:weak:{index}",
                    rank=index,
                    seller=listing.seller,
                    posted_date=listing.posted_date,
                    source_url=listing.source_url,
                    has_image=bool(listing.image_url),
                    text=listing.listing_text,
                    reason_codes=weak_reasons,
                    decision=(
                        "demote" if guardrail_action == "demote" else "include"
                    ),
                    query_intent=intent.kind,
                    fuzzy_score=score.overall_score,
                    guardrail_action=guardrail_action,
                    stable_audit_id=_listing_audit_id(listing),
                )
            )
        for index, listing in enumerate(parsed, start=1):
            if _listing_candidate_key(listing) in matched_keys:
                continue
            score = score_fuzzy_match(query, listing.listing_text)
            if score.reference_score < 80 or score.overall_score < 60:
                continue
            if descriptor_tokens and score.descriptor_overlap_score < 50:
                continue
            ambiguous_count += 1
            audit_events.append(
                SearchAuditEvent(
                    query=query,
                    stage="ambiguous_candidate",
                    candidate_id=f"{candidate_prefix}:ambiguous:{index}",
                    rank=index,
                    seller=listing.seller,
                    posted_date=listing.posted_date,
                    source_url=listing.source_url,
                    has_image=bool(listing.image_url),
                    text=listing.listing_text,
                    reason_codes=(
                        "ambiguous.not_deterministic_match",
                        f"fuzzy_score:{score.overall_score}",
                        f"reference_score:{score.reference_score}",
                    ),
                    decision="ambiguous",
                    query_intent=intent.kind,
                    fuzzy_score=score.overall_score,
                    guardrail_action="warn",
                    stable_audit_id=_listing_audit_id(listing),
                )
            )
        return weak_count, ambiguous_count

    def _get_cached_results(
        self, cache_key: str
    ) -> tuple[list[SearchResult], dict[str, int]] | None:
        cache_record = self.search_cache_repository.get_fresh_row(cache_key)
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
        self.search_cache_repository.record_cache(
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
            self.ai_suggestion_repository.record_suggestion(
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
                    self.issue_repository.record_suspicious(
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
    image_attribution = attribute_product_image(
        listing,
        listing_text=listing_text,
        query=query,
    )
    result = SearchResult(
        listing_text=listing_text,
        seller=listing.seller,
        seller_phone=listing.seller_phone,
        posted_date=listing.posted_date,
        image_url=image_attribution.image_url,
        source_url=listing.source_url,
        raw_listing_text=listing.raw_listing_text or listing.listing_text,
        scope_reason=_scope_reason_for_listing(listing, listing_text=listing_text),
        image_reason=image_attribution.reason,
        segment_reason_codes=listing.segment_reason_codes,
    )
    segment_reason_codes = _dedupe_strings(
        [
            *result.segment_reason_codes,
            *descriptor_context_segment_reason_codes(query, result),
        ]
    )
    result = replace(result, segment_reason_codes=segment_reason_codes)
    return replace(result, price_reason=price_evidence_reason(result))


def _should_block_short_model_phrase_miss(
    result: SearchResult,
    score_reasons: tuple[str, ...],
    query: str,
) -> bool:
    if "guardrail.brand_model_phrase_missing" not in score_reasons:
        return False
    if not result.raw_listing_text or result.raw_listing_text == result.listing_text:
        return True
    raw_score = score_result(
        SearchResult(result.raw_listing_text),
        original_rank=0,
        query=query,
    )
    if "guardrail.brand_model_phrase_missing" in raw_score.reasons:
        return True
    return not _raw_has_local_short_model_phrase(
        raw_text=result.raw_listing_text,
        candidate_text=result.listing_text,
        query=query,
    )


def _raw_has_local_short_model_phrase(
    *,
    raw_text: str,
    candidate_text: str,
    query: str,
) -> bool:
    intent = classify_query_intent(query)
    numeric_suffixes = tuple(
        token
        for token in intent.required_descriptor_tokens
        if token.isdigit() and len(token) == 1
    )
    model_tokens = tuple(
        token for token in intent.required_descriptor_tokens if not token.isdigit()
    )
    if not numeric_suffixes or not model_tokens:
        return False

    normalized_raw = normalize_text(raw_text)
    normalized_candidate = normalize_text(candidate_text)
    if not normalized_raw or not normalized_candidate:
        return False
    candidate_index = normalized_raw.find(normalized_candidate)
    if candidate_index < 0:
        return False

    candidate_end = candidate_index + len(normalized_candidate)
    for model_token in model_tokens:
        for suffix in numeric_suffixes:
            phrase_re = re.compile(
                rf"\b{re.escape(model_token)}\s+{re.escape(suffix)}\b"
            )
            for match in phrase_re.finditer(normalized_raw):
                if match.start() <= candidate_index <= match.end():
                    return True
                if candidate_index <= match.start() < candidate_end:
                    return True
    return False


def _scope_reason_for_listing(
    listing: ListingCandidate,
    *,
    listing_text: str,
) -> str:
    if listing.scope_reason:
        return listing.scope_reason
    raw_text = listing.raw_listing_text or listing.listing_text
    if normalize_text(raw_text) == normalize_text(listing_text):
        return "scope.full_listing"
    return "scope.scoped"


def attribute_product_image(
    listing: ListingCandidate,
    *,
    listing_text: str | None = None,
    query: str = "",
) -> ImageAttribution:
    if listing.image_url is None:
        return ImageAttribution(None, "image.missing_source")

    candidate_text = listing_text or listing.listing_text
    raw_text = listing.raw_listing_text or listing.listing_text
    if normalize_text(candidate_text) == normalize_text(raw_text):
        return ImageAttribution(listing.image_url, "image.direct")
    if _looks_like_multi_listing_for_image(raw_text):
        if _is_first_scoped_listing_for_image(
            raw_text=raw_text,
            candidate_text=candidate_text,
        ):
            return ImageAttribution(
                listing.image_url,
                "image.inherited_parent_first_item",
            )
        return ImageAttribution(None, "image.omitted_bundle_ambiguous")
    return ImageAttribution(listing.image_url, "image.direct")


def _is_first_scoped_listing_for_image(*, raw_text: str, candidate_text: str) -> bool:
    raw_normalized = normalize_text(raw_text)
    candidate_normalized = normalize_text(candidate_text)
    if not raw_normalized or not candidate_normalized:
        return False
    if raw_normalized == candidate_normalized:
        return False

    candidate_index = raw_normalized.find(candidate_normalized)
    if candidate_index < 0:
        return False

    prefix = raw_normalized[:candidate_index]
    preceding_references = {
        token.casefold()
        for token in PRODUCT_REFERENCE_RE.findall(prefix)
        if (
            _looks_like_product_reference(token)
            and not _looks_like_bundle_year_reference(token)
        )
    }
    return not preceding_references


def _looks_like_multi_listing_for_image(listing_text: str) -> bool:
    references = [
        token.casefold()
        for token in PRODUCT_REFERENCE_RE.findall(listing_text)
        if (
            _looks_like_product_reference(token)
            and not _looks_like_bundle_year_reference(token)
        )
    ]
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


def _build_retrieval_plan(query: str, query_plan: QueryPlan) -> RetrievalPlan:
    expansion_rule = _retrieval_expansion_rule(query_plan)
    if expansion_rule is not None:
        local_filter_queries = _dedupe_strings(
            [
                query,
                *expansion_rule.local_filter_queries,
            ]
        )
        if expansion_rule.fallback_min_matched_count is not None:
            raw_fallback_queries = list(expansion_rule.retrieval_queries)
            fallback_fetch_queries = _dedupe_retrieval_queries(
                raw_fallback_queries,
                existing=(query,),
            )
            reason_codes = [
                "retrieval.raw_query",
                f"retrieval.conditional_fallback:{expansion_rule.collection}",
            ]
            if len(fallback_fetch_queries) < len(raw_fallback_queries):
                reason_codes.append("retrieval.duplicate_branch_skipped")
            return RetrievalPlan(
                fetch_queries=(query,),
                local_filter_queries=local_filter_queries,
                reason_codes=tuple(reason_codes),
                fallback_fetch_queries=fallback_fetch_queries,
                fallback_min_matched_count=expansion_rule.fallback_min_matched_count,
                fallback_reason_code=expansion_rule.reason_code,
                strict_local_filter=True,
            )
        raw_retrieval_queries = [query, *expansion_rule.retrieval_queries]
        retrieval_queries = _dedupe_retrieval_queries(raw_retrieval_queries)
        reason_codes = ["retrieval.raw_query", expansion_rule.reason_code]
        if len(retrieval_queries) < len(raw_retrieval_queries):
            reason_codes.append("retrieval.duplicate_branch_skipped")
        return RetrievalPlan(
            fetch_queries=retrieval_queries,
            local_filter_queries=local_filter_queries,
            reason_codes=tuple(reason_codes),
            strict_local_filter=True,
        )

    brand_retrieval_rule = _brand_retrieval_rule(query_plan)
    if brand_retrieval_rule is not None:
        fallback_fetch_queries = _brand_retrieval_queries(
            query_plan,
            brand_retrieval_rule,
        )
        if fallback_fetch_queries:
            local_filter_queries = _dedupe_strings(
                [
                    query,
                    *fallback_fetch_queries,
                ]
            )
            return RetrievalPlan(
                fetch_queries=(query,),
                local_filter_queries=local_filter_queries,
                reason_codes=(
                    "retrieval.raw_query",
                    f"retrieval.conditional_fallback:{brand_retrieval_rule.brand}",
                ),
                fallback_fetch_queries=fallback_fetch_queries,
                fallback_min_matched_count=brand_retrieval_rule.min_matched_count,
                fallback_reason_code=brand_retrieval_rule.reason_code,
                strict_local_filter=True,
            )

    reference_query = _reference_retrieval_query(query_plan)
    if (
        reference_query
        and query_plan.required_descriptors
        and not query_plan.optional_descriptors
    ):
        return RetrievalPlan(
            fetch_queries=(reference_query,),
            local_filter_queries=(query,),
            reason_codes=("retrieval.reference_with_descriptors",),
        )
    return RetrievalPlan(
        fetch_queries=(query,),
        local_filter_queries=(query,),
        reason_codes=("retrieval.raw_query",),
    )


def _retrieval_expansion_rule(query_plan: QueryPlan) -> RetrievalExpansionRule | None:
    for rule in RETRIEVAL_EXPANSION_RULES:
        if rule.requires_reference_absent and query_plan.references:
            continue
        if rule.requires_optional_descriptor_absent and query_plan.optional_descriptors:
            continue
        if rule.collection not in query_plan.collections:
            continue
        if rule.nickname is not None and rule.nickname not in query_plan.nicknames:
            continue
        if rule.reference_terms and not _query_plan_has_reference_terms(
            query_plan,
            rule.reference_terms,
        ):
            continue
        if rule.required_descriptors and not set(rule.required_descriptors).issubset(
            set(query_plan.required_descriptors)
        ):
            continue
        if rule.required_descriptors and not _retrieval_rule_allows_extra_descriptors(
            query_plan,
            rule,
        ):
            continue
        return rule
    return None


def _retrieval_rule_allows_extra_descriptors(
    query_plan: QueryPlan,
    rule: RetrievalExpansionRule,
) -> bool:
    allowed_extra_descriptors = set(rule.allowed_extra_descriptors)
    base_descriptors = set(rule.required_descriptors) | set(rule.reference_terms)
    extra_descriptors = set(query_plan.required_descriptors) - base_descriptors
    return extra_descriptors.issubset(allowed_extra_descriptors)


def _query_plan_has_reference_terms(
    query_plan: QueryPlan,
    reference_terms: tuple[str, ...],
) -> bool:
    query_references = {"".join(reference) for reference in query_plan.references}
    return set(reference_terms).issubset(query_references)


def _brand_retrieval_rule(
    query_plan: QueryPlan,
) -> BrandRetrievalRule | None:
    candidate_brands = {
        candidate["brand"] for candidate in query_plan.brand_candidates
    }
    for rule in BRAND_RETRIEVAL_RULES:
        if rule.requires_reference_absent and query_plan.references:
            continue
        if rule.requires_optional_descriptor_absent and query_plan.optional_descriptors:
            continue
        if rule.brand not in candidate_brands:
            continue
        return rule
    return None


def _brand_retrieval_queries(
    query_plan: QueryPlan,
    rule: BrandRetrievalRule,
) -> tuple[str, ...]:
    canonical_query = query_plan.canonical_query or query_plan.original_query
    canonical_tokens = tuple(canonical_query.split())
    for token_group in rule.replacement_token_groups:
        replacement_query = _replace_token_group(
            tokens=canonical_tokens,
            replacement_group=token_group,
            replacement_token=rule.replacement_token,
        )
        if replacement_query is not None and replacement_query != canonical_query:
            return (replacement_query,)
    return ()


def _replace_token_group(
    tokens: tuple[str, ...],
    replacement_group: tuple[str, ...],
    replacement_token: str,
) -> str | None:
    if len(replacement_group) > len(tokens):
        return None
    group_length = len(replacement_group)
    for index in range(len(tokens) - group_length + 1):
        if tuple(tokens[index : index + group_length]) != replacement_group:
            continue
        replacement_tokens = (
            tokens[:index]
            + (replacement_token,)
            + tokens[index + group_length :]
        )
        return " ".join(replacement_tokens)
    return None


def _filter_retrieved_listings(
    local_filter_queries: tuple[str, ...],
    listings: list[ListingCandidate],
    *,
    server_filtered: bool,
    strict_local_filter: bool = False,
) -> list[ListingCandidate]:
    matched: list[ListingCandidate] = []
    use_server_filtered_policy = (
        server_filtered and len(local_filter_queries) == 1 and not strict_local_filter
    )
    for local_query in local_filter_queries:
        local_matches = (
            _filter_server_filtered_listings(local_query, listings)
            if use_server_filtered_policy
            else filter_matching_listings(local_query, listings)
        )
        matched = _merge_listing_candidates(matched, local_matches)
    return matched


def _reference_retrieval_query(query_plan: QueryPlan) -> str:
    references = [
        " ".join(reference)
        for reference in query_plan.references
        if any(part for part in reference)
    ]
    return " ".join(references)


def _dedupe_strings(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        deduped.append(value)
        seen.add(value)
    return tuple(deduped)


def _dedupe_retrieval_queries(
    values: list[str],
    *,
    existing: tuple[str, ...] = (),
) -> tuple[str, ...]:
    seen = {_retrieval_query_key(value) for value in existing}
    deduped: list[str] = []
    for value in values:
        key = _retrieval_query_key(value)
        if key in seen:
            continue
        deduped.append(value)
        seen.add(key)
    return tuple(deduped)


def _retrieval_query_key(value: str) -> str:
    return normalize_text(value)


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
    reference_terms, descriptor_tokens = parse_query_terms(query)
    filtered: list[ListingCandidate] = []
    for listing in listings:
        if is_non_sale_request(listing.listing_text):
            continue
        if query_colors and _has_conflicting_color_descriptor(query_colors, listing.listing_text):
            continue
        filtered.append(listing)

    if reference_terms and not descriptor_tokens:
        reference_matches = filter_matching_listings(query, filtered)
        if reference_matches:
            return reference_matches

    if not _server_filtered_query_requires_local_matching(query, query_colors):
        return filtered

    matching_query = _server_filtered_matching_query(query, query_colors)
    strict_matches = filter_matching_listings(matching_query, filtered)
    if strict_matches:
        return strict_matches

    if not _server_filtered_image_backed_fallback_allowed(query):
        return []

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


def _server_filtered_image_backed_fallback_allowed(query: str) -> bool:
    intent = classify_query_intent(query)
    effective_descriptors = {
        token
        for token in intent.required_descriptor_tokens
        if token not in SERVER_FILTERED_ALIAS_EXPANSION_DESCRIPTORS
    }
    return len(effective_descriptors) <= 1


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


def _record_retrieval_contributions(
    branch_by_key: dict[tuple[str, str, str, str], str],
    listings: list[ListingCandidate],
    *,
    branch_query: str,
) -> None:
    for listing in listings:
        key = _listing_candidate_key(listing)
        branch_by_key.setdefault(key, branch_query)


def _listing_candidate_key(listing: ListingCandidate) -> tuple[str, str, str, str]:
    return (
        normalize_text(listing.listing_text),
        normalize_text(listing.seller or ""),
        normalize_text(listing.posted_date or ""),
        listing.source_url or "",
    )


def _search_result_branch_key(result: SearchResult) -> tuple[str, str, str, str]:
    return (
        normalize_text(result.raw_listing_text or result.listing_text),
        normalize_text(result.seller or ""),
        normalize_text(result.posted_date or ""),
        result.source_url or "",
    )


def _add_retrieval_result_contribution_counts(
    timings: list[RetrievalTiming],
    results: list[SearchResult],
    result_branch_by_key: dict[tuple[str, str, str, str], str],
    *,
    top_result_limit: int = 3,
) -> list[RetrievalTiming]:
    if not timings:
        return []

    unique_counts: dict[str, int] = {}
    top_counts: dict[str, int] = {}
    for index, result in enumerate(results):
        branch_query = result_branch_by_key.get(_search_result_branch_key(result))
        if branch_query is None:
            continue
        unique_counts[branch_query] = unique_counts.get(branch_query, 0) + 1
        if index < top_result_limit:
            top_counts[branch_query] = top_counts.get(branch_query, 0) + 1

    return [
        replace(
            timing,
            unique_result_count=unique_counts.get(timing.query, 0),
            top_result_count=top_counts.get(timing.query, 0),
        )
        for timing in timings
    ]


def _search_cache_key(query: str, settings: Settings) -> str:
    payload = {
        "version": SEARCH_CACHE_VERSION,
        "query": _retrieval_cache_query(query),
        "watchfacts_url": settings.watchfacts_url,
        "hybrid_ai_mode": settings.hybrid_ai_mode,
        "openai_model": settings.openai_model if settings.hybrid_ai_mode != "off" else "",
        "openai_max_refines": (
            settings.openai_max_refines if settings.hybrid_ai_mode != "off" else 0
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _retrieval_cache_query(query: str) -> str:
    return build_query_plan(query).canonical_query or normalize_text(query)


def _stable_audit_id(result: SearchResult) -> str:
    payload = {
        "listing_text": result.listing_text,
        "seller": result.seller,
        "posted_date": result.posted_date,
        "source_url": result.source_url,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _listing_audit_id(listing: ListingCandidate) -> str:
    payload = {
        "listing_text": listing.listing_text,
        "seller": listing.seller,
        "posted_date": listing.posted_date,
        "source_url": listing.source_url,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _weak_match_reasons(
    score,
    *,
    descriptor_tokens: list[str],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if score.reference_score < 60:
        reasons.append("weak.reference_score_low")
    if descriptor_tokens and score.descriptor_overlap_score < 100:
        reasons.append("weak.descriptor_overlap_low")
    if score.query_text_score < 45:
        reasons.append("weak.query_text_score_low")
    return tuple(reasons)


def _weak_match_guardrail_action(
    query_intent: QueryIntentMetadata,
    reason_codes: tuple[str, ...],
) -> str:
    if query_intent.kind in {"reference_with_descriptor", "reference_with_year"}:
        if "weak.descriptor_overlap_low" in reason_codes:
            return "demote"
    return "warn"


def _guardrail_action_counts(
    audit_events: list[SearchAuditEvent],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in audit_events:
        action = event.guardrail_action
        if not action or action == "none":
            continue
        counts[action] = counts.get(action, 0) + 1
    return counts


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
        scope_reason=_optional_str(item.get("scope_reason")),
        image_reason=_optional_str(item.get("image_reason")),
        price_reason=_optional_str(item.get("price_reason")),
        segment_reason_codes=_optional_str_tuple(item.get("segment_reason_codes")),
    )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))
