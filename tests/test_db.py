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
        "result_feedback",
        "suspicious_results",
    } <= tables


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

    issues = database.list_open_issues()
    exported = database.export_open_issues()

    assert len(issues) == 1
    assert issues[0].issue_type == "suspicious"
    assert issues[0].reason == "ends_with_currency"
    assert issues[0].severity == 3
    assert exported[0]["shown_text"] == "5712R 2016/ HKD"
    assert exported[0]["raw_text"] == "5712R 2016/ HKD 830000"


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
    suspicious_id = next(
        issue.id
        for issue in database.list_open_issues()
        if issue.issue_type == "suspicious"
    )

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
    issue_id = database.list_open_issues()[0].id
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
