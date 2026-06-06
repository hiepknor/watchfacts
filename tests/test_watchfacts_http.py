from __future__ import annotations

import asyncio
import json
import os
import urllib.parse
from pathlib import Path
from typing import Callable

import httpx
import pytest

from app.config import Settings
from app.scraper import ScrapeResult, ScraperError
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
    monotonic_now = 100.0
    wall_now = 1_700_000_000.0
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return form_response(request)
        post_count += 1
        if post_count == 2:
            return httpx.Response(500, text="temporary failure", request=request)
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
        nonlocal wall_now
        manager = WatchFactsHttpClientManager(
            client_factory=client_factory,
            now=lambda: monotonic_now,
            wall_clock=lambda: wall_now,
        )
        try:
            await manager.fetch_search(settings, "5712g", timeout_ms=1234)
            wall_now += 5
            with pytest.raises(ScraperError, match="HTTP 500"):
                await manager.fetch_search(settings, "5712r", timeout_ms=1234)
            return manager.status(settings)
        finally:
            await manager.close_all()

    status = asyncio.run(run())
    payload = status.to_payload()

    assert payload == {
        "enabled": True,
        "form_cache_fresh": True,
        "last_error_type": "http_status",
        "last_success_at": 1_700_000_000.0,
        "last_failure_at": 1_700_000_005.0,
        "last_fallback_at": None,
        "last_elapsed_ms": 0,
        "last_form_refresh_elapsed_ms": 0,
        "last_post_elapsed_ms": 0,
        "last_http_version": "HTTP/1.1",
        "last_status_code": 500,
        "last_response_bytes": len(b"temporary failure"),
        "last_server_query_changed": False,
        "last_server_query_token_count": 1,
        "consecutive_failures": 1,
        "cooldown_until": 1_700_000_065.0,
    }
    serialized = json.dumps(payload).casefold()
    assert "cookie" not in serialized
    assert "csrf-token" not in serialized
    assert "first" not in serialized


