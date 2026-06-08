from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque

from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse
from collections.abc import Deque

from app.config import ConfigError, load_search_settings
from app.result_pages import ResultPageConfig, read_result_page_html
from mcp.server.fastmcp import FastMCP

from app.tool_runtime import (
    watchfacts_create_chat_draft_payload,
    watchfacts_get_issue_payload,
    watchfacts_health_payload,
    watchfacts_list_issues_payload,
    watchfacts_report_issue_payload,
    watchfacts_search_payload,
    watchfacts_suspicious_summary_payload,
    watchfacts_update_issue_payload,
)


logger = logging.getLogger(__name__)
RESULT_PAGE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; "
        "img-src https: data:; "
        "script-src 'unsafe-inline' https://static.cloudflareinsights.com; "
        "script-src-elem 'unsafe-inline' https://static.cloudflareinsights.com; "
        "style-src 'unsafe-inline'; "
        "connect-src 'none'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}

RESULT_PAGE_RATE_LIMIT_ENABLED = True
RESULT_PAGE_RATE_LIMIT_MAX_REQUESTS = 60
RESULT_PAGE_RATE_LIMIT_WINDOW_SECONDS = 60
RESULT_PAGE_RATE_LIMIT_BLOCK_SECONDS = 120

_RESULT_PAGE_RATE_LIMIT_LOCK = threading.Lock()
_RESULT_PAGE_RATE_LIMIT_TIMESTAMPS: dict[str, Deque[float]] = defaultdict(deque)
_RESULT_PAGE_RATE_LIMIT_BLOCKED: dict[str, float] = {}


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
    if _is_rate_limited(client_ip):
        return PlainTextResponse("Too Many Requests", status_code=429, headers={"Retry-After": "120"})

    token = request.path_params.get("token", "")
    try:
        settings = load_search_settings()
    except ConfigError as exc:
        logger.warning("event=result_page.config_error error_type=%s", exc.__class__.__name__)
        return PlainTextResponse("Result page unavailable", status_code=404)

    config = ResultPageConfig.from_settings(settings)
    if not config.enabled:
        return PlainTextResponse("Result page not found", status_code=404)

    page = read_result_page_html(
        token,
        config=config,
    )
    if page.status_code == 200 and page.html is not None:
        return HTMLResponse(page.html, headers=RESULT_PAGE_HEADERS)
    if page.status_code == 410:
        return PlainTextResponse("Result page expired", status_code=410)
    return PlainTextResponse("Result page not found", status_code=404)


def _extract_client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _is_rate_limited(client_ip: str) -> bool:
    if not RESULT_PAGE_RATE_LIMIT_ENABLED:
        return False

    now = time.time()
    with _RESULT_PAGE_RATE_LIMIT_LOCK:
        blocked_until = _RESULT_PAGE_RATE_LIMIT_BLOCKED.get(client_ip)
        if blocked_until is not None and now < blocked_until:
            return True

        timestamps = _RESULT_PAGE_RATE_LIMIT_TIMESTAMPS[client_ip]
        cutoff = now - RESULT_PAGE_RATE_LIMIT_WINDOW_SECONDS
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()

        if len(timestamps) >= RESULT_PAGE_RATE_LIMIT_MAX_REQUESTS:
            _RESULT_PAGE_RATE_LIMIT_BLOCKED[client_ip] = now + RESULT_PAGE_RATE_LIMIT_BLOCK_SECONDS
            return True

        timestamps.append(now)
        return False


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
