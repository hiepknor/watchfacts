from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.db import Database


@dataclass(frozen=True)
class Listing:
    listing_text: str
    seller: str | None = None
    posted_date: str | None = None
    image_url: str | None = None
    source_url: str | None = None


def test_initialize_creates_database_and_tables(tmp_path) -> None:
    db_path = tmp_path / "data" / "bot.db"

    Database(db_path).initialize()

    assert db_path.exists()
    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert {
        "queries",
        "listings",
        "query_results",
        "llm_refinements",
        "search_cache",
        "result_feedback",
        "suspicious_results",
        "ai_refinement_suggestions",
    } <= tables


def test_database_connection_sets_busy_timeout(tmp_path) -> None:
    db_path = tmp_path / "data" / "bot.db"
    database = Database(db_path)

    with database.connect() as connection:
        timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]

    assert timeout == 5000


def test_record_query_results_persists_query_listing_and_relationship(tmp_path) -> None:
    db_path = tmp_path / "data" / "bot.db"
    database = Database(db_path)
    listings = [
        Listing(
            listing_text="Rolex 228253A choco",
            seller="HK STOCKS",
            posted_date="April 20, 2026",
            image_url="https://example.test/image.jpg",
            source_url="https://example.test/listing/1",
        )
    ]

    record = database.record_query_results("228253a choco", listings)

    assert record.query_text == "228253a choco"
    assert record.normalized_query == "228253a choco"
    assert record.result_count == 1

    with sqlite3.connect(db_path) as connection:
        query_row = connection.execute(
            "SELECT query_text, normalized_query, result_count FROM queries"
        ).fetchone()
        listing_row = connection.execute(
            """
            SELECT dedupe_key, listing_text, normalized_text, seller, posted_date,
                   image_url, source_url
            FROM listings
            """
        ).fetchone()
        result_row = connection.execute(
            "SELECT query_id, listing_id, rank FROM query_results"
        ).fetchone()

    assert query_row == ("228253a choco", "228253a choco", 1)
    assert listing_row == (
        "rolex 228253a choco|hk stocks|april 20 2026",
        "Rolex 228253A choco",
        "rolex 228253a choco",
        "HK STOCKS",
        "April 20, 2026",
        "https://example.test/image.jpg",
        "https://example.test/listing/1",
    )
    assert result_row == (record.id, 1, 1)


def test_record_query_results_updates_existing_listing_by_dedupe_key(tmp_path) -> None:
    db_path = tmp_path / "data" / "bot.db"
    database = Database(db_path)

    database.record_query_results(
        "228253a choco",
        [Listing("Rolex 228253A choco", "HK STOCKS", "April 20, 2026")],
    )
    database.record_query_results(
        "228253a",
        [Listing("ROLEX, 228253A CHOCO", "hk stocks", "April 20 2026")],
    )

    with sqlite3.connect(db_path) as connection:
        listing_count = connection.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        query_count = connection.execute("SELECT COUNT(*) FROM queries").fetchone()[0]
        result_count = connection.execute("SELECT COUNT(*) FROM query_results").fetchone()[0]

    assert listing_count == 1
    assert query_count == 2
    assert result_count == 2


def test_llm_refinement_cache_round_trips_by_query_listing_and_model(tmp_path) -> None:
    db_path = tmp_path / "data" / "bot.db"
    database = Database(db_path)

    assert database.get_llm_refinement("fpj elegante", "raw listing", "q4") is None

    database.record_llm_refinement(
        "fpj elegante",
        "raw listing",
        "q4",
        "refined listing",
        latency_ms=123,
    )

    assert (
        database.get_llm_refinement("FPJ Elegante", "raw listing", "q4")
        == "refined listing"
    )
    assert database.get_llm_refinement("fpj elegante", "raw listing", "q8") is None


