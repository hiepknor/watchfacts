from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from app.config import Settings, load_search_settings
from app.db import Database, IssueRecord
from app.openwa_handoff import (
    OpenWAChatDraftResponse,
    OpenWAHandoffConfig,
    create_openwa_chat_draft,
)
from app.scraper import BrowserSessionStatus, check_watchfacts_session
from app.search import WatchFactsSearchWorkflow
from app.search_result import SearchResult, search_result_to_dict
from app.watchfacts_http import WatchFactsHttpClientStatus, watchfacts_http_client_status


OPENWA_MAX_SOURCE_URL_LENGTH = 2048
OPENWA_MAX_QUERY_TEXT_LENGTH = 500
OPENWA_MAX_SELLER_NAME_LENGTH = 255
OPENWA_MAX_PRODUCT_TITLE_LENGTH = 255
RESULT_CACHE_TTL_SECONDS = 30 * 60
VALID_FEEDBACK_REASONS = {"missing_info", "wrong_result", "other"}
VALID_ISSUE_TYPES = {"all", "feedback", "suspicious"}
VALID_ISSUE_STATUSES = {"open", "fixed", "ignored"}


class SearchWorkflow(Protocol):
    async def search(self, query: str) -> list[SearchResult]:
        ...


SessionChecker = Callable[[Settings], Awaitable[BrowserSessionStatus]]
ChatDraftClient = Callable[[dict[str, Any]], Awaitable[OpenWAChatDraftResponse]]
HttpClientStatusProvider = Callable[[Settings], WatchFactsHttpClientStatus]


@dataclass(frozen=True)
class StoredResult:
    query: str
    rank: int
    result: SearchResult
    stored_at: float


_RESULT_CACHE: dict[str, StoredResult] = {}


async def watchfacts_search_payload(
    query: str,
    *,
    limit: int | None = None,
    offset: int = 0,
    include_similar: bool = True,
    include_raw: bool = False,
    settings: Settings | None = None,
    workflow: SearchWorkflow | None = None,
) -> dict[str, object]:
    normalized_query = _require_text(query, "query")
    _validate_limit(limit)
    _validate_offset(offset)

    active_workflow = workflow or WatchFactsSearchWorkflow(
        settings or load_search_settings()
    )
    results = await active_workflow.search(normalized_query)
    _store_results(normalized_query, results)
    visible_results = results[offset : offset + limit] if limit is not None else results[offset:]
    next_offset = offset + len(visible_results)
    has_more = next_offset < len(results)

    return {
        "query": normalized_query,
        "total_count": len(results),
        "offset": offset,
        "limit": limit,
        "result_count": len(visible_results),
        "truncated": offset > 0 or has_more,
        "has_more": has_more,
        "next_offset": next_offset if has_more else None,
        "result_cache_ttl_seconds": RESULT_CACHE_TTL_SECONDS,
        "results": [
            _search_result_payload(
                normalized_query,
                rank,
                result,
                include_similar=include_similar,
                include_raw=include_raw,
            )
            for rank, result in enumerate(visible_results, start=offset + 1)
        ],
    }


async def watchfacts_health_payload(
    *,
    settings: Settings | None = None,
    session_checker: SessionChecker | None = None,
    http_client_status_provider: HttpClientStatusProvider | None = None,
) -> dict[str, object]:
    active_settings = settings or load_search_settings()
    database = Database(active_settings.db_path)
    database_status: dict[str, object]
    try:
        database.initialize()
        database_status = {
            "ok": True,
            "path": str(active_settings.db_path),
        }
    except Exception as exc:
        database_status = {
            "ok": False,
            "error": exc.__class__.__name__,
        }

    checker = session_checker or check_watchfacts_session
    try:
        session = await checker(active_settings)
        session_status = _browser_session_status_payload(session)
    except Exception as exc:
        session_status = {
            "ok": False,
            "status": "error",
            "detail": "WatchFacts session check failed.",
            "error": exc.__class__.__name__,
        }

    openwa_config = OpenWAHandoffConfig.from_settings(active_settings)
    http_status_provider = http_client_status_provider or watchfacts_http_client_status
    http_client_status = http_status_provider(active_settings)
    return {
        "watchfacts_session": session_status,
        "database": database_status,
        "watchfacts_http_client": http_client_status.to_payload(),
        "openwa": {
            "enabled": openwa_config.enabled,
            "ready": openwa_config.is_ready,
        },
        "search_runtime": {
            "ready": bool(database_status["ok"]) and bool(session_status["ok"]),
        },
    }


