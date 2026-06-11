from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.config import Settings
from app.watchfacts_forms import (
    SEARCH_FORM_SELECTOR,
    extract_search_form_fields,
    search_form_data,
)


DEFAULT_TIMEOUT_MS = 30_000
SEARCH_TIMEOUT_MS = 90_000


class ScraperError(RuntimeError):
    """Raised when the WatchFacts page cannot be fetched."""


class BrowserSessionError(ScraperError):
    """Raised when the saved authenticated browser state is missing or invalid."""


@dataclass(frozen=True)
class ScrapeResult:
    html: str
    final_url: str
    server_filtered: bool = False
    used_playwright_fallback: bool = False


@dataclass(frozen=True)
class BrowserSessionStatus:
    ok: bool
    status: str
    detail: str


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

    normalized_query = query.strip() if query is not None else ""
    if not normalized_query:
        raise ScraperError("A non-empty query is required for search.")
    if not settings.watchfacts_http_client_enabled:
        raise ScraperError("WatchFacts HTTP search client is disabled.")

    if http_client_factory is not None:
        http_client = http_client_factory(settings)
        try:
            return await http_client.fetch_search(
                normalized_query,
                timeout_ms=timeout_ms,
            )
        finally:
            await http_client.close()

    from app.watchfacts_http import fetch_watchfacts_http_search

    return await fetch_watchfacts_http_search(
        settings,
        normalized_query,
        timeout_ms=timeout_ms,
    )


async def _fetch_watchfacts_html_with_playwright(
    settings: Settings,
    *,
    query: str | None,
    playwright_factory,
    timeout_ms: int,
) -> ScrapeResult:
    state_path = settings.browser_state_path
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

    form_data = search_form_data(token, query)
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
    from app.parser import parse_listings

    listings = parse_listings(html)
    if listings:
        # In some environments, the GET request to the search endpoint already
        # returns filtered JSON/HTML results for the provided query.
        # Use that payload directly when available, avoiding another POST roundtrip.
        return ScrapeResult(html=html, final_url=response.url, server_filtered=True)

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
    try:
        fields = extract_search_form_fields(base_url, html)
    except ValueError as exc:
        raise ScraperError("WatchFacts search form action is cross-origin") from exc
    if fields is None:
        return None

    return await _post_search_results(
        context,
        fields.action_url,
        search_form_data(fields.token, query),
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


def _looks_unauthenticated(final_url: str, html: str) -> bool:
    normalized_url = final_url.casefold()
    if (
        "login" in normalized_url
        or "signin" in normalized_url
        or "sign-in" in normalized_url
    ):
        return True

    normalized_html = html.casefold()
    password_markers = [
        'type="password"',
        "name=\"password\"",
    ]
    if any(marker in normalized_html for marker in password_markers):
        return True

    form_markers = [
        "<form",
        "login",
        "signin",
        "sign-in",
    ]
    action_markers = [
        "log in",
        "sign in",
    ]
    return any(marker in normalized_html for marker in form_markers) and any(
        marker in normalized_html for marker in action_markers
    )
