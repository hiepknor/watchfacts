from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate draft matcher regression tests from /issues_export JSON."
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="Path to /issues_export JSON. Reads stdin when omitted.",
    )
    parser.add_argument(
        "--case-prefix",
        default="exported_issue",
        help="Prefix for generated pytest case ids.",
    )
    args = parser.parse_args()

    text = Path(args.input).read_text() if args.input else sys.stdin.read()
    issues = load_exported_issues(text)
    sys.stdout.write(render_pytest_module(issues, case_prefix=args.case_prefix))
    return 0


def load_exported_issues(text: str) -> list[dict[str, Any]]:
    payload = json.loads(_extract_json_payload(text))
    if not isinstance(payload, list):
        raise ValueError("Expected /issues_export payload to be a JSON list")
    return [issue for issue in payload if isinstance(issue, dict)]


def render_pytest_module(
    issues: list[dict[str, Any]],
    *,
    case_prefix: str = "exported_issue",
) -> str:
    cases = [_issue_to_case(issue, index, case_prefix) for index, issue in enumerate(issues, start=1)]
    return (
        "import pytest\n\n"
        "from app.matcher import extract_relevant_listing_text\n\n\n"
        f"CASES = {_pretty_repr(cases)}\n\n\n"
        "@pytest.mark.parametrize(\"case\", CASES, ids=lambda case: case[\"name\"])\n"
        "def test_exported_issue_regression(case):\n"
        "    assert extract_relevant_listing_text(case[\"query\"], case[\"raw_text\"]) == case[\"expected_text\"]\n"
    )


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


def _issue_to_case(issue: dict[str, Any], index: int, case_prefix: str) -> dict[str, str]:
    issue_id = issue.get("id", index)
    issue_type = _string_value(issue.get("type"), "issue")
    reason = _string_value(issue.get("reason"), "unknown")
    query = _string_value(issue.get("query"), "")
    shown_text = _string_value(issue.get("shown_text"), "")
    raw_text = _string_value(issue.get("raw_text"), shown_text)
    expected_text = _default_expected_text(issue, shown_text=shown_text, raw_text=raw_text)

    return {
        "name": _case_name(case_prefix, issue_type, issue_id, reason, query),
        "query": query,
        "raw_text": raw_text,
        "expected_text": expected_text,
        "shown_text": shown_text,
        "source_url": _string_value(issue.get("source_url"), ""),
        "reason": reason,
    }


def _default_expected_text(
    issue: dict[str, Any],
    *,
    shown_text: str,
    raw_text: str,
) -> str:
    explicit_expected = issue.get("expected_text") or issue.get("suggested_text")
    if isinstance(explicit_expected, str) and explicit_expected.strip():
        return explicit_expected

    reason = _string_value(issue.get("reason"), "")
    if reason in {
        "missing_info",
        "ends_with_currency",
        "ends_with_price_marker",
        "missing_price_after_currency",
        "raw_much_longer",
    }:
        return raw_text
    return shown_text or raw_text


def _case_name(prefix: str, issue_type: str, issue_id: object, reason: str, query: str) -> str:
    raw = f"{prefix}_{issue_type}_{issue_id}_{reason}_{query}"
    name = re.sub(r"[^a-zA-Z0-9]+", "_", raw).strip("_").lower()
    return name or f"{prefix}_{issue_id}"


def _string_value(value: object, default: str) -> str:
    if value is None:
        return default
    return str(value)


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
