from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import httpx

from app.config import Settings


DEFAULT_TIMEOUT_MS = 30_000
SEARCH_TIMEOUT_MS = 90_000
CSRF_RETRY_STATUSES = {401, 403, 419}
logger = logging.getLogger(__name__)


class ScraperError(RuntimeError):
    """Raised when the WatchFacts page cannot be fetched."""


class BrowserSessionError(ScraperError):
    """Raised when the saved authenticated browser state is missing or invalid."""


@dataclass(frozen=True)
class ScrapeResult:
    html: str
    final_url: str
    server_filtered: bool = False


@dataclass(frozen=True)
class BrowserSessionStatus:
    ok: bool
    status: str
    detail: str


@dataclass(frozen=True)
class SearchFormFields:
    action_url: str
    token: str


@dataclass(frozen=True)
class SearchFormCacheEntry:
    action_url: str
    token: str
    fetched_at: float


class Page(Protocol):
    url: str

    async def goto(self, url: str, *, wait_until: str, timeout: int):
        ...

    async def content(self) -> str:
        ...

    async def evaluate(self, script: str, arg):
        ...


class APIResponse(Protocol):
    status: int
    url: str

    async def text(self) -> str:
        ...


class APIRequestContext(Protocol):
    async def get(self, url: str, *, timeout: int) -> APIResponse:
        ...

    async def post(self, url: str, *, form, timeout: int) -> APIResponse:
        ...


SEARCH_FORM_SELECTOR = "#mode3Form"


class BrowserContext(Protocol):
    request: APIRequestContext

    async def new_page(self) -> Page:
        ...

    async def close(self) -> None:
        ...


class Browser(Protocol):
    async def new_context(self, *, storage_state: Path) -> BrowserContext:
        ...

    async def close(self) -> None:
        ...


class WatchFactsSearchHttpClient(Protocol):
    async def fetch_search(self, query: str, *, timeout_ms: int) -> ScrapeResult:
        ...

    async def close(self) -> None:
        ...


HttpClientFactory = Callable[[Settings], WatchFactsSearchHttpClient]
_SEARCH_FORM_CACHE: dict[tuple[str, str, int], SearchFormCacheEntry] = {}


