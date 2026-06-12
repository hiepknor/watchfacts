from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import Settings, load_search_settings


Complete = Callable[[str], Awaitable[str]]
DEFAULT_MAX_PROMPT_ROWS = 40
DEFAULT_SNIPPET_CHARS = 220
SENSITIVE_CONTEXT_RE = re.compile(
    r"\b(?:cookie|authorization|bearer|api[_-]?key|token|password|secret)\b\s*[:=]\s*\S+",
    re.IGNORECASE,
)
SENSITIVE_CONTEXT_PATH_RE = re.compile(
    r"(?:data/)?(?:\.env|watchfacts_state\.json)",
    re.IGNORECASE,
)
SENSITIVE_KEY_RE = re.compile(
    r"(?:cookie|authorization|api[_-]?key|token|password|secret|browser_state|storage_state)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AuditArtifact:
    path: Path
    rows: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class AuditTriageSummary:
    total_rows: int
    final_result_count: int
    weak_match_count: int
    ambiguous_candidate_count: int
    dedupe_drop_count: int
    missing_image_count: int
    stock_list_scoped_count: int
    query_counts: dict[str, int]
    stage_counts: dict[str, int]
    reason_counts: dict[str, int]


@dataclass(frozen=True)
class AITriagePattern:
    issue_type: str
    priority: str
    evidence: str
    recommended_action: str
    fixture_hint: str


@dataclass(frozen=True)
class AITriageReport:
    summary: str
    risk_level: str
    issue_patterns: tuple[AITriagePattern, ...]
    next_steps: tuple[str, ...]


def load_audit_artifact(path: Path) -> AuditArtifact:
    text = path.read_text(encoding="utf-8")
    stripped = _strip_json_code_fence(text.strip())
    rows = _load_json_or_jsonl_rows(stripped)
    return AuditArtifact(path=path, rows=tuple(rows))


def summarize_artifact(artifact: AuditArtifact) -> AuditTriageSummary:
    query_counts: Counter[str] = Counter()
    stage_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    final_result_count = 0
    weak_match_count = 0
    ambiguous_candidate_count = 0
    dedupe_drop_count = 0
    missing_image_count = 0
    stock_list_scoped_count = 0

    for row in artifact.rows:
        query = _optional_text(row.get("query"))
        if query:
            query_counts[query] += 1

        stage = _row_stage(row)
        if stage:
            stage_counts[stage] += 1

        if stage == "final":
            final_result_count += 1
            if row.get("has_image") is False:
                missing_image_count += 1
            if row.get("scope_reason") == "scope.stock_list":
                stock_list_scoped_count += 1
        elif stage == "weak_match":
            weak_match_count += 1
        elif stage == "ambiguous_candidate":
            ambiguous_candidate_count += 1
        elif stage == "dedupe_drop":
            dedupe_drop_count += 1

        for reason in _reason_codes(row):
            reason_counts[reason] += 1

    return AuditTriageSummary(
        total_rows=len(artifact.rows),
        final_result_count=final_result_count,
        weak_match_count=weak_match_count,
        ambiguous_candidate_count=ambiguous_candidate_count,
        dedupe_drop_count=dedupe_drop_count,
        missing_image_count=missing_image_count,
        stock_list_scoped_count=stock_list_scoped_count,
        query_counts=dict(query_counts),
        stage_counts=dict(stage_counts),
        reason_counts=dict(reason_counts),
    )


def build_ai_triage_prompt(
    artifact: AuditArtifact,
    *,
    max_rows: int = DEFAULT_MAX_PROMPT_ROWS,
) -> str:
    summary = summarize_artifact(artifact)
    rows = [_safe_row(row) for row in artifact.rows[:max_rows]]
    payload = {
        "artifact_name": artifact.path.name,
        "summary": asdict(summary),
        "sample_rows": rows,
        "instructions": [
            "Classify recurring WatchFacts search quality issues.",
            "Use only the provided bounded audit evidence.",
            "Do not invent seller contacts, prices, images, or source URLs.",
            "Recommend deterministic fixtures or matcher/parser/scoring follow-ups.",
        ],
    }
    return (
        "You are a WatchFacts search-quality reviewer. "
        "Return JSON matching the required schema.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


async def run_ai_triage(
    artifact: AuditArtifact,
    *,
    complete: Complete,
    max_rows: int = DEFAULT_MAX_PROMPT_ROWS,
) -> AITriageReport:
    prompt = build_ai_triage_prompt(artifact, max_rows=max_rows)
    response_text = await complete(prompt)
    payload = _extract_json_object(response_text)
    return _triage_report_from_payload(payload)


def render_markdown_report(
    artifact: AuditArtifact,
    *,
    ai_report: AITriageReport | None,
) -> str:
    summary = summarize_artifact(artifact)
    lines = [
        "# AI Audit Triage",
        "",
        f"Artifact: `{artifact.path}`",
        f"Rows: {summary.total_rows}",
        f"Final results: {summary.final_result_count}",
        f"Weak matches: {summary.weak_match_count}",
        f"Ambiguous candidates: {summary.ambiguous_candidate_count}",
        f"Dedupe drops: {summary.dedupe_drop_count}",
        f"Missing images: {summary.missing_image_count}",
        f"Stock-list scoped: {summary.stock_list_scoped_count}",
        "",
        "## Top Queries",
        "",
    ]
    lines.extend(_counter_lines(summary.query_counts))
    lines.extend(["", "## Top Reason Codes", ""])
    lines.extend(_counter_lines(summary.reason_counts))
    if ai_report is not None:
        lines.extend(
            [
                "",
                "## AI Review",
                "",
                f"Risk: `{ai_report.risk_level}`",
                "",
                ai_report.summary,
                "",
                "## AI Issue Patterns",
                "",
            ]
        )
        if ai_report.issue_patterns:
            for pattern in ai_report.issue_patterns:
                lines.extend(
                    [
                        f"- `{pattern.priority}` `{pattern.issue_type}`: {pattern.evidence}",
                        f"  Action: {pattern.recommended_action}",
                        f"  Fixture: {pattern.fixture_hint}",
                    ]
                )
        else:
            lines.append("- None")
        lines.extend(["", "## AI Next Steps", ""])
        if ai_report.next_steps:
            lines.extend(f"- {step}" for step in ai_report.next_steps)
        else:
            lines.append("- None")
    return "\n".join(lines).rstrip() + "\n"


def render_json_report(
    artifact: AuditArtifact,
    *,
    ai_report: AITriageReport | None,
) -> str:
    payload: dict[str, Any] = {
        "artifact": str(artifact.path),
        "summary": asdict(summarize_artifact(artifact)),
    }
    if ai_report is not None:
        payload["ai_report"] = asdict(ai_report)
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def build_openai_complete(settings: Settings) -> Complete:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required when --use-openai is set")

    async def complete(prompt: str) -> str:
        return await asyncio.to_thread(_complete_sync, prompt, settings)

    return complete


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize WatchFacts audit artifacts and optionally ask OpenAI for "
            "offline quality triage."
        )
    )
    parser.add_argument("artifact", help="Audit JSON or JSONL artifact path.")
    parser.add_argument(
        "--use-openai",
        action="store_true",
        help="Call OpenAI with bounded/redacted audit evidence.",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
    )
    parser.add_argument("--max-rows", type=int, default=DEFAULT_MAX_PROMPT_ROWS)
    args = parser.parse_args()
    if args.max_rows <= 0:
        parser.error("--max-rows must be positive")

    artifact = load_audit_artifact(Path(args.artifact))
    ai_report = None
    if args.use_openai:
        settings = load_search_settings()
        ai_report = asyncio.run(
            run_ai_triage(
                artifact,
                complete=build_openai_complete(settings),
                max_rows=args.max_rows,
            )
        )

    if args.format == "json":
        print(render_json_report(artifact, ai_report=ai_report), end="")
    else:
        print(render_markdown_report(artifact, ai_report=ai_report), end="")
    return 0


