from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.search_contracts import validate_search_payload


DEFAULT_ALIAS_TOTAL_DELTA_RATIO = 0.10

DEFAULT_BENCHMARK_QUERIES = (
    "rm07-01 rg",
    "rm07-01 rosegold",
    "rm07-01 rose gold",
    "rm07-01 wg",
    "rm07-01 white gold",
    "rm07-01 mop",
    "rm07-01 mother of pearl",
    "rm07-01 rg snow",
    "rm07-01 rose gold snow",
    "126500ln white",
    "daytona panda",
    "5711 blue",
    "15500st blue",
)


@dataclass(frozen=True)
class BenchmarkRow:
    query: str
    ok: bool
    elapsed_ms: int
    run_number: int = 1
    result_count: int | None = None
    total_count: int | None = None
    has_more: bool | None = None
    query_intent: str | None = None
    canonical_query: str | None = None
    brand_candidates: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    collections: tuple[str, ...] = ()
    nicknames: tuple[str, ...] = ()
    required_descriptors: tuple[str, ...] = ()
    optional_descriptors: tuple[str, ...] = ()
    conflict_descriptors: tuple[str, ...] = ()
    retrieval_query_count: int | None = None
    retrieval_queries: tuple[str, ...] = ()
    retrieval_reason_codes: tuple[str, ...] = ()
    cache_hit: bool | None = None
    server_filtered: bool | None = None
    parsed_count: int | None = None
    matched_count: int | None = None
    weak_match_count: int | None = None
    ambiguous_candidate_count: int | None = None
    image_missing_count: int | None = None
    source_missing_count: int | None = None
    stage_timings_ms: dict[str, int] = field(default_factory=dict)
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


@dataclass(frozen=True)
class AliasRecallCheck:
    canonical_query: str
    run_number: int
    ok: bool
    min_total: int
    max_total: int
    delta: int
    delta_ratio: float
    max_delta_ratio: float
    query_totals: tuple[str, ...]


async def run_benchmark(
    *,
    url: str,
    queries: list[str],
    limit: int,
    timeout_seconds: float,
    include_similar: bool,
    allow_empty: bool,
    repeat: int = 1,
) -> list[BenchmarkRow]:
    rows: list[BenchmarkRow] = []
    for query in _dedupe_queries(queries):
        for run_number in range(1, repeat + 1):
            rows.append(
                await _benchmark_query(
                    url=url,
                    query=query,
                    limit=limit,
                    timeout_seconds=timeout_seconds,
                    include_similar=include_similar,
                    allow_empty=allow_empty,
                    run_number=run_number,
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
    run_number: int = 1,
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
            run_number=run_number,
            error_type=exc.__class__.__name__,
            error=str(exc)[:500],
        )

    errors = tuple(validate_search_payload(payload, allow_empty=allow_empty))
    return _row_from_payload(
        query=query,
        payload=payload,
        elapsed_ms=_elapsed_ms(started_at),
        validation_errors=errors,
        run_number=run_number,
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
    run_number: int = 1,
) -> BenchmarkRow:
    diagnostics = _dict_value(payload.get("search_diagnostics"))
    query_plan = _dict_value(diagnostics.get("query_plan"))
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
        run_number=run_number,
        result_count=_optional_int(payload.get("result_count")),
        total_count=_optional_int(payload.get("total_count")),
        has_more=payload.get("has_more") if isinstance(payload.get("has_more"), bool) else None,
        query_intent=_optional_str(diagnostics.get("query_intent")),
        canonical_query=_optional_str(query_plan.get("canonical_query")),
        brand_candidates=_brand_candidate_values(query_plan.get("brand_candidates")),
        references=_reference_values(query_plan.get("references")),
        collections=_string_tuple(query_plan.get("collections")),
        nicknames=_string_tuple(query_plan.get("nicknames")),
        required_descriptors=_string_tuple(query_plan.get("required_descriptors")),
        optional_descriptors=_string_tuple(query_plan.get("optional_descriptors")),
        conflict_descriptors=_string_tuple(query_plan.get("conflict_descriptors")),
        retrieval_query_count=_optional_int(diagnostics.get("retrieval_query_count")),
        retrieval_queries=_string_tuple(diagnostics.get("retrieval_queries")),
        retrieval_reason_codes=_string_tuple(diagnostics.get("retrieval_reason_codes")),
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
        stage_timings_ms=_stage_timings_value(diagnostics.get("stage_timings_ms")),
        validation_errors=validation_errors,
        top_results=top_results,
    )


