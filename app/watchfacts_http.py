from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import Settings
from app.scraper import (
    BrowserSessionError,
    ScrapeResult,
    ScraperError,
    _looks_unauthenticated,
)
from app.watchfacts_forms import (
    cookie_domain_matches_host,
    extract_search_form_fields,
    search_form_data,
    url_host,
)


SEARCH_TIMEOUT_SECONDS = 90
WATCHFACTS_HTTP_WRITE_TIMEOUT_SECONDS = 30
WATCHFACTS_HTTP_MAX_CONNECTIONS = 4
WATCHFACTS_HTTP_MAX_KEEPALIVE_CONNECTIONS = 2
CSRF_RETRY_STATUSES = {401, 403, 419}
HttpxClientFactory = Callable[[httpx.Cookies, httpx.Timeout, httpx.Limits], httpx.AsyncClient]


@dataclass(frozen=True)
class SearchFormCacheEntry:
    action_url: str
    token: str
    fetched_at: float


@dataclass(frozen=True)
class BrowserStateFingerprint:
    path: str
    mtime_ns: int
    size: int


@dataclass(frozen=True)
class WatchFactsHttpClientStatus:
    enabled: bool
    form_cache_fresh: bool
    last_error_type: str | None = None
    last_success_at: float | None = None
    last_fallback_at: float | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "form_cache_fresh": self.form_cache_fresh,
            "last_error_type": self.last_error_type,
            "last_success_at": self.last_success_at,
            "last_fallback_at": self.last_fallback_at,
        }