class WatchFactsHttpClient:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._client = client
        self._now = now
        self._storage_cookies_loaded = False

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

        timeout_seconds = max(timeout_ms, SEARCH_TIMEOUT_MS) / 1000
        try:
            form = await self._get_search_form(
                timeout_seconds=timeout_seconds,
                use_cache=True,
            )
            for attempt in range(2):
                response = await self._post_search(
                    form,
                    normalized_query,
                    timeout_seconds=timeout_seconds,
                )
                if response.status_code in CSRF_RETRY_STATUSES:
                    if attempt == 0:
                        self._invalidate_cached_search_form()
                        form = await self._get_search_form(
                            timeout_seconds=timeout_seconds,
                            use_cache=False,
                        )
                        continue
                    raise ScraperError(
                        f"WatchFacts search failed with HTTP {response.status_code}"
                    )
                if response.status_code >= 400:
                    raise ScraperError(
                        f"WatchFacts search failed with HTTP {response.status_code}"
                    )

                text = response.text
                final_url = str(response.url)
                if _looks_unauthenticated(final_url, text):
                    if attempt == 0:
                        self._invalidate_cached_search_form()
                        form = await self._get_search_form(
                            timeout_seconds=timeout_seconds,
                            use_cache=False,
                        )
                        continue
                    raise BrowserSessionError(
                        "Saved browser session appears expired. "
                        "Run `python scripts/ops/login.py` again."
                    )

                return ScrapeResult(
                    html=text,
                    final_url=final_url,
                    server_filtered=True,
                )
        except (BrowserSessionError, ScraperError):
            raise
        except httpx.TimeoutException as exc:
            raise ScraperError("WatchFacts HTTP search timed out") from exc
        except httpx.HTTPError as exc:
            raise ScraperError(
                f"WatchFacts HTTP search failed: {exc.__class__.__name__}"
            ) from exc

        raise ScraperError("WatchFacts HTTP search failed")

    async def _get_search_form(
        self,
        *,
        timeout_seconds: float,
        use_cache: bool,
    ) -> SearchFormCacheEntry:
        if use_cache:
            cached = _SEARCH_FORM_CACHE.get(self._form_cache_key())
            if cached is not None and self._is_form_cache_fresh(cached):
                return cached

        client = await self._get_client()
        response = await client.get(
            self.settings.watchfacts_url,
            timeout=timeout_seconds,
        )
        if response.status_code >= 400:
            raise ScraperError(
                f"WatchFacts navigation failed with HTTP {response.status_code}"
            )
        html = response.text
        final_url = str(response.url)
        if _looks_unauthenticated(final_url, html):
            raise BrowserSessionError(
                "Saved browser session appears expired. "
                "Run `python scripts/ops/login.py` again."
            )
        fields = _extract_search_form_fields(final_url, html)
        if fields is None:
            raise ScraperError("WatchFacts search form not found in HTTP response")

        entry = SearchFormCacheEntry(
            action_url=fields.action_url,
            token=fields.token,
            fetched_at=self._now(),
        )
        _SEARCH_FORM_CACHE[self._form_cache_key()] = entry
        return entry

    async def _post_search(
        self,
        form: SearchFormCacheEntry,
        query: str,
        *,
        timeout_seconds: float,
    ) -> httpx.Response:
        client = await self._get_client()
        return await client.post(
            form.action_url,
            data=_search_form_data(form.token, query),
            headers={"Accept": "application/json, text/plain, */*"},
            timeout=timeout_seconds,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                cookies=self._load_storage_state_cookies(),
                follow_redirects=True,
            )
            self._storage_cookies_loaded = True
        elif not self._storage_cookies_loaded:
            self._client.cookies.update(self._load_storage_state_cookies())
            self._storage_cookies_loaded = True
        return self._client

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
        target_host = _url_host(self.settings.watchfacts_url)
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
            if not _cookie_domain_matches_host(domain, target_host):
                continue
            cookies.set(name, value, domain=domain, path=path or "/")
        return cookies

    def _form_cache_key(self) -> tuple[str, str, int]:
        state_path = self.settings.browser_state_path
        try:
            state_mtime_ns = state_path.stat().st_mtime_ns
        except OSError as exc:
            raise BrowserSessionError(
                f"Missing browser session at {state_path}. "
                "Run `python scripts/ops/login.py` first."
            ) from exc
        return (
            self.settings.watchfacts_url,
            str(state_path.resolve()),
            state_mtime_ns,
        )

    def _invalidate_cached_search_form(self) -> None:
        try:
            _SEARCH_FORM_CACHE.pop(self._form_cache_key(), None)
        except BrowserSessionError:
            return

    def _is_form_cache_fresh(self, cached: SearchFormCacheEntry) -> bool:
        age_seconds = self._now() - cached.fetched_at
        return age_seconds < self.settings.watchfacts_form_cache_ttl_seconds