async def watchfacts_create_chat_draft_payload(
    query: str,
    result_id: str | None = None,
    *,
    rank: int | None = None,
    settings: Settings | None = None,
    workflow: SearchWorkflow | None = None,
    openwa_client: ChatDraftClient | None = None,
) -> dict[str, object]:
    normalized_query = _require_text(query, "query")
    normalized_result_id = _clean_optional_text(result_id)
    active_settings = settings or load_search_settings()
    stored = await _resolve_result_reference(
        normalized_query,
        result_id=normalized_result_id,
        rank=rank,
        settings=active_settings,
        workflow=workflow,
    )
    resolved_result_id = _source_result_id(stored.query, stored.rank, stored.result)

    config = OpenWAHandoffConfig.from_settings(active_settings)
    payload = _build_openwa_chat_draft_payload(
        query=stored.query,
        rank=stored.rank,
        result=stored.result,
        watchfacts_url=active_settings.watchfacts_url,
    )
    client = openwa_client or (
        lambda draft_payload: create_openwa_chat_draft(config, draft_payload)
    )
    response = await client(payload)

    return {
        "status": "created",
        "result_id": resolved_result_id,
        "rank": stored.rank,
        "draft_id": response.draft_id,
        "chat_id": response.chat_id,
        "dashboard_url": response.dashboard_url,
    }


async def watchfacts_report_issue_payload(
    query: str,
    result_id: str | None,
    reason: str,
    *,
    rank: int | None = None,
    notes: str | None = None,
    settings: Settings | None = None,
    workflow: SearchWorkflow | None = None,
    database: Database | None = None,
) -> dict[str, object]:
    normalized_query = _require_text(query, "query")
    normalized_result_id = _clean_optional_text(result_id)
    normalized_reason = _require_text(reason, "reason")
    if normalized_reason not in VALID_FEEDBACK_REASONS:
        raise ValueError("reason must be one of: missing_info, wrong_result, other")

    active_settings = settings or load_search_settings()
    stored = await _resolve_result_reference(
        normalized_query,
        result_id=normalized_result_id,
        rank=rank,
        settings=active_settings,
        workflow=workflow,
    )
    resolved_result_id = _source_result_id(stored.query, stored.rank, stored.result)
    result = stored.result
    issue_database = database or Database(active_settings.db_path)
    issue_id = issue_database.record_result_feedback(
        query_text=stored.query,
        result_rank=stored.rank,
        reason=normalized_reason,
        listing_text=result.listing_text,
        raw_listing_text=result.raw_listing_text,
        seller=result.seller,
        posted_date=result.posted_date,
        source_url=result.source_url,
        notes=_clean_optional_text(notes),
    )
    issue = issue_database.get_issue(issue_id, issue_type="feedback")

    return {
        "status": "recorded",
        "result_id": resolved_result_id,
        "rank": stored.rank,
        "issue": _issue_payload(issue),
    }


