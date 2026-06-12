from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from app.application import (
    AuditTriageUseCase,
    IssueTriageUseCase,
    OpenWAHandoffUseCase,
    SearchUseCase,
)
from app.config import load_search_settings
from app.openwa_handoff import OpenWAChatDraftResponse, OpenWAHandoffConfig
from app.search_result import SearchResult


class FakeSearchWorkflow:
    def __init__(self, results: list[SearchResult]) -> None:
        self.results = results
        self.queries: list[str] = []
        self.last_search_diagnostics = {"final_count": len(results)}
        self.last_search_audit_events = ("final",)

    async def search(self, query: str) -> list[SearchResult]:
        self.queries.append(query)
        return self.results


def test_search_use_case_delegates_to_workflow_and_exposes_runtime_metadata() -> None:
    workflow = FakeSearchWorkflow([SearchResult("5712G Used")])
    use_case = SearchUseCase(workflow)

    results = asyncio.run(use_case.search("5712g"))

    assert results == [SearchResult("5712G Used")]
    assert workflow.queries == ["5712g"]
    assert use_case.last_search_diagnostics == {"final_count": 1}
    assert use_case.last_search_audit_events == ("final",)


def test_search_use_case_from_settings_builds_existing_workflow_shape(tmp_path) -> None:
    settings = load_search_settings(env={}, project_root=tmp_path)
    captured: dict[str, Any] = {}

    class FakeWorkflowFactory:
        def __init__(
            self,
            settings_arg,
            *,
            database=None,
            ai_suggestion_repository=None,
            issue_repository=None,
            search_cache_repository=None,
            fetch_html=None,
            refine_results=None,
        ) -> None:
            captured["settings"] = settings_arg
            captured["database"] = database
            captured["ai_suggestion_repository"] = ai_suggestion_repository
            captured["issue_repository"] = issue_repository
            captured["search_cache_repository"] = search_cache_repository
            captured["fetch_html"] = fetch_html
            captured["refine_results"] = refine_results

        async def search(self, query: str) -> list[SearchResult]:
            return [SearchResult(f"result for {query}")]

    async def fake_refiner(query: str, results: list[SearchResult]) -> list[SearchResult]:
        return results

    database = object()
    ai_suggestion_repository = object()
    issue_repository = object()
    search_cache_repository = object()
    fetch_html = object()
    use_case = SearchUseCase.from_settings(
        settings,
        workflow_factory=FakeWorkflowFactory,
        database=database,
        ai_suggestion_repository=ai_suggestion_repository,
        issue_repository=issue_repository,
        search_cache_repository=search_cache_repository,
        fetch_html=fetch_html,
        refine_results=fake_refiner,
    )

    results = asyncio.run(use_case.search("5205r green"))

    assert results == [SearchResult("result for 5205r green")]
    assert captured == {
        "settings": settings,
        "database": database,
        "ai_suggestion_repository": ai_suggestion_repository,
        "issue_repository": issue_repository,
        "search_cache_repository": search_cache_repository,
        "fetch_html": fetch_html,
        "refine_results": fake_refiner,
    }


def test_openwa_handoff_use_case_delegates_chat_draft_creation() -> None:
    config = OpenWAHandoffConfig(
        base_url="https://openwa.example",
        api_key="test-key",
        dashboard_url="https://dashboard.example",
        chat_draft_endpoint="/api/chats/drafts",
        enabled=True,
    )
    captured_payloads: list[dict[str, Any]] = []

    async def fake_client(payload: dict[str, Any]) -> OpenWAChatDraftResponse:
        captured_payloads.append(payload)
        return OpenWAChatDraftResponse(
            draft_id="draft-1",
            chat_id="chat-1",
            dashboard_url="https://dashboard.example/chats/drafts/draft-1",
        )

    use_case = OpenWAHandoffUseCase(config=config, client=fake_client)
    response = asyncio.run(use_case.create_chat_draft({"source": "watchfacts"}))

    assert captured_payloads == [{"source": "watchfacts"}]
    assert response.draft_id == "draft-1"
    assert response.chat_id == "chat-1"


