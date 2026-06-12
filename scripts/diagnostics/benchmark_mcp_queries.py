from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.searching.search_contracts import validate_search_payload


DEFAULT_BENCHMARK_QUERIES = (
    "5205r green",
    "126500ln white 2026",
    "Panerai Luminor",
    "Lange 1",
    "Reverso tribute",
    "Royal Oak Offshore",
    "Omega Speedmaster",
    "Black Bay chrono",
)


@dataclass(frozen=True)
class BenchmarkRow:
    query: str
    ok: bool
    elapsed_ms: int
    result_count: int | None = None
    total_count: int | None = None
    has_more: bool | None = None
    query_intent: str | None = None
    cache_hit: bool | None = None
    server_filtered: bool | None = None
    parsed_count: int | None = None
    matched_count: int | None = None
    weak_match_count: int | None = None
    ambiguous_candidate_count: int | None = None
    image_missing_count: int | None = None
    source_missing_count: int | None = None
    validation_errors: tuple[str, ...] = ()
    top_results: tuple[str, ...] = ()
    error_type: str | None = None
    error: str | None = None

    @property
    def warning_count(self) -> int:
        return sum(
            value or 0
            for value in (
                self.weak_match_count,
                self.ambiguous_candidate_count,
                self.image_missing_count,
                self.source_missing_count,
            )
        ) + len(self.validation_errors)


async def run_benchmark(
    *,
    url: str,
    queries: list[str],
    limit: int,
    timeout_seconds: float,
    include_similar: bool,
    allow_empty: bool,
) -> list[BenchmarkRow]:
    rows: list[BenchmarkRow] = []
    for query in _dedupe_queries(queries):
        rows.append(
            await _benchmark_query(
                url=url,
                query=query,
                limit=limit,
                timeout_seconds=timeout_seconds,
                include_similar=include_similar,
                allow_empty=allow_empty,
            )
        )
    return rows


async def _benchmark_query(
    *,
    url: str,
    query: str,
    limit: int,
    timeout_seconds: float,
    include_similar: bool,
    allow_empty: bool,
) -> BenchmarkRow:
    started_at = time.perf_counter()
    try:
        payload = await _call_search(
            url=url,
            query=query,
            limit=limit,
            timeout_seconds=timeout_seconds,
            include_similar=include_similar,
        )
    except Exception as exc:
        return BenchmarkRow(
            query=query,
            ok=False,
            elapsed_ms=_elapsed_ms(started_at),
            error_type=exc.__class__.__name__,
            error=str(exc)[:500],
        )

    errors = tuple(validate_search_payload(payload, allow_empty=allow_empty))
    return _row_from_payload(
        query=query,
        payload=payload,
        elapsed_ms=_elapsed_ms(started_at),
        validation_errors=errors,
    )


async def _call_search(
    *,
    url: str,
    query: str,
    limit: int,
    timeout_seconds: float,
    include_similar: bool,
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
                    "include_similar": include_similar,
                },
            )
    if result.isError:
        raise RuntimeError(_result_text(result) or "MCP search tool returned an error")
    if result.structuredContent is not None:
        return dict(result.structuredContent)
    for item in result.content:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            decoded = json.loads(text)
            if isinstance(decoded, dict):
                return decoded
    raise RuntimeError("MCP search tool did not return a structured object")


def _row_from_payload(
    *,
    query: str,
    payload: dict[str, Any],
    elapsed_ms: int,
    validation_errors: tuple[str, ...],
) -> BenchmarkRow:
    diagnostics = _dict_value(payload.get("search_diagnostics"))
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    top_results = tuple(
        _snippet(str(result.get("listing_text") or ""))
        for result in results[:3]
        if isinstance(result, dict)
    )
    image_missing_count = sum(
        1
        for result in results
        if isinstance(result, dict) and not _non_empty_str(result.get("image_url"))
    )
    source_missing_count = sum(
        1
        for result in results
        if isinstance(result, dict) and not _non_empty_str(result.get("source_url"))
    )

    return BenchmarkRow(
        query=query,
        ok=not validation_errors,
        elapsed_ms=elapsed_ms,
        result_count=_optional_int(payload.get("result_count")),
        total_count=_optional_int(payload.get("total_count")),
        has_more=payload.get("has_more") if isinstance(payload.get("has_more"), bool) else None,
        query_intent=_optional_str(diagnostics.get("query_intent")),
        cache_hit=diagnostics.get("cache_hit")
        if isinstance(diagnostics.get("cache_hit"), bool)
        else None,
        server_filtered=diagnostics.get("server_filtered")
        if isinstance(diagnostics.get("server_filtered"), bool)
        else None,
        parsed_count=_optional_int(diagnostics.get("parsed_count")),
        matched_count=_optional_int(diagnostics.get("matched_count")),
        weak_match_count=_optional_int(diagnostics.get("weak_match_count")),
        ambiguous_candidate_count=_optional_int(
            diagnostics.get("ambiguous_candidate_count")
        ),
        image_missing_count=image_missing_count,
        source_missing_count=source_missing_count,
        validation_errors=validation_errors,
        top_results=top_results,
    )


