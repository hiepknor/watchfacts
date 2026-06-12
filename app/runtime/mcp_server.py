from __future__ import annotations

import logging
import threading
import time
import hmac
from collections import defaultdict, deque
from typing import Any, Deque

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse

from app.config import ConfigError, Settings, load_search_settings
from app.db import Database
from app.integrations.openwa_handoff import (
    OpenWAHandoffConfig,
    OpenWAHandoffError,
    create_openwa_chat_draft,
)
from app.results.result_pages import (
    ResultPageConfig,
    read_result_page_action_payload,
    read_result_page_html,
)
from mcp.server.fastmcp import FastMCP

from app.runtime.tool_runtime import (
    watchfacts_create_chat_draft_payload,
    watchfacts_get_issue_payload,
    watchfacts_health_payload,
    watchfacts_list_issues_payload,
    watchfacts_report_issue_payload,
    watchfacts_search_payload,
    watchfacts_suspicious_summary_payload,
    watchfacts_update_issue_payload,
)


logger = logging.getLogger("app.mcp_server")
RESULT_PAGE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; "
        "img-src https: data:; "
        "script-src 'unsafe-inline' https://static.cloudflareinsights.com; "
        "script-src-elem 'unsafe-inline' https://static.cloudflareinsights.com; "
        "style-src 'unsafe-inline'; "
        "connect-src 'self' https://static.cloudflareinsights.com; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}

_RESULT_PAGE_RATE_LIMIT_LOCK = threading.Lock()
_RESULT_PAGE_RATE_LIMIT_TIMESTAMPS: dict[str, Deque[float]] = defaultdict(deque)
_RESULT_PAGE_RATE_LIMIT_BLOCKED: dict[str, float] = {}
VALID_RESULT_PAGE_REPORT_REASONS = {"missing_info", "wrong_result", "other"}


app = FastMCP(
    "watchfacts",
    host="0.0.0.0",
    port=8765,
    streamable_http_path="/mcp",
)


@app.tool(
    name="search",
    description=(
        "Search WatchFacts products. Use offset/next_offset for pagination. "
        "Results include rank, short-lived result_id, seller, seller_phone, "
        "image_url, and source_url."
    ),
)
async def search(
    query: str,
    limit: int = 5,
    offset: int = 0,
    include_similar: bool = True,
) -> dict[str, object]:
    """Search WatchFacts and return a structured payload."""
    return await watchfacts_search_payload(
        query=query,
        limit=limit,
        offset=offset,
        include_similar=include_similar,
        include_raw=False,
    )


@app.tool(
    name="health",
    description="Check WatchFacts search runtime, browser session, database, and OpenWA readiness.",
)
async def health() -> dict[str, object]:
    """Check whether WatchFacts runtime dependencies are ready."""
    return await watchfacts_health_payload()


@app.custom_route("/results/{token}", methods=["GET"], include_in_schema=False)
async def result_page(request: Request):
    client_ip = _extract_client_ip(request)
    try:
        settings = load_search_settings()
    except ConfigError as exc:
        logger.warning(
            "event=result_page.config_error error_type=%s", exc.__class__.__name__
        )
        return PlainTextResponse("Result page unavailable", status_code=404)

    token = request.path_params.get("token", "")

    if _is_rate_limited(client_ip, settings):
        logger.warning(
            "event=result_page.rate_limited ip=%s token=%s retry_after=%s",
            client_ip,
            token,
            settings.result_page_rate_limit_block_seconds,
        )
        return PlainTextResponse(
            "Too Many Requests",
            status_code=429,
            headers={"Retry-After": str(settings.result_page_rate_limit_block_seconds)},
        )

    config = ResultPageConfig.from_settings(settings)
    if not config.enabled:
        logger.warning(
            "event=result_page.disabled token=%s ip=%s",
            token,
            client_ip,
        )
        return PlainTextResponse("Result page not found", status_code=404)

    page = read_result_page_html(
        token,
        config=config,
    )
    if page.status_code == 200 and page.html is not None:
        return HTMLResponse(page.html, headers=RESULT_PAGE_HEADERS)
    if page.status_code == 410:
        logger.warning(
            "event=result_page.expired token=%s ip=%s",
            token,
            client_ip,
        )
        return PlainTextResponse("Result page expired", status_code=410)

    logger.warning(
        "event=result_page.not_found token=%s ip=%s",
        token,
        client_ip,
    )
    return PlainTextResponse("Result page not found", status_code=404)


@app.custom_route(
    "/results/{token}/actions/openwa-draft",
    methods=["POST"],
    include_in_schema=False,
)
async def result_page_openwa_draft_action(request: Request):
    context = await _load_result_page_action_context(request, action="openwa-draft")
    if isinstance(context, JSONResponse):
        return context

    item = context["item"]
    settings = context["settings"]
    draft_payload = _openwa_draft_payload_from_page_item(
        query=str(context["payload"].get("query") or ""),
        item=item,
        watchfacts_url=settings.watchfacts_url,
    )
    try:
        response = await create_openwa_chat_draft(
            OpenWAHandoffConfig.from_settings(settings),
            draft_payload,
        )
    except OpenWAHandoffError:
        logger.warning(
            "event=result_page.openwa_failed token=%s ip=%s",
            context["token"],
            context["client_ip"],
        )
        return _action_error(
            "openwa_unavailable",
            "OpenWA draft creation is unavailable.",
            status_code=503,
        )

    return JSONResponse(
        {
            "ok": True,
            "status": "created",
            "result_id": item.get("result_id"),
            "draft_id": response.draft_id,
            "chat_id": response.chat_id,
            "dashboard_url": response.dashboard_url,
        }
    )


