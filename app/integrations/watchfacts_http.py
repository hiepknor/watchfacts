from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from app.config import Settings
from app.searching.matcher_token_classification import parse_query_terms
from app.searching.query_intent import classify_query_intent
from app.integrations.scraper import (
    BrowserSessionError,
    ScrapeResult,
    ScraperError,
    _looks_unauthenticated,
)
from app.integrations.watchfacts_forms import (
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
HttpClientBaseKey = tuple[str, str]
HttpClientKey = tuple[str, str, bool, int, int, int, int, int, int, int]
logger = logging.getLogger("app.watchfacts_http")


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
    last_failure_at: float | None = None
    # Deprecated compatibility key; HTTPX search no longer has a Playwright fallback.
    last_fallback_at: float | None = None
    last_elapsed_ms: int | None = None
    last_form_refresh_elapsed_ms: int | None = None
    last_post_elapsed_ms: int | None = None
    last_http_version: str | None = None
    last_status_code: int | None = None
    last_response_bytes: int | None = None
    last_server_query_changed: bool | None = None
    last_server_query_token_count: int | None = None
    consecutive_failures: int = 0
    cooldown_until: float | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "form_cache_fresh": self.form_cache_fresh,
            "last_error_type": self.last_error_type,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_fallback_at": self.last_fallback_at,
            "last_elapsed_ms": self.last_elapsed_ms,
            "last_form_refresh_elapsed_ms": self.last_form_refresh_elapsed_ms,
            "last_post_elapsed_ms": self.last_post_elapsed_ms,
            "last_http_version": self.last_http_version,
            "last_status_code": self.last_status_code,
            "last_response_bytes": self.last_response_bytes,
            "last_server_query_changed": self.last_server_query_changed,
            "last_server_query_token_count": self.last_server_query_token_count,
            "consecutive_failures": self.consecutive_failures,
            "cooldown_until": self.cooldown_until,
        }