def render_markdown(rows: list[BenchmarkRow]) -> str:
    summary = summarize_rows(rows)
    lines = [
        "# MCP Query Benchmark",
        "",
        (
            f"Passed: {summary['passed']}/{summary['total']} | "
            f"avg: {summary.get('avg_ms', '-')}ms | "
            f"median: {summary.get('median_ms', '-')}ms | "
            f"p95: {summary.get('p95_ms', '-')}ms | "
            f"max: {summary.get('max_ms', '-')}ms"
        ),
        "",
        "| Query | OK | ms | total | intent | cache | warnings | top result |",
        "| --- | --- | ---: | ---: | --- | --- | ---: | --- |",
    ]
    for row in rows:
        top = row.top_results[0] if row.top_results else row.error or ""
        lines.append(
            "| "
            + " | ".join(
                (
                    _md(row.query),
                    "yes" if row.ok else "no",
                    str(row.elapsed_ms),
                    str(row.total_count if row.total_count is not None else "-"),
                    _md(row.query_intent or "-"),
                    _md(_bool_label(row.cache_hit)),
                    str(row.warning_count),
                    _md(top),
                )
            )
            + " |"
        )
    return "\n".join(lines)


def render_text(rows: list[BenchmarkRow]) -> str:
    lines: list[str] = []
    for row in rows:
        details = [
            f"query={row.query!r}",
            f"ok={str(row.ok).lower()}",
            f"elapsed_ms={row.elapsed_ms}",
            f"total_count={row.total_count}",
            f"result_count={row.result_count}",
            f"intent={row.query_intent}",
            f"cache_hit={row.cache_hit}",
            f"warnings={row.warning_count}",
        ]
        if row.error_type:
            details.append(f"error_type={row.error_type}")
        lines.append("MCP_BENCH " + " ".join(details))
    summary = summarize_rows(rows)
    lines.append(
        "SUMMARY "
        + " ".join(f"{key}={value}" for key, value in sorted(summary.items()))
    )
    return "\n".join(lines)


def render_jsonl(rows: list[BenchmarkRow]) -> str:
    return "\n".join(json.dumps(asdict(row), ensure_ascii=False) for row in rows)


def summarize_rows(rows: list[BenchmarkRow]) -> dict[str, int]:
    elapsed = [row.elapsed_ms for row in rows if row.ok]
    summary: dict[str, int] = {
        "passed": sum(1 for row in rows if row.ok),
        "total": len(rows),
    }
    if not elapsed:
        return summary
    summary.update(
        {
            "avg_ms": int(statistics.mean(elapsed)),
            "median_ms": int(statistics.median(elapsed)),
            "min_ms": min(elapsed),
            "max_ms": max(elapsed),
            "p95_ms": _percentile_95(elapsed),
        }
    )
    return summary


def _percentile_95(values: list[int]) -> int:
    if len(values) == 1:
        return values[0]
    return sorted(values)[min(len(values) - 1, int(len(values) * 0.95))]


def _result_text(result: object) -> str | None:
    content = getattr(result, "content", None)
    if not isinstance(content, list):
        return None
    parts = [
        text
        for item in content
        if isinstance((text := getattr(item, "text", None)), str) and text
    ]
    return " | ".join(parts) if parts else None


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


def _load_query_file(path: str) -> list[str]:
    return [
        line.strip()
        for line in Path(path).read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _dict_value(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _non_empty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _snippet(value: str, *, limit: int = 100) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _bool_label(value: bool | None) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "-"


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((time.perf_counter() - started_at) * 1000))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark WatchFacts MCP search queries and emit pasteable reports."
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
        help="Query to benchmark. May be repeated.",
    )
    parser.add_argument(
        "--query-file",
        help="File with one query per line. Blank lines and # comments are ignored.",
    )
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument(
        "--format",
        choices=("text", "markdown", "jsonl"),
        default="text",
    )
    parser.add_argument("--include-similar", action="store_true")
    parser.add_argument("--allow-empty", action="store_true")
    args = parser.parse_args()
    if args.limit <= 0:
        parser.error("--limit must be positive")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")

    queries = list(args.queries or [])
    if args.query_file:
        queries.extend(_load_query_file(args.query_file))
    if not queries:
        queries = list(DEFAULT_BENCHMARK_QUERIES)

    rows = asyncio.run(
        run_benchmark(
            url=args.url,
            queries=queries,
            limit=args.limit,
            timeout_seconds=args.timeout_seconds,
            include_similar=args.include_similar,
            allow_empty=args.allow_empty,
        )
    )
    if args.format == "jsonl":
        print(render_jsonl(rows))
    elif args.format == "markdown":
        print(render_markdown(rows))
    else:
        print(render_text(rows))
    return 0 if all(row.ok for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