@app.custom_route(
    "/results/{token}/actions/report",
    methods=["POST"],
    include_in_schema=False,
)
async def result_page_report_action(request: Request):
    context = await _load_result_page_action_context(request, action="report")
    if isinstance(context, JSONResponse):
        return context

    body = context["body"]
    reason = _clean_action_text(body.get("reason"))
    if reason not in VALID_RESULT_PAGE_REPORT_REASONS:
        return _action_error(
            "validation_error",
            "reason must be one of: missing_info, wrong_result, other",
            status_code=400,
        )

    item = context["item"]
    payload = context["payload"]
    settings = context["settings"]
    issue_id = Database(settings.db_path).record_result_feedback(
        query_text=str(payload.get("query") or ""),
        result_rank=_int_value(item.get("rank"), fallback=0),
        reason=reason,
        listing_text=str(item.get("listing_text") or ""),
        raw_listing_text=None,
        seller=_optional_action_text(item.get("seller")),
        posted_date=_optional_action_text(item.get("posted_date")),
        source_url=_optional_action_text(item.get("source_url")),
        notes=_optional_action_text(body.get("notes")),
    )
    issue = Database(settings.db_path).get_issue(issue_id, issue_type="feedback")

    return JSONResponse(
        {
            "ok": True,
            "status": "recorded",
            "result_id": item.get("result_id"),
            "issue_ref": f"F{issue_id}",
            "issue": _safe_issue_payload(issue),
        }
    )


def _extract_client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _is_rate_limited(client_ip: str, settings: Settings) -> bool:
    if not settings.result_page_rate_limit_enabled:
        return False

    now = time.time()
    with _RESULT_PAGE_RATE_LIMIT_LOCK:
        blocked_until = _RESULT_PAGE_RATE_LIMIT_BLOCKED.get(client_ip)
        if blocked_until is not None and now < blocked_until:
            return True

        timestamps = _RESULT_PAGE_RATE_LIMIT_TIMESTAMPS[client_ip]
        cutoff = now - settings.result_page_rate_limit_window_seconds
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()

        if len(timestamps) >= settings.result_page_rate_limit_max_requests:
            _RESULT_PAGE_RATE_LIMIT_BLOCKED[client_ip] = (
                now + settings.result_page_rate_limit_block_seconds
            )
            return True

        timestamps.append(now)
        return False


async def _load_result_page_action_context(
    request: Request,
    *,
    action: str,
) -> dict[str, Any] | JSONResponse:
    client_ip = _extract_client_ip(request)
    token = request.path_params.get("token", "")
    try:
        settings = load_search_settings()
    except ConfigError:
        return _action_error("not_found", "Result page action not found.", status_code=404)

    if _is_rate_limited(f"{client_ip}:{token}:{action}", settings):
        return _action_error(
            "rate_limited",
            "Too many result page action requests.",
            status_code=429,
            headers={"Retry-After": str(settings.result_page_rate_limit_block_seconds)},
        )

    config = ResultPageConfig.from_settings(settings)
    if not config.enabled:
        return _action_error("not_found", "Result page action not found.", status_code=404)

    action_page = read_result_page_action_payload(token, config=config)
    if action_page.status_code == 410:
        return _action_error("expired", "Result page expired.", status_code=410)
    if action_page.status_code != 200 or action_page.payload is None:
        return _action_error("not_found", "Result page action not found.", status_code=404)

    try:
        body = await request.json()
    except Exception:
        return _action_error("validation_error", "Request body must be JSON.", status_code=400)
    if not isinstance(body, dict):
        return _action_error("validation_error", "Request body must be an object.", status_code=400)

    action_nonce = _clean_action_text(body.get("action_nonce"))
    if not hmac.compare_digest(action_nonce, action_page.action_nonce or ""):
        return _action_error("invalid_nonce", "Invalid result page action nonce.", status_code=403)

    result_id = _clean_action_text(body.get("result_id"))
    item = _find_result_page_item(action_page.payload, result_id=result_id)
    if item is None:
        return _action_error("invalid_result", "Result was not found on this page.", status_code=400)

    return {
        "body": body,
        "client_ip": client_ip,
        "item": item,
        "payload": action_page.payload,
        "settings": settings,
        "token": token,
    }


def _find_result_page_item(
    payload: dict[str, Any],
    *,
    result_id: str,
) -> dict[str, Any] | None:
    if not result_id:
        return None
    results = payload.get("results")
    if not isinstance(results, list):
        return None
    for item in results:
        if isinstance(item, dict) and item.get("result_id") == result_id:
            return item
    return None