class WatchFactsHttpClient:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        client_factory: HttpxClientFactory | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._client = client
        self._client_factory = client_factory
        self._owns_client = client is None
        self._now = now
        self._fingerprint: BrowserStateFingerprint | None = None
        self._form_cache: SearchFormCacheEntry | None = None
        self._client_lock = asyncio.Lock()
        self._form_lock = asyncio.Lock()
        self._last_error_type: str | None = None
        self._last_success_at: float | None = None
        self._last_fallback_at: float | None = None

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> "WatchFactsHttpClient":
        return cls(settings, client=client, now=now)

    async def __aenter__(self) -> "WatchFactsHttpClient":
        await self._get_client()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_search(self, query: str, *, timeout_ms: int) -> ScrapeResult:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be empty")

        try:
            form = await self._get_search_form(timeout_ms=timeout_ms, use_cache=True)
            for attempt in range(2):
                response = await self._post_search(
                    form,
                    normalized_query,
                    timeout_ms=timeout_ms,
                )
                if response.status_code in CSRF_RETRY_STATUSES:
                    self._record_error("csrf_refresh")
                    if attempt == 0:
                        self._form_cache = None
                        form = await self._get_search_form(
                            timeout_ms=timeout_ms,
                            use_cache=False,
                        )
                        continue
                    raise _scraper_error(
                        f"WatchFacts search failed with HTTP {response.status_code}",
                        "csrf_refresh_failed",
                    )
                if response.status_code >= 400:
                    raise _scraper_error(
                        f"WatchFacts search failed with HTTP {response.status_code}",
                        "http_status",
                    )

                text = response.text
                final_url = str(response.url)
                if _looks_unauthenticated(final_url, text):
                    self._record_error("auth_expired")
                    if attempt == 0:
                        self._form_cache = None
                        form = await self._get_search_form(
                            timeout_ms=timeout_ms,
                            use_cache=False,
                        )
                        continue
                    raise BrowserSessionError(
                        "Saved browser session appears expired. "
                        "Run `python scripts/ops/login.py` again."
                    )

                self._last_success_at = self._now()
                return ScrapeResult(
                    html=text,
                    final_url=final_url,
                    server_filtered=True,
                )
        except (BrowserSessionError, ScraperError):
            raise
        except httpx.TimeoutException as exc:
            self._record_error("timeout")
            raise _scraper_error("WatchFacts HTTP search timed out", "timeout") from exc
        except httpx.NetworkError as exc:
            self._record_error("network")
            raise _scraper_error(
                f"WatchFacts HTTP search failed: {exc.__class__.__name__}",
                "network",
            ) from exc
        except httpx.HTTPError as exc:
            self._record_error("httpx_error")
            raise _scraper_error(
                f"WatchFacts HTTP search failed: {exc.__class__.__name__}",
                "httpx_error",
            ) from exc

        raise _scraper_error("WatchFacts HTTP search failed", "unknown")

    def record_fallback(self, *, error_type: str) -> None:
        self._last_error_type = error_type
        self._last_fallback_at = self._now()

    def status(self) -> WatchFactsHttpClientStatus:
        return WatchFactsHttpClientStatus(
            enabled=self.settings.watchfacts_http_client_enabled,
            form_cache_fresh=self._form_cache_is_fresh(),
            last_error_type=self._last_error_type,
            last_success_at=self._last_success_at,
            last_fallback_at=self._last_fallback_at,
        )

    async def _get_search_form(
        self,
        *,
        timeout_ms: int,
        use_cache: bool,
    ) -> SearchFormCacheEntry:
        if use_cache and self._form_cache_is_fresh():
            assert self._form_cache is not None
            return self._form_cache

        async with self._form_lock:
            if use_cache and self._form_cache_is_fresh():
                assert self._form_cache is not None
                return self._form_cache

            client = await self._get_client()
            response = await client.get(
                self.settings.watchfacts_url,
                timeout=self._request_timeout(timeout_ms),
            )
            if response.status_code >= 400:
                raise _scraper_error(
                    f"WatchFacts navigation failed with HTTP {response.status_code}",
                    "http_status",
                )
            html = response.text
            final_url = str(response.url)
            if _looks_unauthenticated(final_url, html):
                self._record_error("auth_expired")
                raise BrowserSessionError(
                    "Saved browser session appears expired. "
                    "Run `python scripts/ops/login.py` again."
                )
            try:
                fields = extract_search_form_fields(final_url, html)
            except ValueError as exc:
                self._record_error("cross_origin")
                raise _scraper_error(
                    "WatchFacts search form action is cross-origin",
                    "cross_origin",
                ) from exc
            if fields is None:
                raise _scraper_error(
                    "WatchFacts search form not found in HTTP response",
                    "bad_form",
                )

            self._form_cache = SearchFormCacheEntry(
                action_url=fields.action_url,
                token=fields.token,
                fetched_at=self._now(),
            )
            return self._form_cache

    async def _post_search(
        self,
        form: SearchFormCacheEntry,
        query: str,
        *,
        timeout_ms: int,
    ) -> httpx.Response:
        client = await self._get_client()
        return await client.post(
            form.action_url,
            data=search_form_data(form.token, query),
            headers={"Accept": "application/json, text/plain, */*"},
            timeout=self._request_timeout(timeout_ms),
        )

    async def _get_client(self) -> httpx.AsyncClient:
        fingerprint = self._state_fingerprint()
        if self._client is not None and self._fingerprint == fingerprint:
            return self._client

        async with self._client_lock:
            fingerprint = self._state_fingerprint()
            if self._client is not None and self._fingerprint == fingerprint:
                return self._client

            cookies = self._load_storage_state_cookies()
            if self._client is not None and self._owns_client:
                await self._client.aclose()
            if self._client is None or self._owns_client:
                self._client = self._make_client(cookies)
            else:
                self._client.cookies.update(cookies)
            self._fingerprint = fingerprint
            self._form_cache = None
            return self._client

    def _make_client(self, cookies: httpx.Cookies) -> httpx.AsyncClient:
        timeout = self._default_timeout()
        limits = self._limits()
        if self._client_factory is not None:
            return self._client_factory(cookies, timeout, limits)
        return httpx.AsyncClient(
            cookies=cookies,
            timeout=timeout,
            limits=limits,
            follow_redirects=True,
        )

    def _request_timeout(self, timeout_ms: int) -> httpx.Timeout:
        read_timeout = max(timeout_ms / 1000, SEARCH_TIMEOUT_SECONDS)
        return httpx.Timeout(
            read_timeout,
            connect=self.settings.watchfacts_http_connect_timeout_seconds,
            read=read_timeout,
            write=WATCHFACTS_HTTP_WRITE_TIMEOUT_SECONDS,
            pool=self.settings.watchfacts_http_pool_timeout_seconds,
        )

    def _default_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            SEARCH_TIMEOUT_SECONDS,
            connect=self.settings.watchfacts_http_connect_timeout_seconds,
            read=SEARCH_TIMEOUT_SECONDS,
            write=WATCHFACTS_HTTP_WRITE_TIMEOUT_SECONDS,
            pool=self.settings.watchfacts_http_pool_timeout_seconds,
        )

    def _limits(self) -> httpx.Limits:
        return httpx.Limits(
            max_connections=WATCHFACTS_HTTP_MAX_CONNECTIONS,
            max_keepalive_connections=WATCHFACTS_HTTP_MAX_KEEPALIVE_CONNECTIONS,
            keepalive_expiry=self.settings.watchfacts_http_keepalive_expiry_seconds,
        )

    def _load_storage_state_cookies(self) -> httpx.Cookies:
        state_path = self.settings.browser_state_path
        if not state_path.exists():
            raise BrowserSessionError(
                f"Missing browser session at {state_path}. "
                "Run `python scripts/ops/login.py` first."
            )
        try:
            state = json.loads(state_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise BrowserSessionError(
                f"Invalid browser session at {state_path}. "
                "Run `python scripts/ops/login.py` again."
            ) from exc
        if not isinstance(state, dict):
            raise BrowserSessionError(
                f"Invalid browser session at {state_path}. "
                "Run `python scripts/ops/login.py` again."
            )

        cookies = httpx.Cookies()
        target_host = url_host(self.settings.watchfacts_url)
        for raw_cookie in state.get("cookies", []):
            if not isinstance(raw_cookie, dict):
                continue
            name = raw_cookie.get("name")
            value = raw_cookie.get("value")
            domain = raw_cookie.get("domain")
            path = raw_cookie.get("path", "/")
            if not (
                isinstance(name, str)
                and isinstance(value, str)
                and isinstance(domain, str)
                and isinstance(path, str)
            ):
                continue
            if not cookie_domain_matches_host(domain, target_host):
                continue
            cookies.set(name, value, domain=domain, path=path or "/")
        return cookies

    def _state_fingerprint(self) -> BrowserStateFingerprint:
        state_path = self.settings.browser_state_path
        try:
            stat = state_path.stat()
        except OSError as exc:
            raise BrowserSessionError(
                f"Missing browser session at {state_path}. "
                "Run `python scripts/ops/login.py` first."
            ) from exc
        return BrowserStateFingerprint(
            path=str(state_path.resolve()),
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size,
        )

    def _form_cache_is_fresh(self) -> bool:
        if self._form_cache is None:
            return False
        try:
            if self._fingerprint != self._state_fingerprint():
                return False
        except BrowserSessionError:
            return False
        age_seconds = self._now() - self._form_cache.fetched_at
        return age_seconds < self.settings.watchfacts_form_cache_ttl_seconds

    def _record_error(self, error_type: str) -> None:
        self._last_error_type = error_type


class WatchFactsHttpClientManager:
    def __init__(
        self,
        *,
        client_factory: HttpxClientFactory | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client_factory = client_factory
        self._now = now
        self._clients: dict[tuple[str, str], WatchFactsHttpClient] = {}
        self._fallback_status: dict[tuple[str, str], tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    async def fetch_search(
        self,
        settings: Settings,
        query: str,
        *,
        timeout_ms: int,
    ) -> ScrapeResult:
        client = await self._client_for(settings)
        return await client.fetch_search(query, timeout_ms=timeout_ms)

    def record_fallback(self, settings: Settings, *, error_type: str) -> None:
        key = self._key(settings)
        client = self._clients.get(key)
        if client is not None:
            client.record_fallback(error_type=error_type)
            return
        self._fallback_status[key] = (error_type, self._now())

    def status(self, settings: Settings) -> WatchFactsHttpClientStatus:
        key = self._key(settings)
        client = self._clients.get(key)
        if client is not None:
            return client.status()
        fallback = self._fallback_status.get(key)
        return WatchFactsHttpClientStatus(
            enabled=settings.watchfacts_http_client_enabled,
            form_cache_fresh=False,
            last_error_type=fallback[0] if fallback is not None else None,
            last_fallback_at=fallback[1] if fallback is not None else None,
        )

    async def close_all(self) -> None:
        clients = list(self._clients.values())
        self._clients.clear()
        for client in clients:
            await client.close()

    async def close(self, settings: Settings) -> None:
        client = self._clients.pop(self._key(settings), None)
        if client is not None:
            await client.close()

    async def _client_for(self, settings: Settings) -> WatchFactsHttpClient:
        key = self._key(settings)
        client = self._clients.get(key)
        if client is not None:
            return client
        async with self._lock:
            client = self._clients.get(key)
            if client is None:
                client = WatchFactsHttpClient(
                    settings,
                    client_factory=self._client_factory,
                    now=self._now,
                )
                self._clients[key] = client
            return client

    def _key(self, settings: Settings) -> tuple[str, str]:
        return (
            settings.watchfacts_url,
            str(settings.browser_state_path.resolve()),
        )


_DEFAULT_MANAGER = WatchFactsHttpClientManager()


async def fetch_watchfacts_http_search(
    settings: Settings,
    query: str,
    *,
    timeout_ms: int,
) -> ScrapeResult:
    return await _DEFAULT_MANAGER.fetch_search(settings, query, timeout_ms=timeout_ms)


def record_watchfacts_http_fallback(settings: Settings, *, error_type: str) -> None:
    _DEFAULT_MANAGER.record_fallback(settings, error_type=error_type)


def watchfacts_http_client_status(settings: Settings) -> WatchFactsHttpClientStatus:
    return _DEFAULT_MANAGER.status(settings)


async def close_watchfacts_http_client(settings: Settings | None = None) -> None:
    if settings is None:
        await _DEFAULT_MANAGER.close_all()
    else:
        await _DEFAULT_MANAGER.close(settings)


def watchfacts_http_error_type(exc: Exception) -> str:
    tagged = getattr(exc, "watchfacts_http_error_type", None)
    if isinstance(tagged, str) and tagged:
        return tagged
    if isinstance(exc, BrowserSessionError):
        return "auth_expired"
    if isinstance(exc, ScraperError):
        return "scraper_error"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.NetworkError):
        return "network"
    if isinstance(exc, httpx.HTTPError):
        return "httpx_error"
    return exc.__class__.__name__


def _scraper_error(message: str, error_type: str) -> ScraperError:
    exc = ScraperError(message)
    setattr(exc, "watchfacts_http_error_type", error_type)
    return exc