def watchfacts_list_issues_payload(
    *,
    issue_type: str = "all",
    limit: int = 10,
    min_severity: int | None = None,
    settings: Settings | None = None,
    database: Database | None = None,
) -> dict[str, object]:
    normalized_issue_type = _validate_issue_type(issue_type)
    _validate_limit(limit)
    if min_severity is not None and min_severity <= 0:
        raise ValueError("min_severity must be a positive integer")

    active_settings = settings or load_search_settings()
    issue_database = database or Database(active_settings.db_path)
    if normalized_issue_type == "feedback":
        issues = issue_database.list_open_feedback_issues(limit=limit)
    elif normalized_issue_type == "suspicious":
        issues = issue_database.list_open_suspicious_issues(
            limit=limit,
            min_severity=min_severity,
        )
    else:
        issues = (
            issue_database.list_open_feedback_issues(limit=limit)
            + issue_database.list_open_suspicious_issues(
                limit=limit,
                min_severity=min_severity,
            )
        )[:limit]

    return {
        "issue_type": normalized_issue_type,
        "result_count": len(issues),
        "issues": [_issue_payload(issue) for issue in issues],
    }


def watchfacts_get_issue_payload(
    issue_ref: str,
    *,
    issue_type: str | None = None,
    settings: Settings | None = None,
    database: Database | None = None,
) -> dict[str, object]:
    parsed_type, issue_id = _parse_issue_ref(issue_ref, issue_type=issue_type)
    active_settings = settings or load_search_settings()
    issue_database = database or Database(active_settings.db_path)
    issue = issue_database.get_issue(issue_id, issue_type=parsed_type)
    return {
        "found": issue is not None,
        "issue": _issue_payload(issue),
    }


def watchfacts_update_issue_payload(
    issue_ref: str,
    status: str,
    *,
    notes: str | None = None,
    issue_type: str | None = None,
    settings: Settings | None = None,
    database: Database | None = None,
) -> dict[str, object]:
    normalized_status = _require_text(status, "status")
    if normalized_status not in VALID_ISSUE_STATUSES:
        raise ValueError("status must be one of: open, fixed, ignored")

    parsed_type, issue_id = _parse_issue_ref(issue_ref, issue_type=issue_type)
    active_settings = settings or load_search_settings()
    issue_database = database or Database(active_settings.db_path)
    issue = issue_database.mark_issue_status(
        issue_id,
        issue_type=parsed_type,
        status=normalized_status,
        notes=_clean_optional_text(notes),
    )
    return {
        "updated": issue is not None,
        "issue": _issue_payload(issue),
    }


def watchfacts_suspicious_summary_payload(
    *,
    limit: int = 20,
    settings: Settings | None = None,
    database: Database | None = None,
) -> dict[str, object]:
    _validate_limit(limit)
    active_settings = settings or load_search_settings()
    issue_database = database or Database(active_settings.db_path)
    summary = issue_database.summarize_open_suspicious_issues(limit=limit)
    return {
        "result_count": len(summary),
        "summary": [
            {
                "reason": item.reason,
                "severity": item.severity,
                "issue_count": item.issue_count,
                "query_count": item.query_count,
                "latest_issue_ref": f"S{item.latest_issue_id}",
                "latest_issue_id": item.latest_issue_id,
                "sample_query": item.sample_query,
            }
            for item in summary
        ],
    }


def _search_result_payload(
    query: str,
    rank: int,
    result: SearchResult,
    *,
    include_similar: bool,
    include_raw: bool,
) -> dict[str, Any]:
    result_id = _source_result_id(query, rank, result)
    payload = search_result_to_dict(
        result,
        include_similar=include_similar,
        include_raw=include_raw,
    )
    payload.update(
        {
            "rank": rank,
            "result_id": result_id,
            "source_result_id": result_id,
        }
    )
    return payload


def _store_results(query: str, results: list[SearchResult]) -> None:
    now = time.monotonic()
    _prune_result_cache(now)
    for rank, result in enumerate(results, start=1):
        result_id = _source_result_id(query, rank, result)
        _RESULT_CACHE[result_id] = StoredResult(
            query=query,
            rank=rank,
            result=result,
            stored_at=now,
        )


