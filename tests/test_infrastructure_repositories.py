from __future__ import annotations

from app.infrastructure import (
    AiSuggestionRepository,
    IssueRepository,
    ResultReferenceRepository,
    SearchCacheRepository,
)
from app.search_result import SearchResult


class FakeResultReferenceDatabase:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def record_search_result_references(self, **kwargs):
        self.calls.append(("record", kwargs))

    def get_fresh_search_result_reference_by_id(self, **kwargs):
        self.calls.append(("by_id", kwargs))
        return (2, SearchResult("5712G by id"))

    def get_fresh_search_result_reference_by_stable_listing_id(self, **kwargs):
        self.calls.append(("by_stable", kwargs))
        return (3, SearchResult("5712G by stable"))

    def get_fresh_search_result_reference_by_rank(self, **kwargs):
        self.calls.append(("by_rank", kwargs))
        return ("watchfacts:rank", SearchResult("5712G by rank"))


def test_result_reference_repository_delegates_database_operations() -> None:
    database = FakeResultReferenceDatabase()
    repository = ResultReferenceRepository(database)
    results = [SearchResult("5712G Used")]

    repository.record_results(
        cache_key="cache-key",
        query_text="5712g",
        results=results,
        ttl_seconds=30,
    )
    by_id = repository.get_by_result_id(
        cache_key="cache-key",
        result_id="watchfacts:result",
    )
    by_stable = repository.get_by_stable_listing_id(
        cache_key="cache-key",
        stable_listing_id="watchfacts-listing:stable",
    )
    by_rank = repository.get_by_rank(cache_key="cache-key", result_rank=2)

    assert by_id == (2, SearchResult("5712G by id"))
    assert by_stable == (3, SearchResult("5712G by stable"))
    assert by_rank == ("watchfacts:rank", SearchResult("5712G by rank"))
    assert database.calls == [
        (
            "record",
            {
                "cache_key": "cache-key",
                "query_text": "5712g",
                "results": results,
                "ttl_seconds": 30,
            },
        ),
        (
            "by_id",
            {
                "cache_key": "cache-key",
                "result_id": "watchfacts:result",
            },
        ),
        (
            "by_stable",
            {
                "cache_key": "cache-key",
                "stable_listing_id": "watchfacts-listing:stable",
            },
        ),
        (
            "by_rank",
            {
                "cache_key": "cache-key",
                "result_rank": 2,
            },
        ),
    ]


class FakeIssueDatabase:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def record_result_feedback(self, **kwargs):
        self.calls.append(("record_feedback", kwargs))
        return 7

    def record_suspicious_result(self, **kwargs):
        self.calls.append(("record_suspicious", kwargs))

    def get_issue(self, issue_id, *, issue_type=None):
        self.calls.append(
            ("get_issue", {"issue_id": issue_id, "issue_type": issue_type})
        )
        return {"id": issue_id, "type": issue_type}

    def list_feedback_issues(self, *, status, limit):
        self.calls.append(("list_feedback", {"status": status, "limit": limit}))
        return [{"type": "feedback"}]

    def list_suspicious_issues(self, *, status, limit, min_severity=None):
        self.calls.append(
            (
                "list_suspicious",
                {
                    "status": status,
                    "limit": limit,
                    "min_severity": min_severity,
                },
            )
        )
        return [{"type": "suspicious"}]

    def mark_issue_status(self, issue_id, *, issue_type, status, notes=None):
        self.calls.append(
            (
                "mark_status",
                {
                    "issue_id": issue_id,
                    "issue_type": issue_type,
                    "status": status,
                    "notes": notes,
                },
            )
        )
        return {"id": issue_id, "status": status}

    def summarize_open_suspicious_issues(self, *, limit):
        self.calls.append(("summary", {"limit": limit}))
        return [{"count": 1}]


def test_issue_repository_delegates_database_operations() -> None:
    database = FakeIssueDatabase()
    repository = IssueRepository(database)

    issue_id = repository.record_feedback(
        query_text="5712g",
        result_rank=1,
        reason="wrong_result",
        listing_text="5712R",
        raw_listing_text="raw",
        seller="Issac",
        posted_date="11/06/2026",
        source_url="/listing",
        notes="bad model",
        telegram_user_id=123,
    )
    issue = repository.get_issue(issue_id, issue_type="feedback")
    feedback = repository.list_feedback(status="open", limit=5)
    suspicious = repository.list_suspicious(
        status="open",
        limit=5,
        min_severity=2,
    )
    updated = repository.mark_status(
        issue_id,
        issue_type="feedback",
        status="fixed",
        notes="done",
    )
    summary = repository.summarize_suspicious(limit=7)
    repository.record_suspicious(
        query_text="5712g",
        result_rank=1,
        reason="missing_image",
        severity=2,
        listing_text="5712G",
        raw_listing_text="raw",
        source_url="/listing",
    )

    assert issue_id == 7
    assert issue == {"id": 7, "type": "feedback"}
    assert feedback == [{"type": "feedback"}]
    assert suspicious == [{"type": "suspicious"}]
    assert updated == {"id": 7, "status": "fixed"}
    assert summary == [{"count": 1}]
    assert database.calls == [
        (
            "record_feedback",
            {
                "query_text": "5712g",
                "result_rank": 1,
                "reason": "wrong_result",
                "listing_text": "5712R",
                "raw_listing_text": "raw",
                "seller": "Issac",
                "posted_date": "11/06/2026",
                "source_url": "/listing",
                "notes": "bad model",
                "telegram_user_id": 123,
            },
        ),
        ("get_issue", {"issue_id": 7, "issue_type": "feedback"}),
        ("list_feedback", {"status": "open", "limit": 5}),
        (
            "list_suspicious",
            {
                "status": "open",
                "limit": 5,
                "min_severity": 2,
            },
        ),
        (
            "mark_status",
            {
                "issue_id": 7,
                "issue_type": "feedback",
                "status": "fixed",
                "notes": "done",
            },
        ),
        ("summary", {"limit": 7}),
        (
            "record_suspicious",
            {
                "query_text": "5712g",
                "result_rank": 1,
                "reason": "missing_image",
                "severity": 2,
                "listing_text": "5712G",
                "raw_listing_text": "raw",
                "source_url": "/listing",
            },
        ),
    ]


