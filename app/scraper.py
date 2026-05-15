from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.config import Settings


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
            detail="Missing browser session. Run `python scripts/login.py` first.",
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
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> ScrapeResult:
    state_path = settings.browser_state_path
    if not state_path.exists():
        raise BrowserSessionError(
            f"Missing browser session at {state_path}. "
            "Run `python scripts/login.py` first."
        )

    if playwright_factory is None:
        from playwright.async_api import async_playwright

        playwright_factory = async_playwright

    async with playwright_factory() as playwright:
        browser = await playwright.chromium.launch(headless=settings.headless)
        try:
            context = await browser.new_context(storage_state=state_path)
            try:
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
                        "Run `python scripts/login.py` again."
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
) -> ScrapeResult:
    response = await context.request.get(url, timeout=max(timeout_ms, SEARCH_TIMEOUT_MS))
    if response.status >= 400:
        raise ScraperError(f"WatchFacts navigation failed with HTTP {response.status}")
    html = await response.text()
    if _looks_unauthenticated(response.url, html):
        raise BrowserSessionError(
            "Saved browser session appears expired. "
            "Run `python scripts/login.py` again."
        )
    search_result = await _fetch_search_results_from_html(
        context,
        response.url,
        html,
        query,
        timeout_ms=timeout_ms,
    )
    if search_result is None:
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

    return await _post_search_results(
        context,
        urljoin(base_url, action),
        _search_form_data(token, query),
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
