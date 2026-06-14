from __future__ import annotations

import asyncio
import json

from app.config import Settings
from scripts.diagnostics import ai_audit_triage


def test_load_artifact_accepts_jsonl_and_counts_quality_signals(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "audit_event",
                        "query": "5205r green",
                        "stage": "weak_match",
                        "decision": "demote",
                        "reason_codes": ["descriptor.conflict"],
                    }
                ),
                json.dumps(
                    {
                        "type": "audit_event",
                        "query": "5205r green",
                        "stage": "dedupe_drop",
                        "decision": "deduped",
                        "reason_codes": ["dedupe.text"],
                    }
                ),
                json.dumps(
                    {
                        "type": "final_result",
                        "query": "5205r green",
                        "stage": "final",
                        "rank": 1,
                        "has_image": False,
                        "scope_reason": "scope.stock_list",
                        "reason_codes": [
                            "quality.missing_price",
                            "image.omitted_bundle_ambiguous",
                        ],
                        "text_snippet": "5205R green HKD",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    artifact = ai_audit_triage.load_audit_artifact(path)
    summary = ai_audit_triage.summarize_artifact(artifact)

    assert summary.total_rows == 3
    assert summary.final_result_count == 1
    assert summary.weak_match_count == 1
    assert summary.dedupe_drop_count == 1
    assert summary.missing_image_count == 1
    assert summary.stock_list_scoped_count == 1
    assert summary.reason_counts["quality.missing_price"] == 1
    assert summary.query_counts["5205r green"] == 3


def test_load_artifact_accepts_pretty_audit_json_report(tmp_path) -> None:
    path = tmp_path / "audit.json"
    path.write_text(
        json.dumps(
            [
                {
                    "query": "5712r",
                    "result_count": 1,
                    "summary": {
                        "audited_result_count": 1,
                        "image_layout_pattern_counts": {
                            "layout.multi_reference_bundle": 1
                        },
                    },
                    "rows": [
                        {
                            "rank": 1,
                            "listing_text": "5712R HKD 820000",
                            "has_image": True,
                            "reason_codes": ["quality.clean"],
                        }
                    ],
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    artifact = ai_audit_triage.load_audit_artifact(path)
    summary = ai_audit_triage.summarize_artifact(artifact)

    assert summary.total_rows == 2
    assert summary.final_result_count == 1
    assert summary.query_counts["5712r"] == 2
    assert summary.reason_counts["quality.clean"] == 1
    assert summary.reason_counts["layout.multi_reference_bundle"] == 1


def test_build_ai_triage_prompt_redacts_sensitive_values(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "audit_event",
                "query": "5712r",
                "stage": "raw",
                "text_snippet": (
                    "5712R cookie=session123 token=abc "
                    "data/watchfacts_state.json password=Ninhbinh123"
                ),
                "source_url": "https://example.test?api_key=secret",
                "reason_codes": ["raw"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    artifact = ai_audit_triage.load_audit_artifact(path)
    prompt = ai_audit_triage.build_ai_triage_prompt(artifact)

    assert "session123" not in prompt
    assert "token=abc" not in prompt
    assert "watchfacts_state.json" not in prompt
    assert "Ninhbinh123" not in prompt
    assert "api_key=secret" not in prompt
    assert "[REDACTED]" in prompt


def test_render_markdown_includes_deterministic_summary(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "final_result",
                "query": "126500ln white 2026",
                "stage": "final",
                "has_image": True,
                "reason_codes": ["quality.clean"],
                "text_snippet": "126500LN white 2026",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    artifact = ai_audit_triage.load_audit_artifact(path)

    output = ai_audit_triage.render_markdown_report(
        artifact,
        ai_report=None,
    )

    assert "# AI Audit Triage" in output
    assert "Rows: 1" in output
    assert "Final results: 1" in output
    assert "quality.clean" in output
    assert "126500ln white 2026" in output


def test_run_ai_triage_uses_injected_completion_and_schema(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "final_result",
                "query": "FPJ Elegante Titanium",
                "stage": "final",
                "has_image": False,
                "scope_reason": "scope.stock_list",
                "reason_codes": ["image.omitted_bundle_ambiguous"],
                "text_snippet": "FPJ Elegante Titanium",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    artifact = ai_audit_triage.load_audit_artifact(path)

    async def complete(prompt: str) -> str:
        assert "FPJ Elegante Titanium" in prompt
        return json.dumps(
            {
                "summary": "Stock-list image ambiguity is the main issue.",
                "risk_level": "medium",
                "issue_patterns": [
                    {
                        "issue_type": "image_attribution",
                        "priority": "high",
                        "evidence": "One final result has omitted bundle image.",
                        "recommended_action": "Create image attribution fixture.",
                        "fixture_hint": "Use audit JSONL final_result row.",
                    }
                ],
                "next_steps": ["Generate regression fixture"],
            }
        )

    report = asyncio.run(ai_audit_triage.run_ai_triage(artifact, complete=complete))

    assert report.summary == "Stock-list image ambiguity is the main issue."
    assert report.risk_level == "medium"
    assert report.issue_patterns[0].issue_type == "image_attribution"
    assert report.next_steps == ("Generate regression fixture",)


def test_build_openai_complete_uses_shared_client_boundary(tmp_path) -> None:
    settings = Settings(
        telegram_bot_token="token",
        telegram_allowed_user_ids=(),
        telegram_result_limit=5,
        watchfacts_url="https://watchfacts.example/simon-match-making",
        headless=True,
        enable_crawl4ai=True,
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        logs_dir=tmp_path / "logs",
        db_path=tmp_path / "data" / "bot.db",
        browser_state_path=tmp_path / "data" / "watchfacts_state.json",
        hybrid_ai_mode="shadow",
        openai_api_key="sk-test",
        openai_model="test-model",
        openai_timeout_seconds=9,
    )

    class FakeOpenAIClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def complete_json(self, **kwargs):
            self.calls.append(kwargs)
            return json.dumps(
                {
                    "summary": "Audit summary",
                    "risk_level": "low",
                    "issue_patterns": [],
                    "next_steps": [],
                }
            )

    client = FakeOpenAIClient()
    complete = ai_audit_triage.build_openai_complete(settings, client=client)

    response = asyncio.run(complete("bounded prompt"))

    assert json.loads(response)["summary"] == "Audit summary"
    assert client.calls == [
        {
            "system_prompt": (
                "You are a WatchFacts search-quality reviewer. Return only "
                "schema-valid JSON. Use the provided audit evidence only."
            ),
            "user_prompt": "bounded prompt",
            "max_output_tokens": 900,
            "schema_name": "watchfacts_ai_audit_triage",
            "schema": ai_audit_triage._triage_schema(),
            "error_message": "OpenAI audit triage request failed",
        }
    ]