def test_ai_refinement_suggestions_are_recorded_for_review(tmp_path) -> None:
    db_path = tmp_path / "data" / "bot.db"
    database = Database(db_path)

    suggestion_id = database.record_ai_refinement_suggestion(
        query_text="Fpj Elegante Titanium",
        result_rank=2,
        mode="shadow",
        model="gemma",
        deterministic_text="FPJ quantieme - [ ] FPJ Elegante Titanium 120k",
        suggested_text="FPJ Elegante Titanium 120k",
        raw_listing_text="FPJ quantieme - [ ] FPJ Elegante Titanium 120k",
        source_url="/flash-sales/1",
        gate_status="accepted",
        gate_reasons=["matches_query", "raw_substring"],
        latency_ms=42,
    )

    suggestions = database.list_ai_refinement_suggestions()

    assert suggestions[0].id == suggestion_id
    assert suggestions[0].query_text == "Fpj Elegante Titanium"
    assert suggestions[0].normalized_query == "fpj elegante titanium"
    assert suggestions[0].result_rank == 2
    assert suggestions[0].mode == "shadow"
    assert suggestions[0].model == "gemma"
    assert suggestions[0].suggested_text == "FPJ Elegante Titanium 120k"
    assert suggestions[0].gate_status == "accepted"
    assert suggestions[0].gate_reasons == ("matches_query", "raw_substring")
    assert suggestions[0].review_status == "open"


def test_ai_refinement_suggestions_are_deduped_and_linked_to_issues(tmp_path) -> None:
    db_path = tmp_path / "data" / "bot.db"
    database = Database(db_path)
    issue_id = database.record_result_feedback(
        query_text="Fpj Elegante Titanium",
        result_rank=2,
        reason="missing_info",
        listing_text="FPJ quantieme - [ ] FPJ Elegante Titanium 120k",
        raw_listing_text="FPJ quantieme - [ ] FPJ Elegante Titanium 120k",
        telegram_user_id=123,
    )

    first_id = database.record_ai_refinement_suggestion(
        query_text="Fpj Elegante Titanium",
        result_rank=2,
        mode="review",
        model="gpt-5-mini",
        deterministic_text="FPJ quantieme - [ ] FPJ Elegante Titanium 120k",
        suggested_text="FPJ Elegante Titanium 120k",
        raw_listing_text="FPJ quantieme - [ ] FPJ Elegante Titanium 120k",
        gate_status="accepted",
        gate_reasons=["matches_query"],
    )
    second_id = database.record_ai_refinement_suggestion(
        query_text="FPJ elegante titanium",
        result_rank=2,
        mode="guarded",
        model="gpt-5-mini",
        deterministic_text="FPJ quantieme - [ ] FPJ Elegante Titanium 120k",
        suggested_text="FPJ Elegante Titanium 120k",
        raw_listing_text="FPJ quantieme - [ ] FPJ Elegante Titanium 120k",
        gate_status="accepted",
        gate_reasons=["matches_query", "raw_substring"],
    )

    suggestions = database.list_ai_refinement_suggestions()

    assert second_id == first_id
    assert len(suggestions) == 1
    assert suggestions[0].mode == "guarded"
    assert suggestions[0].issue_type == "feedback"
    assert suggestions[0].issue_id == issue_id


def test_ai_refinement_suggestion_dedupe_keeps_distinct_suggested_texts(tmp_path) -> None:
    db_path = tmp_path / "data" / "bot.db"
    database = Database(db_path)
    first_id = database.record_ai_refinement_suggestion(
        query_text="Fpj Elegante Titanium",
        result_rank=1,
        mode="review",
        model="gpt-5-mini",
        deterministic_text="FPJ quantieme - [ ] FPJ Elegante Titanium 120k",
        suggested_text="FPJ Elegante Titanium 120k",
        raw_listing_text="FPJ quantieme - [ ] FPJ Elegante Titanium 120k",
        gate_status="accepted",
        gate_reasons=["matches_query"],
    )
    database.mark_ai_refinement_suggestion_status(first_id, status="accepted")

    second_id = database.record_ai_refinement_suggestion(
        query_text="Fpj Elegante Titanium",
        result_rank=1,
        mode="review",
        model="gpt-5-mini",
        deterministic_text="FPJ quantieme - [ ] FPJ Elegante Titanium 120k",
        suggested_text="FPJ Elegante Titanium 2022 120k",
        raw_listing_text="FPJ quantieme - [ ] FPJ Elegante Titanium 120k",
        gate_status="rejected",
        gate_reasons=["not_raw_substring"],
    )

    first = database.get_ai_refinement_suggestion(first_id)
    second = database.get_ai_refinement_suggestion(second_id)

    assert second_id != first_id
    assert first is not None
    assert first.review_status == "accepted"
    assert first.suggested_text == "FPJ Elegante Titanium 120k"
    assert second is not None
    assert second.review_status == "open"
    assert second.suggested_text == "FPJ Elegante Titanium 2022 120k"


