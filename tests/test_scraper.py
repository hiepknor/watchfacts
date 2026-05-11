from __future__ import annotations

import asyncio

import pytest

from app.config import Settings
from app.scraper import (
    BrowserSessionError,
    ScrapeResult,
    ScraperError,
    fetch_watchfacts_html,
)


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status


class FakePage:
    def __init__(self, html: str, final_url: str, response_status: int = 200) -> None:
        self._html = html
        self.url = final_url
        self.response_status = response_status
        self.goto_calls: list[tuple[str, str, int]] = []

    async def goto(self, url: str, *, wait_until: str, timeout: int):
        self.goto_calls.append((url, wait_until, timeout))
        return FakeResponse(self.response_status)

    async def content(self) -> str:
        return self._html


class FakeLocator:
    def __init__(self, *, count: int = 1, attributes: dict[str, str] | None = None) -> None:
        self._count = count
        self.attributes = attributes or {}

    async def count(self) -> int:
        return self._count

    def locator(self, selector: str) -> "FakeLocator":
        return FakeLocator(attributes={"value": self.attributes.get(selector, "")})

    async def get_attribute(self, name: str) -> str | None:
        return self.attributes.get(name)


class FakeSearchPage(FakePage):
    def locator(self, selector: str) -> FakeLocator:
        if selector == "#mode3Form":
            return FakeLocator(
                attributes={
                    "action": "https://watchfacts.example/simon-search-matches",
                    'input[name="_token"]': "csrf-token",
                }
            )
        return FakeLocator(count=0)


class FakeRequest:
    def __init__(self, response) -> None:
        self.response = response
        self.posts = []

    async def post(self, url: str, *, form, timeout: int):
        self.posts.append((url, form, timeout))
        return self.response


class FakeSearchResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        url: str = "https://watchfacts.example/simon-search-matches",
        body: str = '{"listings":[]}',
    ) -> None:
        self.status = status
        self.url = url
        self.body = body

    async def text(self) -> str:
        return self.body


class FakeContext:
    def __init__(self, page: FakePage, request: FakeRequest | None = None) -> None:
        self.page = page
        self.request = request
        self.closed = False

    async def new_page(self) -> FakePage:
        return self.page

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.storage_state = None
        self.closed = False

    async def new_context(self, *, storage_state):
        self.storage_state = storage_state
        return self.context

    async def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser
        self.headless = None

    async def launch(self, *, headless: bool) -> FakeBrowser:
        self.headless = headless
        return self.browser


class FakePlaywright:
    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium


class FakePlaywrightManager:
    def __init__(self, playwright: FakePlaywright) -> None:
        self.playwright = playwright

    async def __aenter__(self) -> FakePlaywright:
        return self.playwright

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def make_settings(tmp_path, *, state_exists: bool = True) -> Settings:
    state_path = tmp_path / "data" / "watchfacts_state.json"
    if state_exists:
        state_path.parent.mkdir(parents=True)
        state_path.write_text("{}")

    return Settings(
        telegram_bot_token="token",
        watchfacts_url="https://watchfacts.example/simon-match-making",
        headless=True,
        enable_crawl4ai=True,
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        logs_dir=tmp_path / "logs",
        db_path=tmp_path / "data" / "bot.db",
        browser_state_path=state_path,
    )


def make_playwright_factory(page: FakePage):
    context = FakeContext(page)
    browser = FakeBrowser(context)
    chromium = FakeChromium(browser)

    def factory() -> FakePlaywrightManager:
        return FakePlaywrightManager(FakePlaywright(chromium))

    return factory, chromium, browser, context


def make_search_playwright_factory(page: FakeSearchPage, response: FakeSearchResponse):
    request = FakeRequest(response)
    context = FakeContext(page, request=request)
    browser = FakeBrowser(context)
    chromium = FakeChromium(browser)

    def factory() -> FakePlaywrightManager:
        return FakePlaywrightManager(FakePlaywright(chromium))

    return factory, request


def test_missing_browser_state_raises_clear_error(tmp_path) -> None:
    settings = make_settings(tmp_path, state_exists=False)

    with pytest.raises(BrowserSessionError, match="Run `python scripts/login.py` first"):
        asyncio.run(fetch_watchfacts_html(settings))


def test_fetch_watchfacts_html_loads_state_and_navigates_to_configured_url(tmp_path) -> None:
    settings = make_settings(tmp_path)
    page = FakePage("<html><body>Listings</body></html>", settings.watchfacts_url)
    factory, chromium, browser, context = make_playwright_factory(page)

    result = asyncio.run(
        fetch_watchfacts_html(
            settings,
            playwright_factory=factory,
            timeout_ms=1234,
        )
    )

    assert result == ScrapeResult(
        html="<html><body>Listings</body></html>",
        final_url=settings.watchfacts_url,
    )
    assert chromium.headless is True
    assert browser.storage_state == settings.browser_state_path
    assert page.goto_calls == [(settings.watchfacts_url, "networkidle", 1234)]
    assert context.closed is True
    assert browser.closed is True


def test_fetch_watchfacts_html_posts_query_to_watchfacts_search(tmp_path) -> None:
    settings = make_settings(tmp_path)
    page = FakeSearchPage("<html><body>Listings</body></html>", settings.watchfacts_url)
    response = FakeSearchResponse(body='{"listings":[{"title":"116500 black"}]}')
    factory, request = make_search_playwright_factory(page, response)

    result = asyncio.run(
        fetch_watchfacts_html(
            settings,
            query="116500 black",
            playwright_factory=factory,
            timeout_ms=1234,
        )
    )

    assert result == ScrapeResult(
        html='{"listings":[{"title":"116500 black"}]}',
        final_url="https://watchfacts.example/simon-search-matches",
        server_filtered=True,
    )
    assert request.posts == [
        (
            "https://watchfacts.example/simon-search-matches",
            {
                "_token": "csrf-token",
                "listingType": "sale",
                "reference": "116500 black",
                "region": "",
                "dial_color": "",
                "is_bundle": "",
                "sort_by": "price-low",
                "created_days": "90",
            },
            90_000,
        )
    ]


def test_expired_browser_state_raises_clear_error(tmp_path) -> None:
    settings = make_settings(tmp_path)
    page = FakePage(
        "<html><body><input type=\"password\"></body></html>",
        "https://watchfacts.example/login",
    )
    factory, _, _, _ = make_playwright_factory(page)

    with pytest.raises(BrowserSessionError, match="appears expired"):
        asyncio.run(fetch_watchfacts_html(settings, playwright_factory=factory))


def test_http_error_raises_scraper_error(tmp_path) -> None:
    settings = make_settings(tmp_path)
    page = FakePage("<html></html>", settings.watchfacts_url, response_status=500)
    factory, _, _, _ = make_playwright_factory(page)

    with pytest.raises(ScraperError, match="HTTP 500"):
        asyncio.run(fetch_watchfacts_html(settings, playwright_factory=factory))