def _load_jsonl_rows(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _load_json_or_jsonl_rows(text: str) -> list[dict[str, Any]]:
    try:
        return _load_json_rows(text)
    except json.JSONDecodeError:
        return _load_jsonl_rows(text)


def _load_json_rows(text: str) -> list[dict[str, Any]]:
    payload = json.loads(text)
    if isinstance(payload, dict) and (
        "type" in payload or "stage" in payload or "reason_codes" in payload
    ):
        return [payload]
    if isinstance(payload, list) and all(
        isinstance(item, dict)
        and ("type" in item or "stage" in item or "reason_codes" in item)
        for item in payload
    ):
        return [item for item in payload if isinstance(item, dict)]
    reports = payload if isinstance(payload, list) else [payload]
    rows: list[dict[str, Any]] = []
    for item in reports:
        if not isinstance(item, dict):
            continue
        query = _optional_text(item.get("query"))
        if query:
            rows.append(
                {
                    "type": "query_summary",
                    "query": query,
                    "result_count": item.get("result_count"),
                    "summary": item.get("summary"),
                }
            )
        for row in item.get("rows", []):
            if not isinstance(row, dict):
                continue
            row_payload = dict(row)
            row_payload.setdefault("type", "final_result")
            row_payload.setdefault("stage", "final")
            if query:
                row_payload.setdefault("query", query)
            rows.append(row_payload)
    return rows


def _strip_json_code_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    stripped = text.strip("`")
    if stripped.startswith("json"):
        stripped = stripped[4:].strip()
    return stripped


def _row_stage(row: dict[str, Any]) -> str:
    stage = _optional_text(row.get("stage"))
    if stage:
        return stage
    return _optional_text(row.get("type")) or ""


def _reason_codes(row: dict[str, Any]) -> tuple[str, ...]:
    reasons: list[str] = []
    for key in ("reason_codes", "score_reasons", "suspicious_reasons", "fuzzy_reason_codes"):
        value = row.get(key)
        if isinstance(value, list):
            reasons.extend(str(item) for item in value if item is not None)
        elif isinstance(value, tuple):
            reasons.extend(str(item) for item in value if item is not None)
    summary = row.get("summary")
    if isinstance(summary, dict):
        for nested_key in ("suspicious_reason_counts", "image_reason_counts"):
            nested = summary.get(nested_key)
            if isinstance(nested, dict):
                reasons.extend(str(reason) for reason in nested)
    return tuple(reasons)


def _safe_row(row: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "type",
        "query",
        "stage",
        "decision",
        "query_intent",
        "rank",
        "has_image",
        "scope_reason",
        "image_reason",
        "guardrail_action",
        "fuzzy_score",
        "reason_codes",
        "score_reasons",
        "suspicious_reasons",
        "text_snippet",
        "listing_text",
        "raw_listing_preview",
        "seller",
        "posted_date",
        "source_url",
    }
    safe: dict[str, Any] = {}
    for key in allowed_keys:
        if key not in row or SENSITIVE_KEY_RE.search(key):
            continue
        value = _redact_value(row[key])
        if value not in (None, "", [], {}):
            safe[key] = value
    return safe


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _redacted_snippet(value, DEFAULT_SNIPPET_CHARS)
    if isinstance(value, list):
        return [_redact_value(item) for item in value[:20]]
    if isinstance(value, tuple):
        return [_redact_value(item) for item in value[:20]]
    if isinstance(value, dict):
        return {
            str(key): _redact_value(item)
            for key, item in value.items()
            if not SENSITIVE_KEY_RE.search(str(key))
        }
    if isinstance(value, bool | int | float):
        return value
    return None


def _redacted_snippet(value: str, limit: int) -> str:
    redacted = SENSITIVE_CONTEXT_RE.sub("[REDACTED]", value)
    redacted = SENSITIVE_CONTEXT_PATH_RE.sub("[REDACTED_PATH]", redacted)
    normalized = " ".join(redacted.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 3, 0)].rstrip() + "..."