async def _resolve_result(
    query: str,
    result_id: str,
    *,
    settings: Settings,
    workflow: SearchWorkflow | None,
) -> StoredResult:
    now = time.monotonic()
    _prune_result_cache(now)
    stored = _RESULT_CACHE.get(result_id)
    if stored is not None and _query_key(stored.query) == _query_key(query):
        return stored

    active_workflow = workflow or WatchFactsSearchWorkflow(settings)
    results = await active_workflow.search(query)
    _store_results(query, results)
    stored = _RESULT_CACHE.get(result_id)
    if stored is None:
        raise ValueError("result_id was not found for query; run search again")
    return stored


async def _resolve_result_reference(
    query: str,
    *,
    result_id: str | None,
    rank: int | None,
    settings: Settings,
    workflow: SearchWorkflow | None,
) -> StoredResult:
    if result_id is not None:
        return await _resolve_result(
            query,
            result_id,
            settings=settings,
            workflow=workflow,
        )
    if rank is None:
        raise ValueError("result_id or rank is required")
    return await _resolve_result_by_rank(
        query,
        rank,
        settings=settings,
        workflow=workflow,
    )


async def _resolve_result_by_rank(
    query: str,
    rank: int,
    *,
    settings: Settings,
    workflow: SearchWorkflow | None,
) -> StoredResult:
    _validate_rank(rank)
    now = time.monotonic()
    _prune_result_cache(now)
    stored = _lookup_stored_result_by_rank(query, rank)
    if stored is not None:
        return stored

    active_workflow = workflow or WatchFactsSearchWorkflow(settings)
    results = await active_workflow.search(query)
    _store_results(query, results)
    stored = _lookup_stored_result_by_rank(query, rank)
    if stored is None:
        raise ValueError("rank was not found for query; run search again")
    return stored


def _lookup_stored_result_by_rank(query: str, rank: int) -> StoredResult | None:
    query_key = _query_key(query)
    latest: StoredResult | None = None
    for stored in _RESULT_CACHE.values():
        if stored.rank == rank and _query_key(stored.query) == query_key:
            if latest is None or stored.stored_at >= latest.stored_at:
                latest = stored
    return latest


def _prune_result_cache(now: float) -> None:
    expired = [
        result_id
        for result_id, stored in _RESULT_CACHE.items()
        if now - stored.stored_at > RESULT_CACHE_TTL_SECONDS
    ]
    for result_id in expired:
        _RESULT_CACHE.pop(result_id, None)


