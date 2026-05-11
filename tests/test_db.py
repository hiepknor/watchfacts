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

    assert {"queries", "listings", "query_results"} <= tables


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