def render_markdown(
    rows: list[BenchmarkRow],
    *,
    alias_checks: tuple[AliasRecallCheck, ...] | None = None,
    require_alias_recall: bool = False,
) -> str:
    summary = summarize_rows(rows)
    checks = (
        evaluate_alias_recall(rows)
        if alias_checks is None
        else alias_checks
    )
    lines = [
        "# MCP Query Benchmark",
        "",
        (
            f"Passed: {summary['passed']}/{summary['total']} | "
            f"avg: {summary.get('avg_ms', '-')}ms | "
            f"median: {summary.get('median_ms', '-')}ms | "
            f"p95: {summary.get('p95_ms', '-')}ms | "
            f"max: {summary.get('max_ms', '-')}ms | "
            f"cache hits: {summary.get('cache_hits', 0)} | "
            f"cache misses: {summary.get('cache_misses', 0)}"
        ),
        "",
        (
            "| Query | Run | OK | ms | total | canonical | intent | brand | ref | "
            "collection | nickname | desc | retrieval | reasons | cache | "
            "warnings | stages | top result |"
        ),
        (
            "| --- | ---: | --- | ---: | ---: | --- | --- | --- | --- | --- | "
            "--- | --- | --- | --- | --- | ---: | --- | --- |"
        ),
    ]
    for row in rows:
        top = row.top_results[0] if row.top_results else row.error or ""
        lines.append(
            "| "
            + " | ".join(
                (
                    _md(row.query),
                    str(row.run_number),
                    "yes" if row.ok else "no",
                    str(row.elapsed_ms),
                    str(row.total_count if row.total_count is not None else "-"),
                    _md(row.canonical_query or "-"),
                    _md(row.query_intent or "-"),
                    _md(_csv(row.brand_candidates)),
                    _md(_csv(row.references)),
                    _md(_csv(row.collections)),
                    _md(_csv(row.nicknames)),
                    _md(_descriptor_summary(row)),
                    _md(_retrieval_summary(row)),
                    _md(_csv(row.retrieval_reason_codes)),
                    _md(_bool_label(row.cache_hit)),
                    str(row.warning_count),
                    _md(_stage_timing_summary(row.stage_timings_ms)),
                    _md(top),
                )
            )
            + " |"
        )
    lines.extend(
        _alias_recall_markdown_lines(
            checks,
            require_alias_recall=require_alias_recall,
        )
    )
    return "\n".join(lines)


def render_text(
    rows: list[BenchmarkRow],
    *,
    alias_checks: tuple[AliasRecallCheck, ...] | None = None,
    require_alias_recall: bool = False,
) -> str:
    lines: list[str] = []
    checks = (
        evaluate_alias_recall(rows)
        if alias_checks is None
        else alias_checks
    )
    for row in rows:
        details = [
            f"query={row.query!r}",
            f"run={row.run_number}",
            f"ok={str(row.ok).lower()}",
            f"elapsed_ms={row.elapsed_ms}",
            f"total_count={row.total_count}",
            f"result_count={row.result_count}",
            f"intent={row.query_intent}",
            f"canonical={row.canonical_query!r}",
            f"brands={_csv(row.brand_candidates)}",
            f"collections={_csv(row.collections)}",
            f"nicknames={_csv(row.nicknames)}",
            f"refs={_csv(row.references)}",
            f"descriptors={_descriptor_summary(row)}",
            f"retrieval_count={row.retrieval_query_count}",
            f"retrieval_queries={_quoted_csv(row.retrieval_queries)}",
            f"retrieval_reasons={_csv(row.retrieval_reason_codes)}",
            f"cache_hit={row.cache_hit}",
            f"warnings={row.warning_count}",
        ]
        if row.stage_timings_ms:
            details.append(f"stages={_stage_timing_summary(row.stage_timings_ms)}")
        if row.error_type:
            details.append(f"error_type={row.error_type}")
        lines.append("MCP_BENCH " + " ".join(details))
    lines.extend(
        _alias_recall_text_lines(
            checks,
            require_alias_recall=require_alias_recall,
        )
    )
    summary = summarize_rows(rows)
    lines.append(
        "SUMMARY "
        + " ".join(f"{key}={value}" for key, value in sorted(summary.items()))
    )
    return "\n".join(lines)