async def check_watchfacts_session(
    settings: Settings,
    *,
    playwright_factory=None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> BrowserSessionStatus:
    state_path = settings.browser_state_path
    if not state_path.exists():
        return BrowserSessionStatus(
            ok=False,
            status="missing",
            detail="Missing browser session. Run `python scripts/ops/login.py` first.",
        )

    if playwright_factory is None:
        from playwright.async_api import async_playwright

        playwright_factory = async_playwright

    try:
        async with playwright_factory() as playwright:
            browser = await playwright.chromium.launch(headless=settings.headless)
            try:
                context = await browser.new_context(storage_state=state_path)
                try:
                    page = await context.new_page()
                    response = await page.goto(
                        settings.watchfacts_url,
                        wait_until="domcontentloaded",
                        timeout=timeout_ms,
                    )
                    if response is not None and response.status >= 400:
                        return BrowserSessionStatus(
                            ok=False,
                            status="http_error",
                            detail=f"WatchFacts returned HTTP {response.status}.",
                        )

                    html = await page.content()
                    final_url = getattr(page, "url", settings.watchfacts_url)
                    if _looks_unauthenticated(final_url, html):
                        return BrowserSessionStatus(
                            ok=False,
                            status="expired",
                            detail="Saved browser session appears expired.",
                        )
                    return BrowserSessionStatus(
                        ok=True,
                        status="valid",
                        detail="Saved browser session is valid.",
                    )
                finally:
                    await context.close()
            finally:
                await browser.close()
    except Exception as exc:
        return BrowserSessionStatus(
            ok=False,
            status="check_failed",
            detail=f"Session check failed: {exc.__class__.__name__}.",
        )


async def fetch_watchfacts_html(
    settings: Settings,
    *,
    query: str | None = None,
    playwright_factory=None,
    http_client_factory: HttpClientFactory | None = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> ScrapeResult:
    state_path = settings.browser_state_path
    if not state_path.exists():
        raise BrowserSessionError(
            f"Missing browser session at {state_path}. "
            "Run `python scripts/ops/login.py` first."
        )

    if playwright_factory is None:
        from playwright.async_api import async_playwright

        playwright_factory = async_playwright

    normalized_query = query.strip() if query is not None else ""
    if normalized_query and settings.watchfacts_http_client_enabled:
        http_client = (
            http_client_factory(settings)
            if http_client_factory is not None
            else WatchFactsHttpClient.from_settings(settings)
        )
        try:
            return await http_client.fetch_search(
                normalized_query,
                timeout_ms=timeout_ms,
            )
        except Exception as exc:
            logger.info(
                "event=watchfacts_http_client.fallback error_type=%s",
                exc.__class__.__name__,
            )
        finally:
            await http_client.close()

    async with playwright_factory() as playwright:
        browser = await playwright.chromium.launch(headless=settings.headless)
        try:
            context = await browser.new_context(storage_state=state_path)
            try:
                if query and query.strip():
                    try:
                        return await _fetch_search_results_with_request_bootstrap(
                            context,
                            settings.watchfacts_url,
                            query.strip(),
                            timeout_ms=timeout_ms,
                            require_search=True,
                        )
                    except (BrowserSessionError, ScraperError):
                        raise
                    except Exception:
                        pass

                page = await context.new_page()
                try:
                    response = await page.goto(
                        settings.watchfacts_url,
                        wait_until="domcontentloaded",
                        timeout=timeout_ms,
                    )
                    if response is not None and response.status >= 400:
                        raise ScraperError(
                            f"WatchFacts navigation failed with HTTP {response.status}"
                        )
                except ScraperError:
                    raise
                except Exception:
                    if query and query.strip():
                        return await _fetch_search_results_with_request_bootstrap(
                            context,
                            settings.watchfacts_url,
                            query.strip(),
                            timeout_ms=timeout_ms,
                        )
                    raise

                html = await page.content()
                final_url = getattr(page, "url", settings.watchfacts_url)
                if _looks_unauthenticated(final_url, html):
                    raise BrowserSessionError(
                        "Saved browser session appears expired. "
                        "Run `python scripts/ops/login.py` again."
                    )
                if query and query.strip():
                    search_result = await _fetch_search_results(
                        context,
                        page,
                        query.strip(),
                        timeout_ms=timeout_ms,
                    )
                    if search_result is not None:
                        return search_result
                    return await _fetch_search_results_with_request_bootstrap(
                        context,
                        settings.watchfacts_url,
                        query.strip(),
                        timeout_ms=timeout_ms,
                    )
                return ScrapeResult(html=html, final_url=final_url)
            finally:
                await context.close()
        finally:
            await browser.close()


async def _fetch_search_results(
    context: BrowserContext,
    page: Page,
    query: str,
    *,
    timeout_ms: int,
) -> ScrapeResult | None:
    form = page.locator(SEARCH_FORM_SELECTOR)
    if await form.count() == 0:
        return None

    token = await form.locator('input[name="_token"]').get_attribute("value")
    action = await form.get_attribute("action")
    if not token or not action:
        return None

    form_data = _search_form_data(token, query)
    try:
        return await _post_search_results(
            context,
            action,
            form_data,
            timeout_ms=timeout_ms,
        )
    except ScraperError:
        raise
    except Exception:
        return await _fetch_search_results_with_page_fetch(
            page,
            action,
            form_data,
        )


async def _fetch_search_results_with_request_bootstrap(
    context: BrowserContext,
    url: str,
    query: str,
    *,
    timeout_ms: int,
    require_search: bool = False,
) -> ScrapeResult:
    response = await context.request.get(url, timeout=max(timeout_ms, SEARCH_TIMEOUT_MS))
    if response.status >= 400:
        raise ScraperError(f"WatchFacts navigation failed with HTTP {response.status}")
    html = await response.text()
    if _looks_unauthenticated(response.url, html):
        raise BrowserSessionError(
            "Saved browser session appears expired. "
            "Run `python scripts/ops/login.py` again."
        )
    search_result = await _fetch_search_results_from_html(
        context,
        response.url,
        html,
        query,
        timeout_ms=timeout_ms,
    )
    if search_result is None:
        if require_search:
            raise ValueError("WatchFacts search form not found in request response")
        return ScrapeResult(html=html, final_url=response.url)
    return search_result


async def _fetch_search_results_from_html(
    context: BrowserContext,
    base_url: str,
    html: str,
    query: str,
    *,
    timeout_ms: int,
) -> ScrapeResult | None:
    fields = _extract_search_form_fields(base_url, html)
    if fields is None:
        return None

    return await _post_search_results(
        context,
        fields.action_url,
        _search_form_data(fields.token, query),
        timeout_ms=timeout_ms,
    )


async def _fetch_search_results_with_page_fetch(
    page: Page,
    action: str,
    form_data: dict[str, str],
) -> ScrapeResult:
    result = await page.evaluate(
        """
        async ({ action, formData }) => {
          const response = await fetch(action, {
            method: "POST",
            credentials: "include",
            headers: {
              "Accept": "application/json, text/plain, */*",
              "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            },
            body: new URLSearchParams(formData).toString(),
          });
          return {
            status: response.status,
            url: response.url,
            text: await response.text(),
          };
        }
        """,
        {"action": action, "formData": form_data},
    )
    status = int(result.get("status", 0))
    if status >= 400:
        raise ScraperError(f"WatchFacts search failed with HTTP {status}")
    return ScrapeResult(
        html=str(result.get("text", "")),
        final_url=str(result.get("url", action)),
        server_filtered=True,
    )


async def _post_search_results(
    context: BrowserContext,
    action: str,
    form_data: dict[str, str],
    *,
    timeout_ms: int,
) -> ScrapeResult:
    response = await context.request.post(
        action,
        form=form_data,
        timeout=max(timeout_ms, SEARCH_TIMEOUT_MS),
    )
    if response.status >= 400:
        raise ScraperError(f"WatchFacts search failed with HTTP {response.status}")
    return ScrapeResult(
        html=await response.text(),
        final_url=response.url,
        server_filtered=True,
    )


def _search_form_data(token: str, query: str) -> dict[str, str]:
    return {
        "_token": token,
        "listingType": "sale",
        "reference": query,
        "region": "",
        "dial_color": "",
        "is_bundle": "",
        "sort_by": "price-low",
        "created_days": "90",
    }


def _extract_search_form_fields(base_url: str, html: str) -> SearchFormFields | None:
    soup = BeautifulSoup(html, "lxml")
    form = soup.select_one(SEARCH_FORM_SELECTOR)
    if form is None:
        return None

    token_input = form.select_one('input[name="_token"]')
    token = token_input.get("value") if token_input is not None else None
    action = form.get("action")
    if not isinstance(token, str) or not token:
        return None
    if not isinstance(action, str) or not action:
        return None

    action_url = urljoin(base_url, action)
    if not _same_origin(base_url, action_url):
        raise ScraperError("WatchFacts search form action is cross-origin")
    return SearchFormFields(action_url=action_url, token=token)


def _same_origin(left_url: str, right_url: str) -> bool:
    left = urlparse(left_url)
    right = urlparse(right_url)
    return (
        left.scheme == right.scheme
        and _without_leading_www(left.hostname or "")
        == _without_leading_www(right.hostname or "")
        and (left.port or _default_port(left.scheme))
        == (right.port or _default_port(right.scheme))
    )


def _without_leading_www(host: str) -> str:
    normalized = host.casefold()
    if normalized.startswith("www."):
        return normalized[4:]
    return normalized


def _default_port(scheme: str) -> int | None:
    if scheme == "http":
        return 80
    if scheme == "https":
        return 443
    return None


def _url_host(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.hostname or "").casefold()


def _cookie_domain_matches_host(domain: str, host: str) -> bool:
    normalized_domain = domain.lstrip(".").casefold()
    normalized_host = host.casefold()
    return (
        normalized_host == normalized_domain
        or normalized_host.endswith(f".{normalized_domain}")
        or normalized_domain.endswith(f".{normalized_host}")
    )


def _looks_unauthenticated(final_url: str, html: str) -> bool:
    normalized_url = final_url.casefold()
    if (
        "login" in normalized_url
        or "signin" in normalized_url
        or "sign-in" in normalized_url
    ):
        return True

    normalized_html = html.casefold()
    login_markers = [
        'type="password"',
        "name=\"password\"",
        "log in",
        "sign in",
    ]
    return any(marker in normalized_html for marker in login_markers)
