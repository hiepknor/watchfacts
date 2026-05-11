from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from app.config import DEFAULT_WATCHFACTS_URL, ConfigError, parse_bool


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class LoginSettings:
    watchfacts_url: str
    headless: bool
    browser_state_path: Path


def load_login_settings() -> LoginSettings:
    load_dotenv(PROJECT_ROOT / ".env")

    watchfacts_url = os.environ.get("WATCHFACTS_URL", DEFAULT_WATCHFACTS_URL).strip()
    if not watchfacts_url:
        raise ConfigError("WATCHFACTS_URL must not be empty")

    return LoginSettings(
        watchfacts_url=watchfacts_url,
        headless=parse_bool(
            os.environ.get("LOGIN_HEADLESS", "false"),
            name="LOGIN_HEADLESS",
        ),
        browser_state_path=PROJECT_ROOT / "data" / "watchfacts_state.json",
    )


def run_login(settings: LoginSettings) -> None:
    from playwright.sync_api import sync_playwright

    settings.browser_state_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=settings.headless)
        context = None
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(settings.watchfacts_url, wait_until="domcontentloaded")

            print("A Chromium window has opened for WatchFacts login.")
            print("Log in manually, complete any required checks, then return here.")
            input("Press Enter after the WatchFacts page is authenticated...")

            context.storage_state(path=settings.browser_state_path)
        finally:
            if context is not None:
                context.close()
            browser.close()

    print(f"Saved browser session to {settings.browser_state_path}")


def main() -> int:
    try:
        settings = load_login_settings()
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 2

    run_login(settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
