from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate draft quality/scoring regression tests from audit JSON."
    )
    parser.add_argument(
        "input",
        nargs="?",
        help=(
            "Path to scripts/diagnostics/audit_quality.py --format json output. "
            "Reads stdin when omitted."
        ),
    )
    parser.add_argument(
        "--case-prefix",
        default="audit_issue",
        help="Prefix for generated pytest case ids.",
    )
    parser.add_argument(
        "--include-clean",
        action="store_true",
        help="Include clean qg=0 rows. By default only suspicious/non-clean rows are emitted.",
    )
    args = parser.parse_args()

    text = Path(args.input).read_text(encoding="utf-8") if args.input else sys.stdin.read()
    reports = load_audit_reports(text)
    sys.stdout.write(
        render_pytest_module(
            reports,
            case_prefix=args.case_prefix,
            include_clean=args.include_clean,
        )
    )
    return 0


def load_audit_reports(text: str) -> list[dict[str, Any]]:
    stripped = text.strip()
    if _looks_like_jsonl(stripped):
        return _load_audit_jsonl_reports(stripped)
    payload = json.loads(_extract_json_payload(text))
    if not isinstance(payload, list):
        raise ValueError("Expected audit payload to be a JSON list")
    return [report for report in payload if isinstance(report, dict)]


def _load_audit_jsonl_reports(text: str) -> list[dict[str, Any]]:
    reports_by_query: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            continue
        query = _string_value(payload.get("query"), "")
        if not query:
            continue
        report = reports_by_query.setdefault(query, {"query": query, "rows": []})
        if payload.get("type") != "final_result":
            continue
        reason_codes = _string_list(payload.get("reason_codes"))
        report["rows"].append(
            {
                "listing_text": _string_value(payload.get("text_snippet"), ""),
                "posted_date": payload.get("posted_date"),
                "quality_group": _jsonl_quality_group(reason_codes),
                "price_evidence_score": _jsonl_price_evidence_score(reason_codes),
                "score_reasons": [
                    reason
                    for reason in reason_codes
                    if reason.startswith("guardrail.")
                ],
                "suspicious_reasons": [
                    reason.removeprefix("suspicious.")
                    for reason in reason_codes
                    if reason.startswith("suspicious.")
                ],
            }
        )
    return list(reports_by_query.values())


def _looks_like_jsonl(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return bool(lines) and all(line.startswith("{") for line in lines)


def _jsonl_quality_group(reason_codes: list[str]) -> int:
    if any(reason.startswith("guardrail.") for reason in reason_codes):
        return 1
    if any(reason == "quality.suspicious" for reason in reason_codes):
        return 2
    if any(reason == "quality.missing_price" for reason in reason_codes):
        return 1
    return 0


def _jsonl_price_evidence_score(reason_codes: list[str]) -> int:
    return 1 if "price.visible" in reason_codes else 0


def render_pytest_module(
    reports: list[dict[str, Any]],
    *,
    case_prefix: str = "audit_issue",
    include_clean: bool = False,
) -> str:
    cases = audit_reports_to_cases(
        reports,
        case_prefix=case_prefix,
        include_clean=include_clean,
    )
    return (
        "import pytest\n\n"
        "from app.issues import detect_suspicious_result\n"
        "from app.result_scoring import score_result\n"
        "from app.search_result import SearchResult\n\n\n"
        f"CASES = {_pretty_repr(cases)}\n\n\n"
        "@pytest.mark.parametrize(\"case\", CASES, ids=lambda case: case[\"name\"])\n"
        "def test_audit_quality_regression(case):\n"
        "    result = SearchResult(\n"
        "        case[\"listing_text\"],\n"
        "        posted_date=case[\"posted_date\"],\n"
        "    )\n"
        "    score = score_result(result, original_rank=0, query=case[\"query\"])\n"
        "    issues = detect_suspicious_result(listing_text=result.listing_text)\n"
        "    assert score.quality_group == case[\"expected_quality_group\"]\n"
        "    assert score.price_evidence_score == case[\"expected_price_evidence_score\"]\n"
        "    for reason in case[\"expected_score_reasons\"]:\n"
        "        assert reason in score.reasons\n"
        "    assert [issue.reason for issue in issues] == case[\"expected_suspicious_reasons\"]\n"
    )


def audit_reports_to_cases(
    reports: list[dict[str, Any]],
    *,
    case_prefix: str,
    include_clean: bool,
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for report_index, report in enumerate(reports, start=1):
        query = _string_value(report.get("query"), "")
        rows = report.get("rows")
        if not isinstance(rows, list):
            continue
        for row_index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                continue
            quality_group = _int_value(row.get("quality_group"), 0)
            suspicious_reasons = _string_list(row.get("suspicious_reasons"))
            score_reasons = _string_list(row.get("score_reasons"))
            if (
                not include_clean
                and quality_group == 0
                and not suspicious_reasons
                and not score_reasons
            ):
                continue
            listing_text = _string_value(row.get("listing_text"), "")
            if not query or not listing_text:
                continue
            cases.append(
                {
                    "name": _case_name(
                        case_prefix,
                        report_index=report_index,
                        row_index=row_index,
                        query=query,
                        quality_group=quality_group,
                        suspicious_reasons=suspicious_reasons,
                    ),
                    "query": query,
                    "listing_text": listing_text,
                    "posted_date": row.get("posted_date"),
                    "expected_quality_group": quality_group,
                    "expected_price_evidence_score": _int_value(
                        row.get("price_evidence_score"),
                        0,
                    ),
                    "expected_score_reasons": score_reasons,
                    "expected_suspicious_reasons": suspicious_reasons,
                }
            )
    return cases


def _extract_json_payload(text: str) -> str:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL)
    if fenced:
        return fenced.group(1).strip()

    start = stripped.find("[")
    end = stripped.rfind("]")
    if start >= 0 and end >= start:
        return stripped[start : end + 1]
    return stripped


def _case_name(
    prefix: str,
    *,
    report_index: int,
    row_index: int,
    query: str,
    quality_group: int,
    suspicious_reasons: list[str],
) -> str:
    reason = "_".join(suspicious_reasons) if suspicious_reasons else f"qg_{quality_group}"
    raw = f"{prefix}_{report_index}_{row_index}_{reason}_{query}"
    name = re.sub(r"[^a-zA-Z0-9]+", "_", raw).strip("_").lower()
    return name or f"{prefix}_{report_index}_{row_index}"


def _string_value(value: object, default: str) -> str:
    if value is None:
        return default
    return str(value)


def _int_value(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _pretty_repr(value: object, indent: int = 0) -> str:
    if isinstance(value, list):
        if not value:
            return "[]"
        inner = ",\n".join(f"{' ' * (indent + 4)}{_pretty_repr(item, indent + 4)}" for item in value)
        return "[\n" + inner + f",\n{' ' * indent}]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = []
        for key, item in value.items():
            items.append(
                f"{' ' * (indent + 4)}{key!r}: {_pretty_repr(item, indent + 4)}"
            )
        return "{\n" + ",\n".join(items) + f",\n{' ' * indent}}}"
    return repr(value)


if __name__ == "__main__":
    raise SystemExit(main())