def _openwa_draft_payload_from_page_item(
    *,
    query: str,
    item: dict[str, Any],
    watchfacts_url: str,
) -> dict[str, Any]:
    listing_text = _optional_action_text(item.get("listing_text"))
    return {
        "source": "watchfacts",
        "sourceResultId": _optional_action_text(item.get("result_id")),
        "sourceUrl": _absolute_action_url(item.get("source_url"), watchfacts_url),
        "queryText": _optional_action_text(query),
        "listingText": listing_text,
        "rawListingText": None,
        "seller": {
            "name": _optional_action_text(item.get("seller")),
            "phone": _optional_action_text(item.get("seller_phone")),
            "watchfactsId": None,
            "profileUrl": None,
        },
        "product": {
            "title": listing_text,
            "reference": None,
            "brand": None,
            "year": None,
            "condition": None,
            "set": None,
            "dial": None,
            "priceText": None,
            "imageUrl": _absolute_action_url(item.get("image_url"), watchfacts_url),
        },
        "origin": {
            "telegramUserId": None,
            "telegramUsername": None,
            "telegramChatId": None,
            "telegramMessageId": None,
        },
    }


def _absolute_action_url(value: object, watchfacts_url: str) -> str | None:
    raw = _optional_action_text(value)
    if raw is None:
        return None
    if raw.startswith(("http://", "https://")):
        return raw
    from urllib.parse import urljoin

    return urljoin(watchfacts_url, raw)


def _safe_issue_payload(issue) -> dict[str, object] | None:
    if issue is None:
        return None
    return {
        "id": issue.id,
        "issue_type": issue.issue_type,
        "status": issue.issue_status,
        "reason": issue.reason,
    }


def _action_error(
    error: str,
    message: str,
    *,
    status_code: int,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": error, "message": message},
        status_code=status_code,
        headers=headers,
    )


def _clean_action_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _optional_action_text(value: object) -> str | None:
    cleaned = _clean_action_text(value)
    return cleaned or None


def _int_value(value: object, *, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


@app.tool(
    name="create_chat_draft",
    description=(
        "Create an OpenWA chat draft for a WatchFacts search result. "
        "Pass the short-lived result_id from search when available, or pass rank "
        "when the user says a result number such as 'ket qua 20'. Do not use "
        "terminal/docker for this."
    ),
)
async def create_chat_draft(
    query: str,
    result_id: str | None = None,
    rank: int | None = None,
) -> dict[str, object]:
    """Create an OpenWA chat draft from a prior search result."""
    return await watchfacts_create_chat_draft_payload(
        query=query,
        result_id=result_id,
        rank=rank,
    )


@app.tool(
    name="report_issue",
    description=(
        "Report a missing-info or wrong-result issue for a WatchFacts search result. "
        "Pass the short-lived result_id from search when available, or pass rank "
        "when the user refers to a result number."
    ),
)
async def report_issue(
    query: str,
    reason: str,
    result_id: str | None = None,
    rank: int | None = None,
    notes: str | None = None,
) -> dict[str, object]:
    """Record result feedback for owner review."""
    return await watchfacts_report_issue_payload(
        query=query,
        result_id=result_id,
        rank=rank,
        reason=reason,
        notes=notes,
    )


@app.tool(
    name="list_issues",
    description="List WatchFacts feedback and suspicious QA issues by status.",
)
def list_issues(
    issue_type: str = "all",
    limit: int = 20,
    min_severity: int | None = None,
    status: str = "open",
) -> dict[str, object]:
    """List WatchFacts issue queue items."""
    return watchfacts_list_issues_payload(
        issue_type=issue_type,
        limit=limit,
        min_severity=min_severity,
        status=status,
    )


@app.tool(
    name="get_issue",
    description="Get one WatchFacts issue by reference such as F1 or S1.",
)
def get_issue(
    issue_ref: str,
    issue_type: str | None = None,
    include_raw_context: bool = True,
) -> dict[str, object]:
    """Get one feedback or suspicious issue."""
    return watchfacts_get_issue_payload(
        issue_ref=issue_ref,
        issue_type=issue_type,
        include_raw_context=include_raw_context,
    )


@app.tool(
    name="update_issue",
    description="Mark a WatchFacts issue as open, fixed, or ignored.",
)
def update_issue(
    issue_ref: str,
    status: str,
    notes: str | None = None,
    issue_type: str | None = None,
) -> dict[str, object]:
    """Update feedback or suspicious issue status."""
    return watchfacts_update_issue_payload(
        issue_ref=issue_ref,
        status=status,
        notes=notes,
        issue_type=issue_type,
    )


@app.tool(
    name="suspicious_summary",
    description="Summarize open WatchFacts auto-QA suspicious issues.",
)
def suspicious_summary(limit: int = 20) -> dict[str, object]:
    """Summarize suspicious issue backlog by reason and severity."""
    return watchfacts_suspicious_summary_payload(limit=limit)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("starting watchfacts mcp server on http://0.0.0.0:8765/mcp")
    app.run(transport="streamable-http")


if __name__ == "__main__":
    main()