def render_jsonl(rows: list[BenchmarkRow]) -> str:
    return "\n".join(json.dumps(asdict(row), ensure_ascii=False) for row in rows)


def evaluate_alias_recall(
    rows: list[BenchmarkRow],
    *,
    max_delta_ratio: float = DEFAULT_ALIAS_TOTAL_DELTA_RATIO,
) -> tuple[AliasRecallCheck, ...]:
    groups: dict[tuple[str, int], dict[str, int]] = {}
    for row in rows:
        if not row.ok or row.total_count is None or not row.canonical_query:
            continue
        key = (row.canonical_query, row.run_number)
        groups.setdefault(key, {})[row.query] = row.total_count

    checks: list[AliasRecallCheck] = []
    for (canonical_query, run_number), query_totals in sorted(groups.items()):
        if len(query_totals) < 2:
            continue
        totals = list(query_totals.values())
        min_total = min(totals)
        max_total = max(totals)
        delta = max_total - min_total
        raw_delta_ratio = delta / max(max_total, 1)
        checks.append(
            AliasRecallCheck(
                canonical_query=canonical_query,
                run_number=run_number,
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


def alias_recall_passed(
    checks: tuple[AliasRecallCheck, ...],
    *,
    require_evaluation: bool,
) -> bool:
    if require_evaluation and not checks:
        return False
    return all(check.ok for check in checks)


def summarize_rows(rows: list[BenchmarkRow]) -> dict[str, int]:
    elapsed = [row.elapsed_ms for row in rows if row.ok]
    summary: dict[str, int] = {
        "passed": sum(1 for row in rows if row.ok),
        "total": len(rows),
        "cache_hits": sum(1 for row in rows if row.cache_hit is True),
        "cache_misses": sum(1 for row in rows if row.cache_hit is False),
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


def _stage_timings_value(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): int(timing)
        for key, timing in value.items()
        if isinstance(key, str)
        and isinstance(timing, int)
        and not isinstance(timing, bool)
        and timing >= 0
    }


def _stage_timing_summary(stage_timings_ms: dict[str, int]) -> str:
    if not stage_timings_ms:
        return "-"
    ordered_keys = (
        "cache_read",
        "in_flight_wait",
        "concurrency_wait",
        "watchfacts_fetch",
        "parse",
        "match",
        "result_pipeline",
        "persist",
        "total",
    )
    parts = [
        f"{key}:{stage_timings_ms[key]}"
        for key in ordered_keys
        if key in stage_timings_ms
    ]
    parts.extend(
        f"{key}:{value}"
        for key, value in sorted(stage_timings_ms.items())
        if key not in ordered_keys
    )
    return ",".join(parts)


def _brand_candidate_values(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    candidates: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        brand = _optional_str(item.get("brand"))
        if brand is None:
            continue
        confidence = _optional_str(item.get("confidence"))
        candidates.append(f"{brand}:{confidence}" if confidence else brand)
    return tuple(candidates)


def _reference_values(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    references: list[str] = []
    for item in value:
        if isinstance(item, list):
            reference = "".join(str(part) for part in item if _non_empty_str(part))
        else:
            reference = str(item) if _non_empty_str(item) else ""
        if reference:
            references.append(reference)
    return tuple(references)


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if _non_empty_str(item))


def _csv(values: tuple[str, ...]) -> str:
    return ",".join(values) if values else "-"


def _quoted_csv(values: tuple[str, ...]) -> str:
    return ",".join(repr(value) for value in values) if values else "-"


def _retrieval_summary(row: BenchmarkRow) -> str:
    count = row.retrieval_query_count
    if count is None and not row.retrieval_queries:
        return "-"
    return f"{count if count is not None else '-'}:{_csv(row.retrieval_queries)}"


def _alias_recall_text_lines(
    checks: tuple[AliasRecallCheck, ...],
    *,
    require_alias_recall: bool,
) -> list[str]:
    if not checks:
        if require_alias_recall:
            return ["ALIAS_RECALL ok=false reason=no_canonical_alias_groups"]
        return []
    return [
        (
            "ALIAS_RECALL "
            f"canonical={check.canonical_query!r} "
            f"run={check.run_number} "
            f"ok={str(check.ok).lower()} "
            f"min_total={check.min_total} "
            f"max_total={check.max_total} "
            f"delta={check.delta} "
            f"delta_ratio={check.delta_ratio:.3f} "
            f"max_delta_ratio={check.max_delta_ratio:.3f} "
            f"queries={_quoted_csv(check.query_totals)}"
        )
        for check in checks
    ]


def _alias_recall_markdown_lines(
    checks: tuple[AliasRecallCheck, ...],
    *,
    require_alias_recall: bool,
) -> list[str]:
    if not checks and not require_alias_recall:
        return []
    lines = [
        "",
        "## Alias Recall",
        "",
        "| Canonical | Run | OK | min | max | delta | ratio | threshold | queries |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    if not checks:
        lines.append("| - | - | no | - | - | - | - | - | no canonical alias groups |")
        return lines
    for check in checks:
        lines.append(
            "| "
            + " | ".join(
                (
                    _md(check.canonical_query),
                    str(check.run_number),
                    "yes" if check.ok else "no",
                    str(check.min_total),
                    str(check.max_total),
                    str(check.delta),
                    f"{check.delta_ratio:.3f}",
                    f"{check.max_delta_ratio:.3f}",
                    _md(_csv(check.query_totals)),
                )
            )
            + " |"
        )
    return lines


def _descriptor_summary(row: BenchmarkRow) -> str:
    parts: list[str] = []
    if row.required_descriptors:
        parts.append(f"required:{_csv(row.required_descriptors)}")
    if row.optional_descriptors:
        parts.append(f"optional:{_csv(row.optional_descriptors)}")
    if row.conflict_descriptors:
        parts.append(f"conflict:{_csv(row.conflict_descriptors)}")
    return ";".join(parts) if parts else "-"


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
        "--repeat",
        type=int,
        default=1,
        help="Run each deduped query this many times. Useful for cold/warm cache comparisons.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "markdown", "jsonl"),
        default="text",
    )
    parser.add_argument("--include-similar", action="store_true")
    parser.add_argument("--allow-empty", action="store_true")
    parser.add_argument(
        "--alias-total-delta-ratio",
        type=float,
        default=DEFAULT_ALIAS_TOTAL_DELTA_RATIO,
        help="Maximum allowed total_count delta ratio across rows with the same canonical query.",
    )
    parser.add_argument(
        "--require-alias-recall",
        action="store_true",
        help="Fail if no canonical alias groups can be evaluated.",
    )
    parser.add_argument(
        "--skip-alias-recall-check",
        action="store_true",
        help="Do not fail the benchmark on alias recall comparison.",
    )
    args = parser.parse_args()
    if args.limit <= 0:
        parser.error("--limit must be positive")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    if args.repeat <= 0:
        parser.error("--repeat must be positive")
    if args.alias_total_delta_ratio < 0:
        parser.error("--alias-total-delta-ratio must be non-negative")

    queries = list(args.queries or [])
    if args.query_file:
        queries.extend(_load_query_file(args.query_file))
    using_default_queries = not queries
    if using_default_queries:
        queries = list(DEFAULT_BENCHMARK_QUERIES)

    rows = asyncio.run(
        run_benchmark(
            url=args.url,
            queries=queries,
            limit=args.limit,
            timeout_seconds=args.timeout_seconds,
            include_similar=args.include_similar,
            allow_empty=args.allow_empty,
            repeat=args.repeat,
        )
    )
    alias_checks = evaluate_alias_recall(
        rows,
        max_delta_ratio=args.alias_total_delta_ratio,
    )
    require_alias_recall = (
        False
        if args.skip_alias_recall_check
        else args.require_alias_recall or using_default_queries
    )
    if args.format == "jsonl":
        print(render_jsonl(rows))
    elif args.format == "markdown":
        print(
            render_markdown(
                rows,
                alias_checks=alias_checks,
                require_alias_recall=require_alias_recall,
            )
        )
    else:
        print(
            render_text(
                rows,
                alias_checks=alias_checks,
                require_alias_recall=require_alias_recall,
            )
        )
    alias_ok = (
        True
        if args.skip_alias_recall_check
        else alias_recall_passed(
            alias_checks,
            require_evaluation=require_alias_recall,
        )
    )
    return 0 if all(row.ok for row in rows) and alias_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
