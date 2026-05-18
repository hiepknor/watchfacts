from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.ai_refiner import refine_search_results
from app.config import load_settings
from app.db import Database
from app.issues import detect_suspicious_result
from app.result_scoring import score_result
from app.search import WatchFactsSearchWorkflow
from app.telegram_bot import SearchResult


DEFAULT_AUDIT_QUERIES = (
    "5205r 2026",
    "126500ln white 2026",
    "7118/1200a grey",
    "Fpj Elegante Titanium",
    "228235a choco",
    "5712r",
    "5205r green",
    "5726/1a",
    "RM65-01 Lebron",
    "116500 panda",
)
DEFAULT_LIMIT = 5
DEFAULT_SNIPPET_CHARS = 220
ReportFormat = Literal["text", "json"]


@dataclass(frozen=True)
class AuditResultRow:
    rank: int
    quality_group: int
    quality_severity: int
    posted_date: str | None
    exact_reference_score: int
    descriptor_score: int
    price_evidence_score: int
    score_reasons: tuple[str, ...]
    suspicious_reasons: tuple[str, ...]
    listing_text: str
    seller: str | None
    source_url: str | None


@dataclass(frozen=True)
class AuditQueryReport:
    query: str
    result_count: int
    top_quality_groups: tuple[int, ...]
    rows: tuple[AuditResultRow, ...]


def build_query_report(
    query: str,
    results: list[SearchResult],
    *,
    limit: int = DEFAULT_LIMIT,
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
) -> AuditQueryReport:
    rows: list[AuditResultRow] = []
    for index, result in enumerate(results[:limit], start=1):
        score = score_result(result, original_rank=index - 1, query=query)
        suspicious = detect_suspicious_result(
            listing_text=result.listing_text,
            raw_listing_text=result.raw_listing_text,
        )
        rows.append(
            AuditResultRow(
                rank=index,
                quality_group=score.quality_group,
                quality_severity=score.quality_severity,
                posted_date=result.posted_date,
                exact_reference_score=score.exact_reference_score,
                descriptor_score=score.descriptor_score,
                price_evidence_score=score.price_evidence_score,
                score_reasons=score.reasons,
                suspicious_reasons=tuple(issue.reason for issue in suspicious),
                listing_text=_snippet(result.listing_text, snippet_chars),
                seller=_snippet(result.seller, 80) if result.seller else None,
                source_url=_snippet(result.source_url, 160) if result.source_url else None,
            )
        )
    return AuditQueryReport(
        query=query,
        result_count=len(results),
        top_quality_groups=tuple(row.quality_group for row in rows),
        rows=tuple(rows),
    )


def format_text_report(reports: list[AuditQueryReport]) -> str:
    lines: list[str] = []
    for report in reports:
        lines.append(
            f"=== {report.query} count={report.result_count} "
            f"top_qg={list(report.top_quality_groups)} ==="
        )
        if not report.rows:
            lines.append("no_results=true")
            lines.append("")
            continue
        for row in report.rows:
            reasons = ",".join(row.score_reasons) if row.score_reasons else "-"
            suspicious = ",".join(row.suspicious_reasons) if row.suspicious_reasons else "-"
            lines.append(
                f"#{row.rank} qg={row.quality_group} sev={row.quality_severity} "
                f"date={row.posted_date!r} ref={row.exact_reference_score} "
                f"desc={row.descriptor_score} price={row.price_evidence_score} "
                f"suspicious={suspicious}"
            )
            lines.append(f" reasons={reasons}")
            lines.append(f" text={row.listing_text}")
            if row.seller:
                lines.append(f" seller={row.seller}")
            if row.source_url:
                lines.append(f" source={row.source_url}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_json_report(reports: list[AuditQueryReport]) -> str:
    return json.dumps([asdict(report) for report in reports], ensure_ascii=False, indent=2)


def load_queries(args: argparse.Namespace) -> list[str]:
    queries: list[str] = []
    if args.queries_file:
        queries.extend(_read_query_file(Path(args.queries_file)))
    queries.extend(args.queries or [])
    if not queries:
        queries.extend(DEFAULT_AUDIT_QUERIES)
    return _dedupe_queries(queries)


async def run_audit(queries: list[str], *, limit: int) -> list[AuditQueryReport]:
    settings = load_settings()
    database = Database(settings.db_path)
    workflow = WatchFactsSearchWorkflow(
        settings,
        database=database,
        refine_results=(
            lambda query, results: refine_search_results(
                query,
                results,
                settings,
                database=database,
            )
        )
        if settings.hybrid_ai_mode != "off"
        else None,
    )

    reports: list[AuditQueryReport] = []
    for query in queries:
        results = await workflow.search(query)
        reports.append(build_query_report(query, results, limit=limit))
    return reports


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit WatchFacts result quality for production or local query sets.",
    )
    parser.add_argument("queries", nargs="*", help="Queries to audit. Defaults to the curated audit set.")
    parser.add_argument(
        "--queries-file",
        help="Text file with one query per line. Blank lines and # comments are ignored.",
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Top results per query.")
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Report format.",
    )
    args = parser.parse_args()
    if args.limit <= 0:
        parser.error("--limit must be a positive integer")

    queries = load_queries(args)
    reports = asyncio.run(run_audit(queries, limit=args.limit))
    if args.format == "json":
        print(format_json_report(reports))
    else:
        print(format_text_report(reports), end="")
    return 0


def _read_query_file(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


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


def _snippet(value: str | None, limit: int) -> str:
    normalized = " ".join((value or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 3, 0)].rstrip() + "..."


if __name__ == "__main__":
    raise SystemExit(main())
