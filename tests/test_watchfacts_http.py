from __future__ import annotations

import asyncio
import json
import os
import urllib.parse
from pathlib import Path
from typing import Callable

import httpx

from app.config import Settings
from app.scraper import ScrapeResult
from app.watchfacts_http import (
    WatchFactsHttpClientManager,
    WatchFactsHttpClientStatus,
)


def make_settings(tmp_path: Path) -> Settings:
    state_path = tmp_path / "data" / "watchfacts_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{}")
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
        browser_state_path=state_path,
        watchfacts_http_client_enabled=True,
    )


def write_storage_state(
    settings: Settings,
    *,
    cookie_value: str,
    mtime_ns: int | None = None,
) -> None:
    settings.browser_state_path.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "wf_session",
                        "value": cookie_value,
                        "domain": "watchfacts.example",
                        "path": "/",
                    }
                ],
                "origins": [],
            }
        )
    )
    if mtime_ns is not None:
        os.utime(settings.browser_state_path, ns=(mtime_ns, mtime_ns))


def form_response(request: httpx.Request, *, token: str = "csrf-token") -> httpx.Response:
    return httpx.Response(
        200,
        text=(
            '<html><body><form id="mode3Form" action="/simon-search-matches">'
            f'<input name="_token" value="{token}"></form></body></html>'
        ),
        request=request,
    )


def search_response(request: httpx.Request, *, title: str = "5712g") -> httpx.Response:
    return httpx.Response(
        200,
        text=f'{{"listings":[{{"title":"{title}"}}]}}',
        request=request,
    )


def test_watchfacts_http_manager_reuses_async_client_between_searches(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    write_storage_state(settings, cookie_value="first", mtime_ns=100)
    created_clients: list[httpx.AsyncClient] = []
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return form_response(request)
        return search_response(request)

    def client_factory(cookies, timeout, limits) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            cookies=cookies,
            timeout=timeout,
            limits=limits,
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        )
        created_clients.append(client)
        return client

    async def run() -> tuple[ScrapeResult, ScrapeResult]:
        manager = WatchFactsHttpClientManager(client_factory=client_factory)
        try:
            first = await manager.fetch_search(settings, "5712g", timeout_ms=1234)
            second = await manager.fetch_search(settings, "5712r", timeout_ms=1234)
            return first, second
        finally:
            await manager.close_all()

    first, second = asyncio.run(run())

    assert first.server_filtered is True
    assert second.server_filtered is True
    assert len(created_clients) == 1
    assert [request.method for request in requests] == ["GET", "POST", "POST"]
    assert all(client.is_closed for client in created_clients)


def test_watchfacts_http_manager_reloads_cookies_when_state_changes(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    write_storage_state(settings, cookie_value="first", mtime_ns=100)
    created_clients: list[httpx.AsyncClient] = []
    seen_cookies: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            seen_cookies.append(request.headers.get("cookie"))
            return form_response(request)
        return search_response(request)

    def client_factory(cookies, timeout, limits) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            cookies=cookies,
            timeout=timeout,
            limits=limits,
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        )
        created_clients.append(client)
        return client

    async def run() -> None:
        manager = WatchFactsHttpClientManager(client_factory=client_factory)
        try:
            await manager.fetch_search(settings, "5712g", timeout_ms=1234)
            old_client = created_clients[0]
            write_storage_state(settings, cookie_value="second", mtime_ns=200)
            await manager.fetch_search(settings, "5712r", timeout_ms=1234)
            assert old_client.is_closed is True
        finally:
            await manager.close_all()

    asyncio.run(run())

    assert len(created_clients) == 2
    assert seen_cookies == ["wf_session=first", "wf_session=second"]


def test_watchfacts_http_manager_serializes_form_refresh_for_concurrent_searches(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    write_storage_state(settings, cookie_value="first", mtime_ns=100)
    request_methods: list[str] = []
    get_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_count
        request_methods.append(request.method)
        if request.method == "GET":
            get_count += 1
            await asyncio.sleep(0.01)
            return form_response(request)
        return search_response(request)

    def client_factory(cookies, timeout, limits) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            cookies=cookies,
            timeout=timeout,
            limits=limits,
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        )

    async def run() -> None:
        manager = WatchFactsHttpClientManager(client_factory=client_factory)
        try:
            await asyncio.gather(
                manager.fetch_search(settings, "5712g", timeout_ms=1234),
                manager.fetch_search(settings, "5712r", timeout_ms=1234),
            )
        finally:
            await manager.close_all()

    asyncio.run(run())

    assert get_count == 1
    assert request_methods.count("POST") == 2


def test_watchfacts_http_manager_reports_status_without_secrets(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    write_storage_state(settings, cookie_value="first", mtime_ns=100)
    now = 1000.0

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return form_response(request)
        return search_response(request)

    def client_factory(cookies, timeout, limits) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            cookies=cookies,
            timeout=timeout,
            limits=limits,
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        )

    async def run() -> WatchFactsHttpClientStatus:
        nonlocal now
        manager = WatchFactsHttpClientManager(
            client_factory=client_factory,
            now=lambda: now,
        )
        try:
            await manager.fetch_search(settings, "5712g", timeout_ms=1234)
            now += 5
            manager.record_fallback(settings, error_type="timeout")
            return manager.status(settings)
        finally:
            await manager.close_all()

    status = asyncio.run(run())
    payload = status.to_payload()

    assert payload == {
        "enabled": True,
        "form_cache_fresh": True,
        "last_error_type": "timeout",
        "last_success_at": 1000.0,
        "last_fallback_at": 1005.0,
    }
    serialized = json.dumps(payload).casefold()
    assert "cookie" not in serialized
    assert "token" not in serialized
    assert "first" not in serialized


def test_watchfacts_http_manager_uses_configured_timeout_and_limits(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings = Settings(
        **{
            **settings.__dict__,
            "watchfacts_http_connect_timeout_seconds": 7,
            "watchfacts_http_pool_timeout_seconds": 3,
            "watchfacts_http_keepalive_expiry_seconds": 11,
        }
    )
    write_storage_state(settings, cookie_value="first", mtime_ns=100)
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return form_response(request)
        posted = urllib.parse.parse_qs(request.content.decode())
        assert posted["reference"] == ["5712g"]
        return search_response(request)

    def client_factory(cookies, timeout, limits) -> httpx.AsyncClient:
        captured["timeout"] = timeout
        captured["limits"] = limits
        return httpx.AsyncClient(
            cookies=cookies,
            timeout=timeout,
            limits=limits,
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        )

    async def run() -> None:
        manager = WatchFactsHttpClientManager(client_factory=client_factory)
        try:
            await manager.fetch_search(settings, "5712g", timeout_ms=1234)
        finally:
            await manager.close_all()

    asyncio.run(run())

    timeout = captured["timeout"]
    limits = captured["limits"]
    assert isinstance(timeout, httpx.Timeout)
    assert isinstance(limits, httpx.Limits)
    assert timeout.connect == 7
    assert timeout.pool == 3
    assert timeout.write == 30
    assert timeout.read == 90
    assert limits.max_connections == 4
    assert limits.max_keepalive_connections == 2
    assert limits.keepalive_expiry == 11
