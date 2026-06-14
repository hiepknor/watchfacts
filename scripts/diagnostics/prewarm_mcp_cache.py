from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.diagnostics.benchmark_mcp_queries import (
    DEFAULT_ALIAS_TOTAL_DELTA_RATIO,
    DEFAULT_BENCHMARK_QUERIES,
    _call_search,
    _dedupe_queries,
    _load_query_file,
)


DEFAULT_PREWARM_QUERIES = (
    "5205r green",
    "126500ln white 2026",
    "5712r",
    "116500 panda",
    "FPJ Elegante Titanium",
    "7118/1200a grey",
    "228235a choco",
)


@dataclass(frozen=True)
class PrewarmRow:
    query: str
    ok: bool
    elapsed_ms: int
    cache_hit: bool | None = None
    total_count: int | None = None
    result_count: int | None = None
    canonical_query: str | None = None
    error_type: str | None = None
    error: str | None = None
    pass_name: str = "warm"


@dataclass(frozen=True)
class PrewarmAliasRecallCheck:
    canonical_query: str
    pass_name: str
    ok: bool
    min_total: int
    max_total: int
    delta: int
    delta_ratio: float
    max_delta_ratio: float
    query_totals: tuple[str, ...]


async def prewarm_queries(
    *,
    url: str,
    queries: list[str],
    limit: int,
    timeout_seconds: float,
    verify_hot: bool,
) -> list[PrewarmRow]:
    rows: list[PrewarmRow] = []
    deduped = _dedupe_queries(queries)
    rows.extend(
        await _run_pass(
            url=url,
            queries=deduped,
            limit=limit,
            timeout_seconds=timeout_seconds,
            pass_name="warm",
        )
    )
    if verify_hot:
        rows.extend(
            await _run_pass(
                url=url,
                queries=deduped,
                limit=limit,
                timeout_seconds=timeout_seconds,
                pass_name="verify",
            )
        )
    return rows


async def _run_pass(
    *,
    url: str,
    queries: list[str],
    limit: int,
    timeout_seconds: float,
    pass_name: str,
) -> list[PrewarmRow]:
    rows: list[PrewarmRow] = []
    for query in queries:
        rows.append(
            await _prewarm_query(
                url=url,
                query=query,
                limit=limit,
                timeout_seconds=timeout_seconds,
                pass_name=pass_name,
            )
        )
    return rows


async def _prewarm_query(
    *,
    url: str,
    query: str,
    limit: int,
    timeout_seconds: float,
    pass_name: str,
) -> PrewarmRow:
    started_at = time.perf_counter()
    try:
        payload = await _call_search(
            url=url,
            query=query,
            limit=limit,
            timeout_seconds=timeout_seconds,
            include_similar=True,
        )
    except Exception as exc:
        return PrewarmRow(
            query=query,
            ok=False,
            elapsed_ms=_elapsed_ms(started_at),
            error_type=exc.__class__.__name__,
            error=str(exc)[:500],
            pass_name=pass_name,
        )
    return _row_from_payload(
        query=query,
        payload=payload,
        elapsed_ms=_elapsed_ms(started_at),
        pass_name=pass_name,
    )