def test_watchfacts_http_manager_uses_configured_timeout_and_limits(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings = Settings(
        **{
            **settings.__dict__,
            "watchfacts_http_connect_timeout_seconds": 7,
            "watchfacts_http_pool_timeout_seconds": 3,
            "watchfacts_http_keepalive_expiry_seconds": 11,
            "watchfacts_http_read_timeout_seconds": 13,
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
    assert timeout.read == 13
    assert limits.max_connections == 4
    assert limits.max_keepalive_connections == 2
    assert limits.keepalive_expiry == 11


def test_watchfacts_http_manager_caps_read_timeout_to_configured_limit(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    settings = Settings(
        **{
            **settings.__dict__,
            "watchfacts_http_read_timeout_seconds": 13,
        }
    )
    write_storage_state(settings, cookie_value="first", mtime_ns=100)
    captured_timeouts: list[httpx.Timeout] = []

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

    async def run() -> None:
        manager = WatchFactsHttpClientManager(client_factory=client_factory)
        try:
            client = await manager._client_for(settings)
            original_request_timeout = client._request_timeout

            def capture_timeout(timeout_ms: int) -> httpx.Timeout:
                timeout = original_request_timeout(timeout_ms)
                captured_timeouts.append(timeout)
                return timeout

            client._request_timeout = capture_timeout
            await manager.fetch_search(settings, "5712g", timeout_ms=60_000)
        finally:
            await manager.close_all()

    asyncio.run(run())

    assert captured_timeouts
    assert {timeout.read for timeout in captured_timeouts} == {13}


def test_watchfacts_http_manager_uses_lower_caller_timeout_when_provided(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    settings = Settings(
        **{
            **settings.__dict__,
            "watchfacts_http_read_timeout_seconds": 13,
        }
    )
    write_storage_state(settings, cookie_value="first", mtime_ns=100)
    captured_timeouts: list[httpx.Timeout] = []

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

    async def run() -> None:
        manager = WatchFactsHttpClientManager(client_factory=client_factory)
        try:
            client = await manager._client_for(settings)
            original_request_timeout = client._request_timeout

            def capture_timeout(timeout_ms: int) -> httpx.Timeout:
                timeout = original_request_timeout(timeout_ms)
                captured_timeouts.append(timeout)
                return timeout

            client._request_timeout = capture_timeout
            await manager.fetch_search(settings, "5712g", timeout_ms=1234)
        finally:
            await manager.close_all()

    asyncio.run(run())

    assert captured_timeouts
    assert {timeout.read for timeout in captured_timeouts} == {1.234}


def test_watchfacts_http_manager_uses_longer_search_timeout_for_post(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    settings = Settings(
        **{
            **settings.__dict__,
            "watchfacts_http_read_timeout_seconds": 13,
            "watchfacts_http_search_read_timeout_seconds": 120,
        }
    )
    write_storage_state(settings, cookie_value="first", mtime_ns=100)
    captured_timeouts: list[tuple[str, httpx.Timeout]] = []

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

    async def run() -> None:
        manager = WatchFactsHttpClientManager(client_factory=client_factory)
        try:
            client = await manager._client_for(settings)
            original_request_timeout = client._request_timeout
            original_search_request_timeout = client._search_request_timeout

            def capture_request_timeout(timeout_ms: int) -> httpx.Timeout:
                timeout = original_request_timeout(timeout_ms)
                captured_timeouts.append(("form", timeout))
                return timeout

            def capture_search_request_timeout(timeout_ms: int) -> httpx.Timeout:
                timeout = original_search_request_timeout(timeout_ms)
                captured_timeouts.append(("search", timeout))
                return timeout

            client._request_timeout = capture_request_timeout
            client._search_request_timeout = capture_search_request_timeout
            await manager.fetch_search(settings, "7118/1200a blue", timeout_ms=30_000)
        finally:
            await manager.close_all()

    asyncio.run(run())

    assert [(kind, timeout.read) for kind, timeout in captured_timeouts] == [
        ("form", 13),
        ("search", 120),
    ]


def test_watchfacts_http_manager_posts_reference_only_query(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    write_storage_state(settings, cookie_value="first", mtime_ns=100)
    posted_references: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return form_response(request)
        posted = urllib.parse.parse_qs(request.content.decode())
        posted_references.extend(posted["reference"])
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
            await manager.fetch_search(
                settings,
                "7118/1200a blue new",
                timeout_ms=30_000,
            )
        finally:
            await manager.close_all()

    asyncio.run(run())

    assert posted_references == ["7118/1200a"]


def test_watchfacts_http_manager_warmup_caches_form_before_first_search(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    write_storage_state(settings, cookie_value="first", mtime_ns=100)
    request_methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_methods.append(request.method)
        if request.method == "GET":
            return form_response(request, token="warm-token")
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
        manager = WatchFactsHttpClientManager(client_factory=client_factory)
        try:
            await manager.warmup(settings, timeout_ms=1234)
            await manager.fetch_search(settings, "5712g", timeout_ms=1234)
            return manager.status(settings)
        finally:
            await manager.close_all()

    status = asyncio.run(run())

    assert request_methods == ["GET", "POST"]
    assert status.form_cache_fresh is True
    assert status.last_form_refresh_elapsed_ms == 0


def test_watchfacts_http_manager_skips_network_during_failure_cooldown(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    settings = Settings(
        **{
            **settings.__dict__,
            "watchfacts_http_failure_cooldown_seconds": 60,
        }
    )
    write_storage_state(settings, cookie_value="first", mtime_ns=100)
    wall_now = 1_700_000_000.0
    request_methods: list[str] = []
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        request_methods.append(request.method)
        if request.method == "GET":
            return form_response(request)
        post_count += 1
        if post_count == 1:
            raise httpx.ReadTimeout("simulated timeout", request=request)
        return search_response(request)

    def client_factory(cookies, timeout, limits) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            cookies=cookies,
            timeout=timeout,
            limits=limits,
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        )

    async def run() -> tuple[WatchFactsHttpClientStatus, WatchFactsHttpClientStatus]:
        nonlocal wall_now
        manager = WatchFactsHttpClientManager(
            client_factory=client_factory,
            wall_clock=lambda: wall_now,
        )
        try:
            with pytest.raises(ScraperError, match="timed out"):
                await manager.fetch_search(settings, "5712g", timeout_ms=1234)
            failed_status = manager.status(settings)

            with pytest.raises(ScraperError, match="cooling down"):
                await manager.fetch_search(settings, "5712r", timeout_ms=1234)
            assert request_methods == ["GET", "POST"]

            wall_now += 61
            await manager.fetch_search(settings, "5712r", timeout_ms=1234)
            return failed_status, manager.status(settings)
        finally:
            await manager.close_all()

    failed_status, recovered_status = asyncio.run(run())

    assert failed_status.last_error_type == "timeout"
    assert failed_status.consecutive_failures == 1
    assert failed_status.cooldown_until == 1_700_000_060.0
    assert recovered_status.last_error_type is None
    assert recovered_status.consecutive_failures == 0
    assert recovered_status.cooldown_until is None


def test_watchfacts_http_manager_recreates_client_when_config_changes(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    write_storage_state(settings, cookie_value="first", mtime_ns=100)
    created_clients: list[httpx.AsyncClient] = []
    captured_connect_timeouts: list[float | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return form_response(request)
        return search_response(request)

    def client_factory(cookies, timeout, limits) -> httpx.AsyncClient:
        captured_connect_timeouts.append(timeout.connect)
        client = httpx.AsyncClient(
            cookies=cookies,
            timeout=timeout,
            limits=limits,
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        )
        created_clients.append(client)
        return client

    updated_settings = Settings(
        **{
            **settings.__dict__,
            "watchfacts_http_connect_timeout_seconds": 13,
        }
    )

    async def run() -> None:
        manager = WatchFactsHttpClientManager(client_factory=client_factory)
        try:
            await manager.fetch_search(settings, "5712g", timeout_ms=1234)
            old_client = created_clients[0]
            await manager.fetch_search(updated_settings, "5712r", timeout_ms=1234)
            assert old_client.is_closed is True
        finally:
            await manager.close_all()

    asyncio.run(run())

    assert len(created_clients) == 2
    assert captured_connect_timeouts == [10, 13]
