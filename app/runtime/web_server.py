from __future__ import annotations

import asyncio
import hmac
import logging
import threading
import time
from collections import defaultdict, deque
from typing import Any, Deque

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse

from app.application import IssueTriageUseCase
from app.config import ConfigError, Settings, load_search_settings
from app.infrastructure import check_runtime_readiness
from app.results.result_pages import (
    ResultPageConfig,
    is_valid_result_page_token,
    read_result_page_action_payload,
    read_result_page_html,
)
logger = logging.getLogger("app.web_server")
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
_RESULT_PAGE_RATE_LIMIT_MAX_KEYS = 4096
VALID_RESULT_PAGE_REPORT_REASONS = {"missing_info", "wrong_result", "other"}


app = Starlette()


def route(path: str, *, methods: list[str]):
    """Register a route without relying on Starlette's removed decorator API."""

    def register(endpoint):
        app.add_route(path, endpoint, methods=methods)
        return endpoint

    return register


@route("/livez", methods=["GET"])
async def livez(_request: Request):
    """Report that the HTTP process can serve requests."""
    return JSONResponse({"status": "ok", "service": "watchfacts-web"})


@route("/healthz", methods=["GET"])
@route("/readyz", methods=["GET"])
async def healthz(_request: Request):
    """Report readiness for the result-page HTTP service."""
    try:
        settings = load_search_settings()
        await asyncio.to_thread(check_runtime_readiness, settings)
    except Exception as exc:
        logger.warning(
            "event=web.readiness_failed error_type=%s",
            exc.__class__.__name__,
        )
        return JSONResponse(
            {
                "status": "error",
                "service": "watchfacts-web",
                "error": exc.__class__.__name__,
            },
            status_code=503,
        )
    return JSONResponse({"status": "ok", "service": "watchfacts-web"})


@route("/results/{token}", methods=["GET"])
async def result_page(request: Request):
    client_ip = _extract_client_ip(request)
    token = request.path_params.get("token", "")
    if not is_valid_result_page_token(token):
        return PlainTextResponse("Result page not found", status_code=404)
    try:
        settings = load_search_settings()
    except ConfigError as exc:
        logger.warning(
            "event=result_page.config_error error_type=%s", exc.__class__.__name__
        )
        return PlainTextResponse("Result page unavailable", status_code=404)

    if _is_rate_limited(f"page:{client_ip}", settings):
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

    page = await asyncio.to_thread(
        read_result_page_html,
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


@route(
    "/results/{token}/actions/report",
    methods=["POST"],
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
    issue = await asyncio.to_thread(
        IssueTriageUseCase.from_settings(settings).record_feedback,
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
    issue_id = issue.id if issue is not None else None

    return JSONResponse(
        {
            "ok": True,
            "status": "recorded",
            "result_id": item.get("result_id"),
            "issue_ref": f"F{issue_id}" if issue_id is not None else None,
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


def _is_rate_limited(rate_limit_key: str, settings: Settings) -> bool:
    if not settings.result_page_rate_limit_enabled:
        return False

    now = time.monotonic()
    with _RESULT_PAGE_RATE_LIMIT_LOCK:
        _prune_rate_limit_state(
            now,
            window_seconds=settings.result_page_rate_limit_window_seconds,
        )
        blocked_until = _RESULT_PAGE_RATE_LIMIT_BLOCKED.get(rate_limit_key)
        if blocked_until is not None and now < blocked_until:
            return True

        timestamps = _RESULT_PAGE_RATE_LIMIT_TIMESTAMPS[rate_limit_key]
        cutoff = now - settings.result_page_rate_limit_window_seconds
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()

        if len(timestamps) >= settings.result_page_rate_limit_max_requests:
            _RESULT_PAGE_RATE_LIMIT_BLOCKED[rate_limit_key] = (
                now + settings.result_page_rate_limit_block_seconds
            )
            _enforce_rate_limit_capacity()
            return True

        timestamps.append(now)
        _enforce_rate_limit_capacity()
        return False


def _prune_rate_limit_state(now: float, *, window_seconds: int) -> None:
    cutoff = now - window_seconds
    for key, timestamps in list(_RESULT_PAGE_RATE_LIMIT_TIMESTAMPS.items()):
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()
        if not timestamps and _RESULT_PAGE_RATE_LIMIT_BLOCKED.get(key, 0) <= now:
            _RESULT_PAGE_RATE_LIMIT_TIMESTAMPS.pop(key, None)
    for key, blocked_until in list(_RESULT_PAGE_RATE_LIMIT_BLOCKED.items()):
        if blocked_until <= now:
            _RESULT_PAGE_RATE_LIMIT_BLOCKED.pop(key, None)


def _enforce_rate_limit_capacity() -> None:
    while len(_RESULT_PAGE_RATE_LIMIT_TIMESTAMPS) > _RESULT_PAGE_RATE_LIMIT_MAX_KEYS:
        oldest_key = next(iter(_RESULT_PAGE_RATE_LIMIT_TIMESTAMPS))
        _RESULT_PAGE_RATE_LIMIT_TIMESTAMPS.pop(oldest_key, None)
        _RESULT_PAGE_RATE_LIMIT_BLOCKED.pop(oldest_key, None)
    while len(_RESULT_PAGE_RATE_LIMIT_BLOCKED) > _RESULT_PAGE_RATE_LIMIT_MAX_KEYS:
        oldest_key = next(iter(_RESULT_PAGE_RATE_LIMIT_BLOCKED))
        _RESULT_PAGE_RATE_LIMIT_BLOCKED.pop(oldest_key, None)
        _RESULT_PAGE_RATE_LIMIT_TIMESTAMPS.pop(oldest_key, None)


async def _load_result_page_action_context(
    request: Request,
    *,
    action: str,
) -> dict[str, Any] | JSONResponse:
    client_ip = _extract_client_ip(request)
    token = request.path_params.get("token", "")
    if not is_valid_result_page_token(token):
        return _action_error("not_found", "Result page action not found.", status_code=404)
    try:
        settings = load_search_settings()
    except ConfigError:
        return _action_error("not_found", "Result page action not found.", status_code=404)

    if _is_rate_limited(f"action:{client_ip}:{action}", settings):
        return _action_error(
            "rate_limited",
            "Too many result page action requests.",
            status_code=429,
            headers={"Retry-After": str(settings.result_page_rate_limit_block_seconds)},
        )

    config = ResultPageConfig.from_settings(settings)
    if not config.enabled:
        return _action_error("not_found", "Result page action not found.", status_code=404)

    action_page = await asyncio.to_thread(
        read_result_page_action_payload,
        token,
        config=config,
    )
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


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    import uvicorn

    logger.info("starting watchfacts web server on http://0.0.0.0:8766")
    uvicorn.run(app, host="0.0.0.0", port=8766)


if __name__ == "__main__":
    main()
