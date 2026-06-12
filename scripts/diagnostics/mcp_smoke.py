from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.search_contracts import validate_search_payload


DEFAULT_SMOKE_QUERIES = (
    "5712g",
    "5205r green",
    "116500 panda",
    "126500ln white 2026",
)


async def run_smoke(
    *,
    url: str,
    queries: list[str],
    limit: int,
    timeout_seconds: float,
    allow_empty: bool,
) -> int:
    failures = 0
    for query in queries:
        try:
            payload = await _call_search(
                url=url,
                query=query,
                limit=limit,
                timeout_seconds=timeout_seconds,
            )
            errors = validate_search_payload(payload, allow_empty=allow_empty)
        except Exception as exc:
            failures += 1
            print(
                f"MCP_SMOKE query={query!r} ok=false error_type={exc.__class__.__name__}"
            )
            continue

        if errors:
            failures += 1
            print(
                f"MCP_SMOKE query={query!r} ok=false errors={json.dumps(errors)}"
            )
            continue

        print(
            "MCP_SMOKE "
            f"query={query!r} ok=true "
            f"result_count={payload.get('result_count')} "
            f"total_count={payload.get('total_count')} "
            f"has_more={payload.get('has_more')}"
        )

    print(f"SUMMARY mcp_smoke_passed={len(queries) - failures}/{len(queries)}")
    return 0 if failures == 0 else 1


async def _call_search(
    *,
    url: str,
    query: str,
    limit: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(
        url,
        timeout=timeout_seconds,
        sse_read_timeout=timeout_seconds,
    ) as (read_stream, write_stream, _get_session_id):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(
                "search",
                {
                    "query": query,
                    "limit": limit,
                    "offset": 0,
                    "include_similar": False,
                },
            )
    if result.isError:
        raise RuntimeError("MCP search tool returned an error")
    if result.structuredContent is not None:
        return dict(result.structuredContent)
    for item in result.content:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            decoded = json.loads(text)
            if isinstance(decoded, dict):
                return decoded
    raise RuntimeError("MCP search tool did not return a structured object")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test the WatchFacts MCP search tool response shape."
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("MCP_SMOKE_URL", "http://127.0.0.1:8765/mcp"),
        help="Streamable HTTP MCP URL.",
    )
    parser.add_argument(
        "--query",
        action="append",
        dest="queries",
        help="Query to smoke. May be repeated. Defaults to the representative set.",
    )
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Allow zero-result responses while still validating response shape.",
    )
    args = parser.parse_args()
    if args.limit <= 0:
        parser.error("--limit must be positive")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")

    queries = _dedupe_queries(args.queries or list(DEFAULT_SMOKE_QUERIES))
    return asyncio.run(
        run_smoke(
            url=args.url,
            queries=queries,
            limit=args.limit,
            timeout_seconds=args.timeout_seconds,
            allow_empty=args.allow_empty,
        )
    )


def _dedupe_queries(queries: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for query in queries:
        normalized = " ".join(query.split())
        if not normalized or normalized.casefold() in seen:
            continue
        seen.add(normalized.casefold())
        deduped.append(normalized)
    return deduped


if __name__ == "__main__":
    raise SystemExit(main())