def _triage_report_from_payload(payload: dict[str, Any]) -> AITriageReport:
    patterns = []
    for item in payload.get("issue_patterns", []):
        if not isinstance(item, dict):
            continue
        patterns.append(
            AITriagePattern(
                issue_type=_required_text(item, "issue_type"),
                priority=_required_text(item, "priority"),
                evidence=_required_text(item, "evidence"),
                recommended_action=_required_text(item, "recommended_action"),
                fixture_hint=_required_text(item, "fixture_hint"),
            )
        )
    next_steps_value = payload.get("next_steps")
    next_steps = (
        tuple(str(step) for step in next_steps_value if isinstance(step, str))
        if isinstance(next_steps_value, list)
        else ()
    )
    return AITriageReport(
        summary=_required_text(payload, "summary"),
        risk_level=_required_text(payload, "risk_level"),
        issue_patterns=tuple(patterns),
        next_steps=next_steps,
    )


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"AI triage response missing {key}")
    return value.strip()


def _extract_json_object(value: str) -> dict[str, Any]:
    stripped = value.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end < start:
        raise ValueError("AI triage response missing JSON object")
    parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("AI triage response must be an object")
    return parsed


def _complete_sync(prompt: str, settings: Settings) -> str:
    payload = {
        "model": settings.openai_model,
        "input": [
            {
                "role": "system",
                "content": (
                    "You are a WatchFacts search-quality reviewer. Return only "
                    "schema-valid JSON. Use the provided audit evidence only."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "max_output_tokens": 900,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "watchfacts_ai_audit_triage",
                "strict": True,
                "schema": _triage_schema(),
            }
        },
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=settings.openai_timeout_seconds,
        ) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (TimeoutError, urllib.error.URLError) as exc:
        raise RuntimeError("OpenAI audit triage request failed") from exc
    return _extract_response_text(data)


def _extract_response_text(data: dict[str, Any]) -> str:
    output_text = data.get("output_text")
    if isinstance(output_text, str):
        return output_text
    output = data.get("output")
    if not isinstance(output, list):
        raise ValueError("OpenAI response missing output")
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for content_item in content:
            if not isinstance(content_item, dict):
                continue
            text = content_item.get("text")
            if isinstance(text, str):
                return text
    raise ValueError("OpenAI response missing output text")


def _triage_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
            "issue_patterns": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "issue_type": {"type": "string"},
                        "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                        "evidence": {"type": "string"},
                        "recommended_action": {"type": "string"},
                        "fixture_hint": {"type": "string"},
                    },
                    "required": [
                        "issue_type",
                        "priority",
                        "evidence",
                        "recommended_action",
                        "fixture_hint",
                    ],
                },
            },
            "next_steps": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary", "risk_level", "issue_patterns", "next_steps"],
    }


def _counter_lines(counts: dict[str, int], *, limit: int = 10) -> list[str]:
    if not counts:
        return ["- None"]
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    return [f"- `{key}`: {count}" for key, count in ordered]


def _optional_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


if __name__ == "__main__":
    raise SystemExit(main())