def test_reviewed_ai_suggestions_export_as_regression_cases(tmp_path) -> None:
    db_path = tmp_path / "data" / "bot.db"
    database = Database(db_path)
    suggestion_id = database.record_ai_refinement_suggestion(
        query_text="Fpj Elegante Titanium",
        result_rank=1,
        mode="review",
        model="gpt-5-mini",
        deterministic_text="FPJ quantieme - [ ] FPJ Elegante Titanium 120k",
        suggested_text="FPJ Elegante Titanium 120k",
        raw_listing_text="FPJ quantieme - [ ] FPJ Elegante Titanium 120k",
        gate_status="accepted",
        gate_reasons=["matches_query", "raw_substring"],
    )

    database.mark_ai_refinement_suggestion_status(
        suggestion_id,
        status="accepted",
        notes="covered by matcher fixture",
    )
    exported = database.export_reviewed_ai_suggestions()

    assert exported == [
        {
            "id": suggestion_id,
            "type": "ai_suggestion",
            "query": "Fpj Elegante Titanium",
            "reason": "ai_reviewed_refinement",
            "shown_text": "FPJ quantieme - [ ] FPJ Elegante Titanium 120k",
            "raw_text": "FPJ quantieme - [ ] FPJ Elegante Titanium 120k",
            "expected_text": "FPJ Elegante Titanium 120k",
            "suggested_text": "FPJ Elegante Titanium 120k",
            "source_url": None,
            "gate_status": "accepted",
            "gate_reasons": ["matches_query", "raw_substring"],
            "review_status": "accepted",
            "issue_type": None,
            "issue_id": None,
            "model": "gpt-5-mini",
            "prompt_version": "watchfacts-refine-v1",
        }
    ]


