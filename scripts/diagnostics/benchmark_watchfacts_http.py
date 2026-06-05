from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import load_search_settings
from app.scraper import fetch_watchfacts_html
from app.watchfacts_http import (
    close_watchfacts_http_client,
    fetch_watchfacts_http_search,
    warm_watchfacts_http_client,
    watchfacts_http_client_status,
    watchfacts_http_error_type,
)


async def _run_httpx_case(
    *,
    query: str,
    timeout_ms: int,
    run_number: int,
) -> bool:
    settings = load_search_settings()
    started_at = time.perf_counter()
    try:
        result = await fetch_watchfacts_http_search(
            settings,
            query,
            timeout_ms=timeout_ms,
        )
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        status = watchfacts_http_client_status(settings)
        print(
            "HTTPX "
            f"run={run_number} ok=false elapsed_ms={elapsed_ms} "
            f"error_type={watchfacts_http_error_type(exc)} "
            f"consecutive_failures={status.consecutive_failures} "
            f"cooldown_active={status.cooldown_until is not None}"
        )
        return False

    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    status = watchfacts_http_client_status(settings)
    print(
        "HTTPX "
        f"run={run_number} ok=true elapsed_ms={elapsed_ms} "
        f"status_elapsed_ms={status.last_elapsed_ms} "
        f"form_ms={status.last_form_refresh_elapsed_ms} "
        f"post_ms={status.last_post_elapsed_ms} "
        f"http_version={status.last_http_version} "
        f"html_bytes={len(result.html.encode('utf-8'))} "
        f"server_filtered={result.server_filtered}"
    )
    return True


async def _run_playwright_case(*, query: str, timeout_ms: int) -> None:
    settings = replace(load_search_settings(), watchfacts_http_client_enabled=False)
    started_at = time.perf_counter()
    try:
        result = await fetch_watchfacts_html(settings, query=query, timeout_ms=timeout_ms)
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        print(
            "PLAYWRIGHT "
            f"ok=false elapsed_ms={elapsed_ms} error_type={exc.__class__.__name__}"
        )
        return

    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    print(
        "PLAYWRIGHT "
        f"ok=true elapsed_ms={elapsed_ms} "
        f"html_bytes={len(result.html.encode('utf-8'))} "
        f"server_filtered={result.server_filtered}"
    )


async def _main_async(args: argparse.Namespace) -> int:
    settings = load_search_settings()
    await close_watchfacts_http_client()
    try:
        if args.warmup:
            started_at = time.perf_counter()
            try:
                await warm_watchfacts_http_client(settings, timeout_ms=args.timeout_ms)
            except Exception as exc:
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                print(
                    "WARMUP "
                    f"ok=false elapsed_ms={elapsed_ms} "
                    f"error_type={watchfacts_http_error_type(exc)}"
                )
            else:
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                status = watchfacts_http_client_status(settings)
                print(
                    "WARMUP "
                    f"ok=true elapsed_ms={elapsed_ms} "
                    f"form_ms={status.last_form_refresh_elapsed_ms} "
                    f"http_version={status.last_http_version}"
                )

        passed = 0
        for run_number in range(1, args.repeat + 1):
            if await _run_httpx_case(
                query=args.query,
                timeout_ms=args.timeout_ms,
                run_number=run_number,
            ):
                passed += 1

        if args.include_playwright:
            await _run_playwright_case(query=args.query, timeout_ms=args.timeout_ms)

        print(f"SUMMARY httpx_passed={passed}/{args.repeat}")
        return 0 if passed == args.repeat else 1
    finally:
        await close_watchfacts_http_client()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark authorized WatchFacts HTTPX search latency."
    )
    parser.add_argument("--query", required=True)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    parser.add_argument("--warmup", action="store_true")
    parser.add_argument("--include-playwright", action="store_true")
    args = parser.parse_args()
    if args.repeat <= 0:
        parser.error("--repeat must be positive")
    if args.timeout_ms <= 0:
        parser.error("--timeout-ms must be positive")
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