def _source_result_id(query: str, rank: int, result: SearchResult) -> str:
    payload = {
        "query": query,
        "rank": rank,
        "listingText": result.listing_text,
        "rawListingText": result.raw_listing_text,
        "sourceUrl": result.source_url,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"watchfacts:{digest[:24]}"


def _build_openwa_chat_draft_payload(
    *,
    query: str,
    rank: int,
    result: SearchResult,
    watchfacts_url: str | None,
) -> dict[str, Any]:
    return {
        "source": "watchfacts",
        "sourceResultId": _source_result_id(query, rank, result),
        "sourceUrl": _openwa_url(result.source_url, watchfacts_url),
        "queryText": _openwa_text(query, max_length=OPENWA_MAX_QUERY_TEXT_LENGTH),
        "listingText": result.listing_text,
        "rawListingText": result.raw_listing_text,
        "seller": {
            "name": _openwa_text(result.seller, max_length=OPENWA_MAX_SELLER_NAME_LENGTH),
            "phone": _openwa_phone(result.seller_phone),
            "watchfactsId": None,
            "profileUrl": None,
        },
        "product": {
            "title": _openwa_text(
                result.listing_text,
                max_length=OPENWA_MAX_PRODUCT_TITLE_LENGTH,
            ),
            "reference": None,
            "brand": None,
            "year": None,
            "condition": None,
            "set": None,
            "dial": None,
            "priceText": None,
            "imageUrl": _openwa_url(result.image_url, watchfacts_url),
        },
        "origin": {
            "telegramUserId": None,
            "telegramUsername": None,
            "telegramChatId": None,
            "telegramMessageId": None,
        },
    }


def _openwa_text(value: str | None, *, max_length: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized[:max_length]


def _openwa_url(value: str | None, watchfacts_url: str | None) -> str | None:
    raw_value = _openwa_text(value, max_length=OPENWA_MAX_SOURCE_URL_LENGTH)
    if raw_value is None:
        return None

    candidate = raw_value
    parsed = urllib.parse.urlparse(candidate)
    if not (parsed.scheme in {"http", "https"} and parsed.netloc):
        base_url = (watchfacts_url or "").strip()
        candidate = urllib.parse.urljoin(base_url, candidate)

    parsed_candidate = urllib.parse.urlparse(candidate)
    if parsed_candidate.scheme not in {"http", "https"} or not parsed_candidate.netloc:
        return None
    return candidate[:OPENWA_MAX_SOURCE_URL_LENGTH]


def _openwa_phone(value: str | None) -> str | None:
    if value is None:
        return None
    digits = "".join(character for character in value if character.isdigit())
    if len(digits) < 8 or len(digits) > 15 or digits.startswith("0"):
        return None
    return digits


def _browser_session_status_payload(status: BrowserSessionStatus) -> dict[str, object]:
    return {
        "ok": status.ok,
        "status": status.status,
        "detail": status.detail,
    }


def _issue_payload(issue: IssueRecord | None) -> dict[str, object] | None:
    if issue is None:
        return None
    return {
        "id": issue.id,
        "issue_ref": _issue_ref(issue.issue_type, issue.id),
        "type": issue.issue_type,
        "query": issue.query_text,
        "result_rank": issue.result_rank,
        "reason": issue.reason,
        "listing_text": issue.listing_text,
        "raw_listing_text": issue.raw_listing_text,
        "seller": issue.seller,
        "posted_date": issue.posted_date,
        "source_url": issue.source_url,
        "report_count": issue.report_count,
        "severity": issue.severity,
        "status": issue.issue_status,
    }


def _issue_ref(issue_type: str, issue_id: int) -> str:
    return f"{'F' if issue_type == 'feedback' else 'S'}{issue_id}"


def _parse_issue_ref(
    issue_ref: str,
    *,
    issue_type: str | None = None,
) -> tuple[str | None, int]:
    raw_ref = _require_text(issue_ref, "issue_ref")
    parsed_issue_type = _validate_issue_type(issue_type) if issue_type else None
    if parsed_issue_type == "all":
        parsed_issue_type = None

    prefix = raw_ref[:1].upper()
    raw_id = raw_ref
    if prefix in {"F", "S"}:
        parsed_issue_type = "feedback" if prefix == "F" else "suspicious"
        raw_id = raw_ref[1:]

    try:
        issue_id = int(raw_id)
    except ValueError as exc:
        raise ValueError("issue_ref must be like F1, S1, or a numeric id") from exc
    if issue_id <= 0:
        raise ValueError("issue id must be a positive integer")
    return parsed_issue_type, issue_id


def _validate_issue_type(issue_type: str) -> str:
    normalized = _require_text(issue_type, "issue_type")
    if normalized not in VALID_ISSUE_TYPES:
        raise ValueError("issue_type must be one of: all, feedback, suspicious")
    return normalized


def _validate_limit(limit: int | None) -> None:
    if limit is not None and limit <= 0:
        raise ValueError("limit must be a positive integer")


def _validate_offset(offset: int) -> None:
    if offset < 0:
        raise ValueError("offset must not be negative")


def _validate_rank(rank: int) -> None:
    if rank <= 0:
        raise ValueError("rank must be a positive integer")


def _require_text(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _query_key(value: str) -> str:
    return " ".join(value.casefold().split())