@dataclass(frozen=True)
class FakeIssue:
    id: int
    issue_type: str


class FakeIssueDatabase:
    def __init__(self) -> None:
        self.feedback_id = 41

    def record_result_feedback(self, **payload):
        self.last_feedback_payload = payload
        return self.feedback_id

    def get_issue(self, issue_id, *, issue_type=None):
        return FakeIssue(id=issue_id, issue_type=issue_type or "feedback")

    def list_feedback_issues(self, *, status, limit):
        return [FakeIssue(id=1, issue_type=f"feedback:{status}:{limit}")]

    def list_suspicious_issues(self, *, status, limit, min_severity=None):
        return [FakeIssue(id=2, issue_type=f"suspicious:{status}:{limit}:{min_severity}")]

    def mark_issue_status(self, issue_id, *, issue_type, status, notes=None):
        return FakeIssue(id=issue_id, issue_type=f"{issue_type}:{status}:{notes}")

    def summarize_open_suspicious_issues(self, *, limit):
        return [FakeIssue(id=3, issue_type=f"summary:{limit}")]


def test_issue_triage_use_case_wraps_feedback_and_issue_queries() -> None:
    database = FakeIssueDatabase()
    use_case = IssueTriageUseCase.from_database(database)

    issue = use_case.record_feedback(
        query_text="5712g",
        result_rank=1,
        reason="wrong_result",
        listing_text="5712R",
        raw_listing_text="raw",
        seller="Issac",
        posted_date="11/06/2026",
        source_url="/listing",
        notes="bad model",
    )
    listed = use_case.list_issues(issue_type="all", status="open", limit=5)
    updated = use_case.update_issue(8, issue_type="feedback", status="fixed")
    summary = use_case.summarize_suspicious(limit=7)

    assert issue == FakeIssue(id=41, issue_type="feedback")
    assert database.last_feedback_payload["query_text"] == "5712g"
    assert [item.issue_type for item in listed] == [
        "feedback:open:5",
        "suspicious:open:5:None",
    ]
    assert updated == FakeIssue(id=8, issue_type="feedback:fixed:None")
    assert summary == [FakeIssue(id=3, issue_type="summary:7")]


def test_audit_triage_use_case_uses_injected_cli_operations() -> None:
    calls: list[tuple[str, Any]] = []

    def load(path):
        calls.append(("load", path))
        return {"path": path}

    def summarize(artifact):
        calls.append(("summarize", artifact))
        return {"total_rows": 1}

    def render_markdown(artifact, *, ai_report):
        calls.append(("markdown", ai_report))
        return "# report\n"

    def render_json(artifact, *, ai_report):
        calls.append(("json", ai_report))
        return '{"report": true}\n'

    async def run_ai(artifact, *, complete, max_rows):
        calls.append(("ai", max_rows))
        return {"summary": await complete("prompt")}

    async def complete(prompt: str) -> str:
        return f"done:{prompt}"

    use_case = AuditTriageUseCase(
        load_artifact=load,
        summarize_artifact=summarize,
        render_markdown_report=render_markdown,
        render_json_report=render_json,
        run_ai_triage=run_ai,
    )
    artifact = use_case.load("/tmp/audit.jsonl")

    assert artifact == {"path": "/tmp/audit.jsonl"}
    assert use_case.summarize(artifact) == {"total_rows": 1}
    assert use_case.render_markdown(artifact, ai_report=None) == "# report\n"
    assert use_case.render_json(artifact, ai_report={"ok": True}) == '{"report": true}\n'
    assert asyncio.run(use_case.run_ai(artifact, complete=complete, max_rows=3)) == {
        "summary": "done:prompt"
    }
    assert calls == [
        ("load", "/tmp/audit.jsonl"),
        ("summarize", {"path": "/tmp/audit.jsonl"}),
        ("markdown", None),
        ("json", {"ok": True}),
        ("ai", 3),
    ]