class FakeSearchCacheDatabase:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def record_query_results(self, query_text, listings, **kwargs):
        self.calls.append(
            (
                "record_query_results",
                {
                    "query_text": query_text,
                    "listings": listings,
                    **kwargs,
                },
            )
        )
        return {"id": 1}

    def get_search_cache_quality_metrics(self, cache_key):
        self.calls.append(("cache_metrics", {"cache_key": cache_key}))
        return {"image_missing_count": 1}

    def get_fresh_search_cache_row(self, cache_key):
        self.calls.append(("cache_row", {"cache_key": cache_key}))
        return ("[]", 0, 1, 0)

    def record_search_cache(self, **kwargs):
        self.calls.append(("record_cache", kwargs))


def test_search_cache_repository_delegates_database_operations() -> None:
    database = FakeSearchCacheDatabase()
    repository = SearchCacheRepository(database)
    results = [SearchResult("5712G Used")]

    record = repository.record_query_results(
        "5712g",
        results,
        image_missing_count=1,
        server_filtered_hit_count=2,
        playwright_fallback_count=3,
    )
    metrics = repository.get_quality_metrics("cache-key")
    row = repository.get_fresh_row("cache-key")
    repository.record_cache(
        cache_key="cache-key",
        query_text="5712g",
        result_json="[]",
        result_count=1,
        image_missing_count=1,
        server_filtered_hit_count=2,
        playwright_fallback_count=3,
        ttl_seconds=30,
    )

    assert record == {"id": 1}
    assert metrics == {"image_missing_count": 1}
    assert row == ("[]", 0, 1, 0)
    assert database.calls == [
        (
            "record_query_results",
            {
                "query_text": "5712g",
                "listings": results,
                "image_missing_count": 1,
                "server_filtered_hit_count": 2,
                "playwright_fallback_count": 3,
            },
        ),
        ("cache_metrics", {"cache_key": "cache-key"}),
        ("cache_row", {"cache_key": "cache-key"}),
        (
            "record_cache",
            {
                "cache_key": "cache-key",
                "query_text": "5712g",
                "result_json": "[]",
                "result_count": 1,
                "image_missing_count": 1,
                "server_filtered_hit_count": 2,
                "playwright_fallback_count": 3,
                "ttl_seconds": 30,
            },
        ),
    ]


class FakeAiSuggestionDatabase:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def record_ai_refinement_suggestion(self, **kwargs):
        self.calls.append(("record", kwargs))
        return 9

    def list_ai_refinement_suggestions(self, **kwargs):
        self.calls.append(("list", kwargs))
        return [{"id": 9}]

    def get_ai_refinement_suggestion(self, suggestion_id):
        self.calls.append(("get", {"suggestion_id": suggestion_id}))
        return {"id": suggestion_id}

    def mark_ai_refinement_suggestion_status(self, suggestion_id, **kwargs):
        self.calls.append(("mark", {"suggestion_id": suggestion_id, **kwargs}))
        return {"id": suggestion_id, "status": kwargs["status"]}

    def export_reviewed_ai_suggestions(self, **kwargs):
        self.calls.append(("export", kwargs))
        return [{"id": 9, "type": "ai_suggestion"}]


def test_ai_suggestion_repository_delegates_database_operations() -> None:
    database = FakeAiSuggestionDatabase()
    repository = AiSuggestionRepository(database)

    suggestion_id = repository.record_suggestion(
        query_text="5712g",
        result_rank=1,
        mode="shadow",
        model="test-model",
        deterministic_text="5712R",
        suggested_text="5712G",
        raw_listing_text="raw",
        source_url="/listing",
        gate_status="accepted",
        gate_reasons=("reference_match",),
        latency_ms=123,
        prompt_version="prompt-v1",
    )
    listed = repository.list_suggestions(limit=5, review_status="open")
    detail = repository.get_suggestion(9)
    updated = repository.mark_status(9, status="accepted", notes="good")
    exported = repository.export_reviewed(status="accepted", limit=50)

    assert suggestion_id == 9
    assert listed == [{"id": 9}]
    assert detail == {"id": 9}
    assert updated == {"id": 9, "status": "accepted"}
    assert exported == [{"id": 9, "type": "ai_suggestion"}]
    assert database.calls == [
        (
            "record",
            {
                "query_text": "5712g",
                "result_rank": 1,
                "mode": "shadow",
                "model": "test-model",
                "deterministic_text": "5712R",
                "suggested_text": "5712G",
                "raw_listing_text": "raw",
                "source_url": "/listing",
                "gate_status": "accepted",
                "gate_reasons": ("reference_match",),
                "latency_ms": 123,
                "prompt_version": "prompt-v1",
            },
        ),
        ("list", {"limit": 5, "review_status": "open"}),
        ("get", {"suggestion_id": 9}),
        (
            "mark",
            {
                "suggestion_id": 9,
                "status": "accepted",
                "notes": "good",
            },
        ),
        ("export", {"status": "accepted", "limit": 50}),
    ]
