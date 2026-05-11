from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.config import Settings


DEFAULT_TIMEOUT_MS = 30_000


class ScraperError(RuntimeError):
    """Raised when the WatchFacts page cannot be fetched."""


class BrowserSessionError(ScraperError):
    """Raised when the saved authenticated browser state is missing or invalid."""


@dataclass(frozen=True)
class ScrapeResult:
    html: str
    final_url: str


class Page(Protocol):
    url: str

    async def goto(self, url: str, *, wait_until: str, timeout: int):
        ...

    async def content(self) -> str:
        ...


class BrowserContext(Protocol):
    async def new_page(self) -> Page:
        ...

    async def close(self) -> None:
        ...


class Browser(Protocol):
    async def new_context(self, *, storage_state: Path) -> BrowserContext:
        ...

    async def close(self) -> None:
        ...


async def fetch_watchfacts_html(
    settings: Settings,
    *,
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

    browser = None
    context = None
    try:
        async with playwright_factory() as playwright:
            browser = await playwright.chromium.launch(headless=settings.headless)
            context = await browser.new_context(storage_state=state_path)
            page = await context.new_page()
            response = await page.goto(
                settings.watchfacts_url,
                wait_until="networkidle",
                timeout=timeout_ms,
            )
            if response is not None and response.status >= 400:
                raise ScraperError(
                    f"WatchFacts navigation failed with HTTP {response.status}"
                )

            html = await page.content()
            final_url = getattr(page, "url", settings.watchfacts_url)
            if _looks_unauthenticated(final_url, html):
                raise BrowserSessionError(
                    "Saved browser session appears expired. "
                    "Run `python scripts/login.py` again."
                )
            return ScrapeResult(html=html, final_url=final_url)
    finally:
        if context is not None:
            await context.close()
        if browser is not None:
            await browser.close()


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