def _row_from_payload(
    *,
    query: str,
    payload: dict[str, Any],
    elapsed_ms: int,
    pass_name: str,
) -> PrewarmRow:
    diagnostics = payload.get("search_diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    query_plan = _dict_value(diagnostics.get("query_plan"))
    return PrewarmRow(
        query=query,
        ok=True,
        elapsed_ms=elapsed_ms,
        cache_hit=diagnostics.get("cache_hit")
        if isinstance(diagnostics.get("cache_hit"), bool)
        else None,
        total_count=_optional_int(payload.get("total_count")),
        result_count=_optional_int(payload.get("result_count")),
        canonical_query=_optional_str(query_plan.get("canonical_query")),
        pass_name=pass_name,
    )


def render_text(
    rows: list[PrewarmRow],
    *,
    alias_checks: tuple[PrewarmAliasRecallCheck, ...] | None = None,
    require_alias_recall: bool = False,
) -> str:
    lines: list[str] = []
    for row in rows:
        parts = [
            f"pass={row.pass_name}",
            f"query={row.query!r}",
            f"ok={str(row.ok).lower()}",
            f"elapsed_ms={row.elapsed_ms}",
            f"cache_hit={_bool_label(row.cache_hit)}",
            f"total_count={row.total_count}",
            f"result_count={row.result_count}",
        ]
        if row.canonical_query:
            parts.append(f"canonical={row.canonical_query!r}")
        if row.error_type:
            parts.append(f"error_type={row.error_type}")
        lines.append("MCP_PREWARM " + " ".join(parts))
    lines.extend(
        _alias_recall_text_lines(
            alias_checks or (),
            require_alias_recall=require_alias_recall,
        )
    )
    lines.append(
        "SUMMARY "
        + " ".join(f"{key}={value}" for key, value in sorted(summarize_rows(rows).items()))
    )
    return "\n".join(lines)


def render_jsonl(rows: list[PrewarmRow]) -> str:
    return "\n".join(json.dumps(asdict(row), ensure_ascii=False) for row in rows)


def summarize_rows(rows: list[PrewarmRow]) -> dict[str, int]:
    ok_rows = [row for row in rows if row.ok]
    hot_rows = [row for row in rows if row.cache_hit is True]
    elapsed = [row.elapsed_ms for row in ok_rows]
    summary: dict[str, int] = {
        "passed": len(ok_rows),
        "total": len(rows),
        "cache_hits": len(hot_rows),
        "cache_misses": sum(1 for row in rows if row.cache_hit is False),
    }
    if elapsed:
        summary["avg_ms"] = int(sum(elapsed) / len(elapsed))
        summary["max_ms"] = max(elapsed)
        summary["min_ms"] = min(elapsed)
    return summary


def evaluate_prewarm_alias_recall(
    rows: list[PrewarmRow],
    *,
    max_delta_ratio: float = DEFAULT_ALIAS_TOTAL_DELTA_RATIO,
) -> tuple[PrewarmAliasRecallCheck, ...]:
    groups: dict[tuple[str, str], dict[str, int]] = {}
    for row in rows:
        if not row.ok or row.total_count is None or not row.canonical_query:
            continue
        key = (row.canonical_query, row.pass_name)
        groups.setdefault(key, {})[row.query] = row.total_count

    checks: list[PrewarmAliasRecallCheck] = []
    for (canonical_query, pass_name), query_totals in sorted(groups.items()):
        if len(query_totals) < 2:
            continue
        totals = list(query_totals.values())
        min_total = min(totals)
        max_total = max(totals)
        delta = max_total - min_total
        raw_delta_ratio = delta / max(max_total, 1)
        checks.append(
            PrewarmAliasRecallCheck(
                canonical_query=canonical_query,
                pass_name=pass_name,
                ok=raw_delta_ratio <= max_delta_ratio,
                min_total=min_total,
                max_total=max_total,
                delta=delta,
                delta_ratio=round(raw_delta_ratio, 3),
                max_delta_ratio=max_delta_ratio,
                query_totals=tuple(
                    f"{query}:{total}" for query, total in sorted(query_totals.items())
                ),
            )
        )
    return tuple(checks)


def prewarm_passed(
    rows: list[PrewarmRow],
    *,
    alias_checks: tuple[PrewarmAliasRecallCheck, ...] = (),
    require_alias_recall: bool = False,
    require_hot_cache: bool = False,
) -> bool:
    if not all(row.ok for row in rows):
        return False
    if require_hot_cache:
        verify_rows = [row for row in rows if row.pass_name == "verify"]
        if not verify_rows or any(row.cache_hit is not True for row in verify_rows):
            return False
    if require_alias_recall and not alias_checks:
        return False
    return all(check.ok for check in alias_checks)


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _dict_value(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _bool_label(value: bool | None) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "unknown"


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((time.perf_counter() - started_at) * 1000))


def _alias_recall_text_lines(
    checks: tuple[PrewarmAliasRecallCheck, ...],
    *,
    require_alias_recall: bool,
) -> list[str]:
    if not checks:
        if require_alias_recall:
            return ["MCP_PREWARM_ALIAS ok=false reason=no_canonical_alias_groups"]
        return []
    lines: list[str] = []
    for check in checks:
        lines.append(
            "MCP_PREWARM_ALIAS "
            f"pass={check.pass_name} "
            f"canonical={check.canonical_query!r} "
            f"ok={str(check.ok).lower()} "
            f"min={check.min_total} "
            f"max={check.max_total} "
            f"delta={check.delta} "
            f"ratio={check.delta_ratio:.3f} "
            f"threshold={check.max_delta_ratio:.3f} "
            f"queries={','.join(check.query_totals)!r}"
        )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prewarm WatchFacts MCP search cache for common production queries."
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
        help="Query to prewarm. May be repeated.",
    )
    parser.add_argument(
        "--query-file",
        help="File with one query per line. Blank lines and # comments are ignored.",
    )
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--verify-hot", action="store_true")
    parser.add_argument("--use-benchmark-defaults", action="store_true")
    parser.add_argument("--format", choices=("text", "jsonl"), default="text")
    parser.add_argument(
        "--alias-total-delta-ratio",
        type=float,
        default=DEFAULT_ALIAS_TOTAL_DELTA_RATIO,
        help="Maximum allowed total_count delta ratio across benchmark alias groups.",
    )
    args = parser.parse_args()
    if args.limit <= 0:
        parser.error("--limit must be positive")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    if args.alias_total_delta_ratio < 0:
        parser.error("--alias-total-delta-ratio must be non-negative")

    queries = list(args.queries or [])
    if args.query_file:
        queries.extend(_load_query_file(args.query_file))
    if not queries:
        queries = list(
            DEFAULT_BENCHMARK_QUERIES
            if args.use_benchmark_defaults
            else DEFAULT_PREWARM_QUERIES
        )

    rows = asyncio.run(
        prewarm_queries(
            url=args.url,
            queries=queries,
            limit=args.limit,
            timeout_seconds=args.timeout_seconds,
            verify_hot=args.verify_hot,
        )
    )
    alias_checks = (
        evaluate_prewarm_alias_recall(
            rows,
            max_delta_ratio=args.alias_total_delta_ratio,
        )
        if args.use_benchmark_defaults
        else ()
    )
    require_alias_recall = args.use_benchmark_defaults
    if args.format == "jsonl":
        print(render_jsonl(rows))
    else:
        print(
            render_text(
                rows,
                alias_checks=alias_checks,
                require_alias_recall=require_alias_recall,
            )
        )
    return (
        0
        if prewarm_passed(
            rows,
            alias_checks=alias_checks,
            require_alias_recall=require_alias_recall,
            require_hot_cache=args.verify_hot,
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