def test_ai_refinement_suggestion_schema_migrates_existing_table(tmp_path) -> None:
    db_path = tmp_path / "data" / "bot.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE ai_refinement_suggestions (
                id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL,
                query_text TEXT NOT NULL,
                normalized_query TEXT NOT NULL,
                result_rank INTEGER NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL,
                deterministic_text TEXT NOT NULL,
                suggested_text TEXT NOT NULL,
                raw_listing_text TEXT,
                source_url TEXT,
                gate_status TEXT NOT NULL,
                gate_reasons TEXT NOT NULL,
                latency_ms INTEGER
            )
            """
        )

    database = Database(db_path)
    suggestion_id = database.record_ai_refinement_suggestion(
        query_text="Fpj Elegante Titanium",
        result_rank=1,
        mode="review",
        model="gpt-5-mini",
        deterministic_text="FPJ quantieme - [ ] FPJ Elegante Titanium 120k",
        suggested_text="FPJ Elegante Titanium 120k",
        raw_listing_text="FPJ quantieme - [ ] FPJ Elegante Titanium 120k",
        gate_status="accepted",
        gate_reasons=["matches_query"],
    )

    suggestion = database.get_ai_refinement_suggestion(suggestion_id)

    assert suggestion is not None
    assert suggestion.review_status == "open"
    assert suggestion.prompt_version == "watchfacts-refine-v1"


def test_search_cache_round_trips_fresh_payload_and_expires(tmp_path) -> None:
    db_path = tmp_path / "data" / "bot.db"
    database = Database(db_path)

    assert database.get_fresh_search_cache("cache-key") is None

    database.record_search_cache(
        cache_key="cache-key",
        query_text="7118/1200a white",
        result_json='[{"listing_text":"7118/1200A WHITE"}]',
        result_count=1,
        ttl_seconds=300,
    )

    assert (
        database.get_fresh_search_cache("cache-key")
        == '[{"listing_text":"7118/1200A WHITE"}]'
    )

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE search_cache SET expires_at = '2000-01-01T00:00:00+00:00'"
        )

    assert database.get_fresh_search_cache("cache-key") is None


def test_result_feedback_records_and_dedupes_reports(tmp_path) -> None:
    db_path = tmp_path / "data" / "bot.db"
    database = Database(db_path)

    first_id = database.record_result_feedback(
        query_text="5712r",
        result_rank=26,
        reason="missing_info",
        listing_text="5712R 2016/ HKD",
        raw_listing_text="5712R 2016/ HKD 830000",
        seller="AM.Timepiece TONY",
        posted_date="February 14, 2026",
        source_url="/flash-sales/9927122",
        telegram_user_id=123,
    )
    second_id = database.record_result_feedback(
        query_text="5712r",
        result_rank=26,
        reason="missing_info",
        listing_text="5712R 2016/ HKD",
        raw_listing_text="5712R 2016/ HKD 830000",
        seller="AM.Timepiece TONY",
        source_url="/flash-sales/9927122",
        telegram_user_id=123,
    )

    assert second_id == first_id
    issue = database.get_issue(first_id)

    assert issue is not None
    assert issue.issue_type == "feedback"
    assert issue.reason == "missing_info"
    assert issue.report_count == 2
    assert issue.raw_listing_text == "5712R 2016/ HKD 830000"
    assert issue.source_url == "/flash-sales/9927122"


def test_suspicious_result_records_and_exports_open_issues(tmp_path) -> None:
    db_path = tmp_path / "data" / "bot.db"
    database = Database(db_path)

    database.record_suspicious_result(
        query_text="5712r",
        result_rank=26,
        reason="ends_with_currency",
        severity=3,
        listing_text="5712R 2016/ HKD",
        raw_listing_text="5712R 2016/ HKD 830000",
        source_url="/flash-sales/9927122",
    )
    database.record_suspicious_result(
        query_text="5712r",
        result_rank=26,
        reason="ends_with_currency",
        severity=2,
        listing_text="5712R 2016/ HKD",
        raw_listing_text="5712R 2016/ HKD 830000",
        source_url="/flash-sales/9927122",
    )

    issues = database.list_open_suspicious_issues()
    exported = database.export_open_suspicious_issues()

    assert len(issues) == 1
    assert issues[0].issue_type == "suspicious"
    assert issues[0].reason == "ends_with_currency"
    assert issues[0].severity == 3
    assert exported[0]["shown_text"] == "5712R 2016/ HKD"
    assert exported[0]["raw_text"] == "5712R 2016/ HKD 830000"
    assert database.list_open_issues() == []
    assert database.export_open_issues() == []


def test_suspicious_queue_filters_and_summarizes_open_issues(tmp_path) -> None:
    db_path = tmp_path / "data" / "bot.db"
    database = Database(db_path)
    database.record_suspicious_result(
        query_text="5712r",
        result_rank=26,
        reason="ends_with_currency",
        severity=3,
        listing_text="5712R 2016/ HKD",
    )
    database.record_suspicious_result(
        query_text="6102r",
        result_rank=12,
        reason="missing_price_evidence",
        severity=1,
        listing_text="Patek 6102R good price",
    )

    severity_three = database.list_open_suspicious_issues(min_severity=3)
    summary = database.summarize_open_suspicious_issues()

    assert [issue.query_text for issue in severity_three] == ["5712r"]
    assert [(item.reason, item.severity, item.issue_count) for item in summary] == [
        ("ends_with_currency", 3, 1),
        ("missing_price_evidence", 1, 1),
    ]


def test_mark_issue_status_closes_feedback_and_suspicious_issues(tmp_path) -> None:
    db_path = tmp_path / "data" / "bot.db"
    database = Database(db_path)
    feedback_id = database.record_result_feedback(
        query_text="5712r",
        result_rank=26,
        reason="missing_info",
        listing_text="5712R 2016/ HKD",
        raw_listing_text="5712R 2016/ HKD 830000",
        telegram_user_id=123,
    )
    database.record_suspicious_result(
        query_text="5712r",
        result_rank=27,
        reason="ends_with_currency",
        severity=3,
        listing_text="5712R 2012 fullset HKD",
        raw_listing_text="5712R 2012 fullset HKD 777000",
    )
    suspicious_id = database.list_open_suspicious_issues()[0].id

    feedback = database.mark_issue_status(
        feedback_id,
        issue_type="feedback",
        status="fixed",
        notes="covered by regression",
    )
    suspicious = database.mark_issue_status(
        suspicious_id,
        issue_type="suspicious",
        status="ignored",
    )

    assert feedback is not None
    assert feedback.issue_status == "fixed"
    assert suspicious is not None
    assert suspicious.issue_status == "ignored"
    assert database.list_open_issues() == []
    assert database.export_open_issues() == []
    assert database.list_open_suspicious_issues() == []
    assert database.export_open_suspicious_issues() == []


def test_issue_status_filters_include_review_notes(tmp_path) -> None:
    db_path = tmp_path / "data" / "bot.db"
    database = Database(db_path)
    fixed_id = database.record_result_feedback(
        query_text="5712r",
        result_rank=1,
        reason="missing_info",
        listing_text="5712R 2016/ HKD",
    )
    ignored_id = database.record_result_feedback(
        query_text="5205r",
        result_rank=2,
        reason="wrong_result",
        listing_text="5205R wrong dial",
    )
    database.mark_issue_status(
        fixed_id,
        issue_type="feedback",
        status="fixed",
        notes="Fixture passed after deploy.",
    )
    database.mark_issue_status(
        ignored_id,
        issue_type="feedback",
        status="ignored",
        notes="Source lacks enough detail.",
    )

    fixed = database.list_feedback_issues(status="fixed")
    ignored = database.list_feedback_issues(status="ignored")
    all_issues = database.list_feedback_issues(status="all")

    assert [(issue.id, issue.review_notes) for issue in fixed] == [
        (fixed_id, "Fixture passed after deploy.")
    ]
    assert [(issue.id, issue.review_notes) for issue in ignored] == [
        (ignored_id, "Source lacks enough detail.")
    ]
    assert {issue.id for issue in all_issues} == {fixed_id, ignored_id}


def test_repeated_feedback_reopens_fixed_issue(tmp_path) -> None:
    db_path = tmp_path / "data" / "bot.db"
    database = Database(db_path)
    issue_id = database.record_result_feedback(
        query_text="5712r",
        result_rank=26,
        reason="missing_info",
        listing_text="5712R 2016/ HKD",
        telegram_user_id=123,
    )
    database.mark_issue_status(issue_id, issue_type="feedback", status="fixed")

    repeated_id = database.record_result_feedback(
        query_text="5712r",
        result_rank=26,
        reason="missing_info",
        listing_text="5712R 2016/ HKD",
        telegram_user_id=123,
    )

    assert repeated_id == issue_id
    issue = database.get_issue(issue_id, issue_type="feedback")
    assert issue is not None
    assert issue.issue_status == "open"
    assert issue.report_count == 2


def test_repeated_suspicious_result_reopens_fixed_but_not_ignored_issue(tmp_path) -> None:
    db_path = tmp_path / "data" / "bot.db"
    database = Database(db_path)
    database.record_suspicious_result(
        query_text="5712r",
        result_rank=26,
        reason="ends_with_currency",
        severity=3,
        listing_text="5712R 2016/ HKD",
    )
    issue_id = database.list_open_suspicious_issues()[0].id
    database.mark_issue_status(issue_id, issue_type="suspicious", status="fixed")

    database.record_suspicious_result(
        query_text="5712r",
        result_rank=26,
        reason="ends_with_currency",
        severity=3,
        listing_text="5712R 2016/ HKD",
    )

    issue = database.get_issue(issue_id, issue_type="suspicious")
    assert issue is not None
    assert issue.issue_status == "open"

    database.mark_issue_status(issue_id, issue_type="suspicious", status="ignored")
    database.record_suspicious_result(
        query_text="5712r",
        result_rank=26,
        reason="ends_with_currency",
        severity=3,
        listing_text="5712R 2016/ HKD",
    )

    issue = database.get_issue(issue_id, issue_type="suspicious")
    assert issue is not None
    assert issue.issue_status == "ignored"
