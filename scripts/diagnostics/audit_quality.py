from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.integrations.ai_refiner import refine_search_results
from app.config import load_search_settings
from app.db import Database
from app.searching.fuzzy_diagnostics import score_fuzzy_match
from app.searching.issues import detect_suspicious_result
from app.searching.query_intent import classify_query_intent
from app.searching.result_scoring import score_result
from app.searching.search import SearchAuditEvent, WatchFactsSearchWorkflow, _search_cache_key
from app.searching.search_contracts import validate_search_diagnostics, validate_search_payload
from app.searching.search_result import SearchResult, stable_listing_id


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
ReportFormat = Literal["text", "json", "jsonl"]
PRODUCT_REFERENCE_RE = re.compile(
    r"\b(?=[A-Za-z0-9/.-]*\d)[A-Za-z0-9]+(?:/[A-Za-z0-9]+)*\b",
    re.IGNORECASE,
)
SENSITIVE_CONTEXT_RE = re.compile(
    r"\b(?:cookie|authorization|bearer|api[_-]?key|token|password|secret)\b\s*[:=]\s*\S+",
    re.IGNORECASE,
)
SENSITIVE_CONTEXT_PATH_RE = re.compile(
    r"(?:data/)?(?:\.env|watchfacts_state\.json)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AuditQuerySummary:
    audited_result_count: int
    image_missing_count: int
    image_missing_rate: float
    server_filtered_result_count: int
    scoped_stock_list_count: int
    validation_error_count: int
    suspicious_reason_counts: dict[str, int]
    image_reason_counts: dict[str, int]


@dataclass(frozen=True)
class AuditResultRow:
    rank: int
    quality_group: int
    quality_severity: int
    posted_date: str | None
    exact_reference_score: int
    descriptor_score: int
    price_evidence_score: int
    fuzzy_score: int
    fuzzy_reference_score: int
    fuzzy_descriptor_overlap_score: int
    query_intent: str
    guardrail_action: str
    score_reasons: tuple[str, ...]
    fuzzy_reason_codes: tuple[str, ...]
    suspicious_reasons: tuple[str, ...]
    has_image: bool
    image_reason: str
    scope_reason: str
    server_filtered: bool
    raw_listing_preview: str | None
    stable_listing_id: str
    listing_text: str
    seller: str | None
    source_url: str | None


@dataclass(frozen=True)
class AuditQueryReport:
    query: str
    result_count: int
    top_quality_groups: tuple[int, ...]
    summary: AuditQuerySummary
    rows: tuple[AuditResultRow, ...]
    audit_events: tuple[SearchAuditEvent, ...] = ()
    validation_errors: tuple[str, ...] = ()


def build_query_report(
    query: str,
    results: list[SearchResult],
    *,
    limit: int = DEFAULT_LIMIT,
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
    server_filtered: bool = False,
    audit_events: tuple[SearchAuditEvent, ...] = (),
    validation_errors: tuple[str, ...] = (),
) -> AuditQueryReport:
    rows: list[AuditResultRow] = []
    for index, result in enumerate(results[:limit], start=1):
        score = score_result(result, original_rank=index - 1, query=query)
        suspicious = detect_suspicious_result(
            listing_text=result.listing_text,
            raw_listing_text=result.raw_listing_text,
        )
        scope_reason = _scope_reason(result)
        image_reason = _image_reason(result, scope_reason=scope_reason)
        fuzzy = score_fuzzy_match(query, result.listing_text)
        query_intent = _query_intent_from_final_result(query)
        rows.append(
            AuditResultRow(
                rank=index,
                quality_group=score.quality_group,
                quality_severity=score.quality_severity,
                posted_date=result.posted_date,
                exact_reference_score=score.exact_reference_score,
                descriptor_score=score.descriptor_score,
                price_evidence_score=score.price_evidence_score,
                fuzzy_score=fuzzy.overall_score,
                fuzzy_reference_score=fuzzy.reference_score,
                fuzzy_descriptor_overlap_score=fuzzy.descriptor_overlap_score,
                query_intent=query_intent,
                guardrail_action=_row_guardrail_action(fuzzy),
                score_reasons=score.reasons,
                fuzzy_reason_codes=fuzzy.reason_codes,
                suspicious_reasons=tuple(issue.reason for issue in suspicious),
                has_image=bool(result.image_url),
                image_reason=image_reason,
                scope_reason=scope_reason,
                server_filtered=server_filtered,
                raw_listing_preview=_raw_listing_preview(result, snippet_chars),
                stable_listing_id=stable_listing_id(result),
                listing_text=_snippet(result.listing_text, snippet_chars),
                seller=_snippet(result.seller, 80) if result.seller else None,
                source_url=_snippet(result.source_url, 160) if result.source_url else None,
            )
        )
    summary = _query_summary(rows, validation_errors=validation_errors)
    return AuditQueryReport(
        query=query,
        result_count=len(results),
        top_quality_groups=tuple(row.quality_group for row in rows),
        summary=summary,
        rows=tuple(rows),
        audit_events=audit_events,
        validation_errors=validation_errors,
    )


def format_text_report(reports: list[AuditQueryReport]) -> str:
    lines: list[str] = []
    for report in reports:
        lines.append(
            f"=== {report.query} count={report.result_count} "
            f"top_qg={list(report.top_quality_groups)} ==="
        )
        lines.append(
            "summary="
            f"audited_result_count:{report.summary.audited_result_count} "
            f"image_missing_count:{report.summary.image_missing_count} "
            f"image_missing_rate:{report.summary.image_missing_rate:.4f} "
            f"server_filtered_results:{report.summary.server_filtered_result_count} "
            f"scoped_stock_list:{report.summary.scoped_stock_list_count}"
        )
        if report.summary.suspicious_reason_counts:
            lines.append(
                " suspicious_counts="
                + ",".join(
                    f"{reason}:{count}"
                    for reason, count in sorted(report.summary.suspicious_reason_counts.items())
                )
            )
        if report.summary.image_reason_counts:
            lines.append(
                " image_reason_counts="
                + ",".join(
                    f"{reason}:{count}"
                    for reason, count in sorted(report.summary.image_reason_counts.items())
                )
            )
        if report.validation_errors:
            lines.append(
                " validation_errors="
                + json.dumps(list(report.validation_errors), ensure_ascii=False)
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
                f"fuzzy={row.fuzzy_score} "
                f"suspicious={suspicious} image={row.has_image}"
            )
            lines.append(f" reasons={reasons}")
            if row.fuzzy_reason_codes:
                lines.append(" fuzzy_reasons=" + ",".join(row.fuzzy_reason_codes))
            lines.append(
                f" diagnostics=image_reason:{row.image_reason} "
                f"scope_reason:{row.scope_reason} server_filtered:{row.server_filtered} "
                f"query_intent:{row.query_intent} "
                f"guardrail_action:{row.guardrail_action} "
                f"stable_listing_id:{row.stable_listing_id}"
            )
            lines.append(f" text={row.listing_text}")
            if row.raw_listing_preview:
                lines.append(f" raw_preview={row.raw_listing_preview}")
            if row.seller:
                lines.append(f" seller={row.seller}")
            if row.source_url:
                lines.append(f" source={row.source_url}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_json_report(reports: list[AuditQueryReport]) -> str:
    payload = []
    for report in reports:
        report_payload = asdict(report)
        report_payload.pop("audit_events", None)
        payload.append(report_payload)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def format_jsonl_report(reports: list[AuditQueryReport]) -> str:
    lines: list[str] = []
    for report in reports:
        lines.append(json.dumps(_query_summary_event(report), ensure_ascii=False))
        for event in report.audit_events:
            lines.append(json.dumps(_audit_event_payload(event), ensure_ascii=False))
        for row in report.rows:
            lines.append(json.dumps(_final_row_event(report.query, row), ensure_ascii=False))
    return "\n".join(lines) + ("\n" if lines else "")


def summarize_jsonl_report(path: Path) -> str:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "DuckDB is required for JSONL summaries. Install requirements.txt first."
        ) from exc

    with duckdb.connect(database=":memory:") as connection:
        _create_jsonl_table(connection, "audit_rows", path)
        rows = connection.execute(
            """
            SELECT
              query,
              COALESCE(stage, type) AS stage,
              COUNT(*) AS row_count
            FROM audit_rows
            GROUP BY query, COALESCE(stage, type)
            ORDER BY query, stage
            """
        ).fetchall()
        metrics = connection.execute(
            """
            WITH totals AS (
              SELECT
                COUNT(*) FILTER (WHERE stage = 'matched') AS matched_count,
                COUNT(*) FILTER (WHERE stage = 'weak_match') AS weak_match_count,
                COUNT(*) FILTER (
                  WHERE stage = 'ambiguous_candidate'
                ) AS ambiguous_candidate_count,
                COUNT(*) FILTER (WHERE stage = 'dedupe_drop') AS dedupe_drop_count,
                COUNT(*) FILTER (WHERE stage = 'final') AS final_count,
                COUNT(*) FILTER (
                  WHERE stage = 'final'
                    AND COALESCE(fuzzy_score, 100) < 60
                ) AS low_fuzzy_included_count,
                COUNT(*) FILTER (
                  WHERE stage = 'final'
                    AND COALESCE(has_image, false) = false
                ) AS missing_image_count,
                COUNT(*) FILTER (
                  WHERE stage = 'final'
                    AND scope_reason = 'scope.stock_list'
                ) AS stock_list_scoped_count
              FROM audit_rows
            ),
            denominators AS (
              SELECT
                GREATEST(matched_count, final_count, 1) AS candidate_count,
                GREATEST(final_count, 1) AS included_count,
                *
              FROM totals
            )
            SELECT
              weak_match_count::DOUBLE / candidate_count,
              ambiguous_candidate_count::DOUBLE / candidate_count,
              dedupe_drop_count::DOUBLE / included_count,
              low_fuzzy_included_count,
              missing_image_count::DOUBLE / included_count,
              stock_list_scoped_count::DOUBLE / included_count
            FROM denominators
            """
        ).fetchone()

    lines = ["query,stage,row_count"]
    lines.extend(f"{query},{stage},{row_count}" for query, stage, row_count in rows)
    if metrics is not None:
        (
            weak_match_rate,
            ambiguous_candidate_rate,
            dedupe_drop_rate,
            low_fuzzy_included_count,
            missing_image_rate,
            stock_list_scoped_rate,
        ) = metrics
        lines.append(
            "metrics "
            f"weak_match_rate={weak_match_rate:.4f} "
            f"ambiguous_candidate_rate={ambiguous_candidate_rate:.4f} "
            f"dedupe_drop_rate={dedupe_drop_rate:.4f} "
            f"low_fuzzy_included_count={low_fuzzy_included_count} "
            f"missing_image_rate={missing_image_rate:.4f} "
            f"stock_list_scoped_rate={stock_list_scoped_rate:.4f}"
        )
    return "\n".join(lines) + "\n"


def compare_jsonl_reports(before_path: Path, after_path: Path) -> str:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "DuckDB is required for JSONL comparison. Install requirements.txt first."
        ) from exc

    with duckdb.connect(database=":memory:") as connection:
        _create_jsonl_table(connection, "before_rows", before_path)
        _create_jsonl_table(connection, "after_rows", after_path)
        rows = connection.execute(
            """
            WITH before_counts AS (
              SELECT query, COALESCE(stage, type) AS stage, COUNT(*) AS row_count
              FROM before_rows
              GROUP BY query, COALESCE(stage, type)
            ),
            after_counts AS (
              SELECT query, COALESCE(stage, type) AS stage, COUNT(*) AS row_count
              FROM after_rows
              GROUP BY query, COALESCE(stage, type)
            )
            SELECT
              COALESCE(before_counts.query, after_counts.query) AS query,
              COALESCE(before_counts.stage, after_counts.stage) AS stage,
              COALESCE(before_counts.row_count, 0) AS before_count,
              COALESCE(after_counts.row_count, 0) AS after_count,
              COALESCE(after_counts.row_count, 0)
                - COALESCE(before_counts.row_count, 0) AS delta
            FROM before_counts
            FULL OUTER JOIN after_counts
              ON before_counts.query = after_counts.query
             AND before_counts.stage = after_counts.stage
            ORDER BY query, stage
            """
        ).fetchall()

    lines = ["query,stage,before_count,after_count,delta"]
    lines.extend(
        f"{query},{stage},{before_count},{after_count},{delta}"
        for query, stage, before_count, after_count, delta in rows
    )
    return "\n".join(lines) + "\n"