class WatchFactsHttpClient:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        client_factory: HttpxClientFactory | None = None,
        now: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self._client = client
        self._client_factory = client_factory
        self._owns_client = client is None
        self._now = now
        self._wall_clock = wall_clock
        self._fingerprint: BrowserStateFingerprint | None = None
        self._form_cache: SearchFormCacheEntry | None = None
        self._client_lock = asyncio.Lock()
        self._form_lock = asyncio.Lock()
        self._last_error_type: str | None = None
        self._last_success_at: float | None = None
        self._last_failure_at: float | None = None
        self._last_elapsed_ms: int | None = None
        self._last_form_refresh_elapsed_ms: int | None = None
        self._last_post_elapsed_ms: int | None = None
        self._last_http_version: str | None = None
        self._last_status_code: int | None = None
        self._last_response_bytes: int | None = None
        self._last_server_query_changed: bool | None = None
        self._last_server_query_token_count: int | None = None
        self._consecutive_failures = 0
        self._cooldown_until: float | None = None

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        now: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> "WatchFactsHttpClient":
        return cls(settings, client=client, now=now, wall_clock=wall_clock)

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

        if self._cooldown_is_active():
            self._record_error("cooldown")
            raise _scraper_error("WatchFacts HTTP search is cooling down", "cooldown")

        started_at = self._now()
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

                self._record_success(started_at)
                return ScrapeResult(
                    html=text,
                    final_url=final_url,
                    server_filtered=True,
                )
        except BrowserSessionError:
            self._record_failure("auth_expired", started_at)
            raise
        except ScraperError as exc:
            self._record_failure(watchfacts_http_error_type(exc), started_at)
            raise
        except httpx.TimeoutException as exc:
            self._record_failure("timeout", started_at)
            raise _scraper_error("WatchFacts HTTP search timed out", "timeout") from exc
        except httpx.NetworkError as exc:
            self._record_failure("network", started_at)
            raise _scraper_error(
                f"WatchFacts HTTP search failed: {exc.__class__.__name__}",
                "network",
            ) from exc
        except httpx.HTTPError as exc:
            self._record_failure("httpx_error", started_at)
            raise _scraper_error(
                f"WatchFacts HTTP search failed: {exc.__class__.__name__}",
                "httpx_error",
            ) from exc

        raise _scraper_error("WatchFacts HTTP search failed", "unknown")

    async def warmup(self, *, timeout_ms: int) -> None:
        if self._cooldown_is_active():
            self._record_error("cooldown")
            raise _scraper_error("WatchFacts HTTP search is cooling down", "cooldown")

        started_at = self._now()
        try:
            await self._get_search_form(timeout_ms=timeout_ms, use_cache=True)
            self._record_success(started_at)
        except BrowserSessionError:
            self._record_failure("auth_expired", started_at)
            raise
        except ScraperError as exc:
            self._record_failure(watchfacts_http_error_type(exc), started_at)
            raise
        except httpx.TimeoutException as exc:
            self._record_failure("timeout", started_at)
            raise _scraper_error("WatchFacts HTTP warmup timed out", "timeout") from exc
        except httpx.NetworkError as exc:
            self._record_failure("network", started_at)
            raise _scraper_error(
                f"WatchFacts HTTP warmup failed: {exc.__class__.__name__}",
                "network",
            ) from exc
        except httpx.HTTPError as exc:
            self._record_failure("httpx_error", started_at)
            raise _scraper_error(
                f"WatchFacts HTTP warmup failed: {exc.__class__.__name__}",
                "httpx_error",
            ) from exc

    def status(self) -> WatchFactsHttpClientStatus:
        return WatchFactsHttpClientStatus(
            enabled=self.settings.watchfacts_http_client_enabled,
            form_cache_fresh=self._form_cache_is_fresh(),
            last_error_type=self._last_error_type,
            last_success_at=self._last_success_at,
            last_failure_at=self._last_failure_at,
            last_fallback_at=None,
            last_elapsed_ms=self._last_elapsed_ms,
            last_form_refresh_elapsed_ms=self._last_form_refresh_elapsed_ms,
            last_post_elapsed_ms=self._last_post_elapsed_ms,
            last_http_version=self._last_http_version,
            last_status_code=self._last_status_code,
            last_response_bytes=self._last_response_bytes,
            last_server_query_changed=self._last_server_query_changed,
            last_server_query_token_count=self._last_server_query_token_count,
            consecutive_failures=self._consecutive_failures,
            cooldown_until=self._cooldown_until,
        )

    async def _get_search_form(
        self,
        *,
        timeout_ms: int,
        use_cache: bool,
    ) -> SearchFormCacheEntry:
        if use_cache and self._form_cache_is_fresh():
            assert self._form_cache is not None
            self._last_form_refresh_elapsed_ms = 0
            return self._form_cache

        async with self._form_lock:
            if use_cache and self._form_cache_is_fresh():
                assert self._form_cache is not None
                self._last_form_refresh_elapsed_ms = 0
                return self._form_cache

            client = await self._get_client()
            started_at = self._now()
            response: httpx.Response | None = None
            try:
                response = await client.get(
                    self.settings.watchfacts_url,
                    timeout=self._request_timeout(timeout_ms),
                )
            finally:
                self._last_form_refresh_elapsed_ms = _elapsed_ms(
                    self._now(),
                    started_at,
                )
            self._record_http_version(response)
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
        started_at = self._now()
        response: httpx.Response | None = None
        server_query = _server_search_query(query)
        server_query_token_count = len(server_query.split())
        server_query_changed = _normalized_query(server_query) != _normalized_query(
            query
        )
        self._last_status_code = None
        self._last_response_bytes = None
        self._last_server_query_changed = server_query_changed
        self._last_server_query_token_count = server_query_token_count
        try:
            response = await client.post(
                form.action_url,
                data=search_form_data(form.token, server_query),
                headers={"Accept": "application/json, text/plain, */*"},
                timeout=self._search_request_timeout(timeout_ms),
            )
            return response
        finally:
            self._last_post_elapsed_ms = _elapsed_ms(self._now(), started_at)
            self._record_http_version(response)
            if response is not None:
                self._last_status_code = response.status_code
                self._last_response_bytes = len(response.content)
                logger.info(
                    "event=watchfacts_http_client.search_post "
                    "elapsed_ms=%s status_code=%s response_bytes=%s "
                    "http_version=%s server_query_changed=%s "
                    "server_query_token_count=%s",
                    self._last_post_elapsed_ms,
                    self._last_status_code,
                    self._last_response_bytes,
                    self._last_http_version,
                    self._last_server_query_changed,
                    self._last_server_query_token_count,
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
        request_timeout_seconds = (
            timeout_ms / 1000
            if timeout_ms > 0
            else self.settings.watchfacts_http_read_timeout_seconds
        )
        read_timeout = min(
            request_timeout_seconds,
            self.settings.watchfacts_http_read_timeout_seconds,
        )
        return self._timeout_with_read(read_timeout)

    def _search_request_timeout(self, timeout_ms: int) -> httpx.Timeout:
        request_timeout_seconds = (
            timeout_ms / 1000
            if timeout_ms > 0
            else self.settings.watchfacts_http_search_read_timeout_seconds
        )
        read_timeout = max(
            request_timeout_seconds,
            self.settings.watchfacts_http_search_read_timeout_seconds,
        )
        return self._timeout_with_read(read_timeout)

    def _timeout_with_read(self, read_timeout: float) -> httpx.Timeout:
        return httpx.Timeout(
            read_timeout,
            connect=self.settings.watchfacts_http_connect_timeout_seconds,
            read=read_timeout,
            write=WATCHFACTS_HTTP_WRITE_TIMEOUT_SECONDS,
            pool=self.settings.watchfacts_http_pool_timeout_seconds,
        )

    def _default_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            self.settings.watchfacts_http_read_timeout_seconds,
            connect=self.settings.watchfacts_http_connect_timeout_seconds,
            read=self.settings.watchfacts_http_read_timeout_seconds,
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

    def _record_failure(self, error_type: str, started_at: float) -> None:
        self._last_error_type = error_type
        self._last_elapsed_ms = _elapsed_ms(self._now(), started_at)
        if error_type in {"cooldown", "auth_expired"}:
            return
        failure_at = self._wall_clock()
        self._last_failure_at = failure_at
        self._consecutive_failures += 1
        self._cooldown_until = (
            failure_at + self.settings.watchfacts_http_failure_cooldown_seconds
        )

    def _record_success(self, started_at: float) -> None:
        self._last_error_type = None
        self._last_success_at = self._wall_clock()
        self._last_elapsed_ms = _elapsed_ms(self._now(), started_at)
        self._consecutive_failures = 0
        self._cooldown_until = None

    def _cooldown_is_active(self) -> bool:
        return (
            self._cooldown_until is not None
            and self._wall_clock() < self._cooldown_until
        )

    def _record_http_version(self, response: httpx.Response | None) -> None:
        if response is not None:
            self._last_http_version = response.http_version


class WatchFactsHttpClientManager:
    def __init__(
        self,
        *,
        client_factory: HttpxClientFactory | None = None,
        now: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._client_factory = client_factory
        self._now = now
        self._wall_clock = wall_clock
        self._clients: dict[HttpClientKey, WatchFactsHttpClient] = {}
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

    async def warmup(self, settings: Settings, *, timeout_ms: int) -> None:
        client = await self._client_for(settings)
        await client.warmup(timeout_ms=timeout_ms)

    def status(self, settings: Settings) -> WatchFactsHttpClientStatus:
        key = self._key(settings)
        client = self._clients.get(key)
        if client is not None:
            return client.status()
        return WatchFactsHttpClientStatus(
            enabled=settings.watchfacts_http_client_enabled,
            form_cache_fresh=False,
        )

    async def close_all(self) -> None:
        clients = list(self._clients.values())
        self._clients.clear()
        for client in clients:
            await client.close()

    async def close(self, settings: Settings) -> None:
        base_key = self._base_key(settings)
        clients = [
            self._clients.pop(key)
            for key in list(self._clients)
            if self._base_key_from_key(key) == base_key
        ]
        for client in clients:
            await client.close()

    async def _client_for(self, settings: Settings) -> WatchFactsHttpClient:
        key = self._key(settings)
        client = self._clients.get(key)
        if client is not None:
            return client
        async with self._lock:
            client = self._clients.get(key)
            if client is None:
                await self._close_stale_clients_for_settings(settings, keep_key=key)
                client = WatchFactsHttpClient(
                    settings,
                    client_factory=self._client_factory,
                    now=self._now,
                    wall_clock=self._wall_clock,
                )
                self._clients[key] = client
            return client

    async def _close_stale_clients_for_settings(
        self,
        settings: Settings,
        *,
        keep_key: HttpClientKey,
    ) -> None:
        base_key = self._base_key(settings)
        stale_keys = [
            key
            for key in self._clients
            if key != keep_key and self._base_key_from_key(key) == base_key
        ]
        for stale_key in stale_keys:
            await self._clients.pop(stale_key).close()

    def _base_key(self, settings: Settings) -> HttpClientBaseKey:
        return (
            settings.watchfacts_url,
            str(settings.browser_state_path.resolve()),
        )

    def _key(self, settings: Settings) -> HttpClientKey:
        return (
            settings.watchfacts_url,
            str(settings.browser_state_path.resolve()),
            settings.watchfacts_http_client_enabled,
            settings.watchfacts_form_cache_ttl_seconds,
            settings.watchfacts_http_connect_timeout_seconds,
            settings.watchfacts_http_pool_timeout_seconds,
            settings.watchfacts_http_keepalive_expiry_seconds,
            settings.watchfacts_http_read_timeout_seconds,
            settings.watchfacts_http_search_read_timeout_seconds,
            settings.watchfacts_http_failure_cooldown_seconds,
        )

    def _base_key_from_key(self, key: HttpClientKey) -> HttpClientBaseKey:
        return (key[0], key[1])


_DEFAULT_MANAGER = WatchFactsHttpClientManager()


async def fetch_watchfacts_http_search(
    settings: Settings,
    query: str,
    *,
    timeout_ms: int,
) -> ScrapeResult:
    return await _DEFAULT_MANAGER.fetch_search(settings, query, timeout_ms=timeout_ms)


async def warm_watchfacts_http_client(
    settings: Settings,
    *,
    timeout_ms: int = 30_000,
) -> None:
    await _DEFAULT_MANAGER.warmup(settings, timeout_ms=timeout_ms)


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


def _server_search_query(query: str) -> str:
    intent = classify_query_intent(query)
    if intent.kind == "brand_model_descriptor":
        return query.strip()
    reference_terms, _ = parse_query_terms(query)
    if not reference_terms:
        return query.strip()
    return " ".join(" ".join(parts) for parts in reference_terms)


def _normalized_query(query: str) -> str:
    return " ".join(query.casefold().split())


def _scraper_error(message: str, error_type: str) -> ScraperError:
    exc = ScraperError(message)
    setattr(exc, "watchfacts_http_error_type", error_type)
    return exc


def _elapsed_ms(now: float, started_at: float) -> int:
    return max(0, int((now - started_at) * 1000))