def load_queries(args: argparse.Namespace) -> list[str]:
    queries: list[str] = []
    if args.queries_file:
        queries.extend(_read_query_file(Path(args.queries_file)))
    queries.extend(args.queries or [])
    if not queries:
        queries.extend(DEFAULT_AUDIT_QUERIES)
    return _dedupe_queries(queries)


async def run_audit(queries: list[str], *, limit: int) -> list[AuditQueryReport]:
    settings = load_search_settings()
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
        validation_errors = _validate_audit_payload(
            query=query,
            results=results,
            diagnostics=getattr(workflow, "last_search_diagnostics", None),
        )
        cache_metrics = database.get_search_cache_quality_metrics(
            _search_cache_key(query, settings)
        )
        reports.append(
            build_query_report(
                query,
                results,
                limit=limit,
                server_filtered=cache_metrics["server_filtered_hit_count"] > 0,
                audit_events=getattr(workflow, "last_search_audit_events", ()),
                validation_errors=tuple(validation_errors),
            )
        )
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
    parser.add_argument(
        "--summarize-jsonl",
        help="Read an audit JSONL artifact and print DuckDB stage-count summary.",
    )
    parser.add_argument(
        "--compare-jsonl",
        nargs=2,
        metavar=("BEFORE", "AFTER"),
        help="Compare two audit JSONL artifacts with DuckDB.",
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Top results per query.")
    parser.add_argument(
        "--format",
        choices=("text", "json", "jsonl"),
        default="text",
        help="Report format.",
    )
    args = parser.parse_args()
    if args.summarize_jsonl:
        print(summarize_jsonl_report(Path(args.summarize_jsonl)), end="")
        return 0
    if args.compare_jsonl:
        print(
            compare_jsonl_reports(
                Path(args.compare_jsonl[0]),
                Path(args.compare_jsonl[1]),
            ),
            end="",
        )
        return 0
    if args.limit <= 0:
        parser.error("--limit must be a positive integer")

    queries = load_queries(args)
    reports = asyncio.run(run_audit(queries, limit=args.limit))
    if args.format == "json":
        print(format_json_report(reports))
    elif args.format == "jsonl":
        print(format_jsonl_report(reports), end="")
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


def _raw_listing_preview(result: SearchResult, limit: int) -> str | None:
    raw_text = result.raw_listing_text
    if not raw_text:
        return None
    if " ".join(raw_text.split()) == " ".join(result.listing_text.split()):
        return None
    return _redacted_snippet(raw_text, limit)


def _redacted_snippet(value: str, limit: int) -> str:
    redacted = SENSITIVE_CONTEXT_RE.sub("[REDACTED]", value)
    redacted = SENSITIVE_CONTEXT_PATH_RE.sub("[REDACTED_PATH]", redacted)
    return _snippet(redacted, limit)


def _query_summary_event(report: AuditQueryReport) -> dict[str, object]:
    return {
        "type": "query_summary",
        "query": report.query,
        "result_count": report.result_count,
        "summary": asdict(report.summary),
    }


def _validate_audit_payload(
    *,
    query: str,
    results: list[SearchResult],
    diagnostics: object,
) -> list[str]:
    payload: dict[str, object] = {
        "query": query,
        "total_count": len(results),
        "offset": 0,
        "limit": None,
        "result_count": len(results),
        "has_more": False,
        "next_offset": None,
        "results": [
            {
                "result_id": f"watchfacts:audit-{index}",
                "stable_listing_id": stable_listing_id(result),
                "rank": index,
                "listing_text": result.listing_text,
                "seller": result.seller,
                "posted_date": result.posted_date,
                "source_url": result.source_url,
                "image_url": result.image_url,
            }
            for index, result in enumerate(results, start=1)
        ],
    }
    errors = validate_search_payload(payload, allow_empty=True)
    to_payload = getattr(diagnostics, "to_payload", None)
    diagnostics_payload = to_payload() if callable(to_payload) else diagnostics
    if isinstance(diagnostics_payload, dict):
        errors.extend(validate_search_diagnostics(diagnostics_payload))
    elif diagnostics is not None:
        errors.append("search_diagnostics must be an object")
    return errors


def _audit_event_payload(event: SearchAuditEvent) -> dict[str, object]:
    return {
        "type": "audit_event",
        "query": event.query,
        "stage": event.stage,
        "candidate_id": event.candidate_id,
        "decision": event.decision,
        "query_intent": event.query_intent,
        "fuzzy_score": event.fuzzy_score,
        "guardrail_action": event.guardrail_action,
        "stable_audit_id": event.stable_audit_id,
        "kept_audit_id": event.kept_audit_id,
        "rank": event.rank,
        "seller": _redacted_snippet(event.seller, 80) if event.seller else None,
        "posted_date": event.posted_date,
        "source_url": _redacted_snippet(event.source_url, 160)
        if event.source_url
        else None,
        "has_image": event.has_image,
        "text_snippet": _redacted_snippet(event.text, DEFAULT_SNIPPET_CHARS)
        if event.text
        else None,
        "reason_codes": list(event.reason_codes),
    }


def _final_row_event(query: str, row: AuditResultRow) -> dict[str, object]:
    return {
        "type": "final_result",
        "query": query,
        "stage": "final",
        "candidate_id": row.stable_listing_id,
        "decision": "include",
        "query_intent": row.query_intent,
        "fuzzy_score": row.fuzzy_score,
        "guardrail_action": row.guardrail_action,
        "stable_audit_id": row.stable_listing_id,
        "rank": row.rank,
        "seller": row.seller,
        "posted_date": row.posted_date,
        "source_url": row.source_url,
        "has_image": row.has_image,
        "text_snippet": row.listing_text,
        "reason_codes": [
            *row.score_reasons,
            *row.suspicious_reasons,
            *row.fuzzy_reason_codes,
            row.image_reason,
            row.scope_reason,
        ],
        "scope_reason": row.scope_reason,
    }


def _scope_reason(result: SearchResult) -> str:
    raw_text = " ".join((result.raw_listing_text or "").split())
    listing_text = " ".join(result.listing_text.split())
    if not raw_text or raw_text == listing_text:
        return "scope.full_listing"
    if _looks_like_stock_list(raw_text):
        return "scope.stock_list"
    return "scope.scoped"


def _image_reason(result: SearchResult, *, scope_reason: str) -> str:
    if result.image_url:
        return "image.direct"
    if scope_reason == "scope.stock_list":
        return "image.omitted_bundle_ambiguous"
    return "image.missing_source"


def _query_intent_from_final_result(query: str) -> str:
    return classify_query_intent(query).kind


def _row_guardrail_action(fuzzy) -> str:
    if fuzzy.reference_score >= 100 and fuzzy.descriptor_overlap_score < 50:
        return "warn"
    return "none"


def _create_jsonl_table(connection, table_name: str, path: Path) -> None:
    connection.execute(
        f"""
        CREATE TABLE {table_name} (
          type VARCHAR,
          query VARCHAR,
          stage VARCHAR,
          decision VARCHAR,
          has_image BOOLEAN,
          fuzzy_score INTEGER,
          scope_reason VARCHAR
        )
        """
    )
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            continue
        rows.append(
            (
                _optional_string(payload.get("type")),
                _optional_string(payload.get("query")),
                _optional_string(payload.get("stage")),
                _optional_string(payload.get("decision")),
                _optional_bool(payload.get("has_image")),
                _optional_int(payload.get("fuzzy_score")),
                _optional_string(payload.get("scope_reason")),
            )
        )
    if rows:
        connection.executemany(
            f"INSERT INTO {table_name} VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _looks_like_stock_list(value: str) -> bool:
    references = {
        token.casefold()
        for token in PRODUCT_REFERENCE_RE.findall(value)
        if _looks_like_product_reference(token)
    }
    return len(references) > 1


def _looks_like_product_reference(token: str) -> bool:
    normalized = token.casefold()
    if normalized.isdigit() and len(normalized) == 4:
        year = int(normalized)
        if 1900 <= year <= 2099:
            return False
    if len(normalized) < 4 and "/" not in normalized:
        return False
    if any(currency in normalized for currency in ("hkd", "usd", "eur", "aed")):
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?[km]", normalized):
        return False
    return True


def _query_summary(
    rows: list[AuditResultRow],
    *,
    validation_errors: tuple[str, ...] = (),
) -> AuditQuerySummary:
    image_missing_count = sum(1 for row in rows if not row.has_image)
    suspicious_counts: dict[str, int] = {}
    image_counts: dict[str, int] = {}
    for row in rows:
        image_counts[row.image_reason] = image_counts.get(row.image_reason, 0) + 1
        for reason in row.suspicious_reasons:
            suspicious_counts[reason] = suspicious_counts.get(reason, 0) + 1
    return AuditQuerySummary(
        audited_result_count=len(rows),
        image_missing_count=image_missing_count,
        image_missing_rate=image_missing_count / len(rows) if rows else 0.0,
        server_filtered_result_count=sum(1 for row in rows if row.server_filtered),
        scoped_stock_list_count=sum(
            1 for row in rows if row.scope_reason == "scope.stock_list"
        ),
        validation_error_count=len(validation_errors),
        suspicious_reason_counts=suspicious_counts,
        image_reason_counts=image_counts,
    )


if __name__ == "__main__":
    raise SystemExit(main())
