from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Protocol

from app.dedupe import dedupe_key
from app.matcher import normalize_text


SCHEMA = """
CREATE TABLE IF NOT EXISTS queries (
    id INTEGER PRIMARY KEY,
    query_text TEXT NOT NULL,
    normalized_query TEXT NOT NULL,
    created_at TEXT NOT NULL,
    result_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    listing_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    seller TEXT,
    posted_date TEXT,
    image_url TEXT,
    source_url TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS query_results (
    query_id INTEGER NOT NULL REFERENCES queries(id),
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    rank INTEGER NOT NULL,
    PRIMARY KEY (query_id, listing_id)
);
"""


class ListingLike(Protocol):
    listing_text: str
    seller: str | None
    posted_date: str | None
    image_url: str | None
    source_url: str | None


@dataclass(frozen=True)
class QueryRecord:
    id: int
    query_text: str
    normalized_query: str
    result_count: int


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def record_query_results(
        self,
        query_text: str,
        listings: Iterable[ListingLike],
    ) -> QueryRecord:
        listing_rows = list(listings)
        now = _utc_now()
        normalized_query = normalize_text(query_text)

        with self.connect() as connection:
            connection.executescript(SCHEMA)
            cursor = connection.execute(
                """
                INSERT INTO queries (query_text, normalized_query, created_at, result_count)
                VALUES (?, ?, ?, ?)
                """,
                (query_text, normalized_query, now, len(listing_rows)),
            )
            query_id = int(cursor.lastrowid)

            for rank, listing in enumerate(listing_rows, start=1):
                listing_id = _upsert_listing(connection, listing, now)
                connection.execute(
                    """
                    INSERT INTO query_results (query_id, listing_id, rank)
                    VALUES (?, ?, ?)
                    """,
                    (query_id, listing_id, rank),
                )

        return QueryRecord(
            id=query_id,
            query_text=query_text,
            normalized_query=normalized_query,
            result_count=len(listing_rows),
        )


def _upsert_listing(
    connection: sqlite3.Connection,
    listing: ListingLike,
    timestamp: str,
) -> int:
    key = dedupe_key(
        listing.listing_text,
        seller=listing.seller,
        posted_date=listing.posted_date,
    )
    normalized_text = normalize_text(listing.listing_text)

    connection.execute(
        """
        INSERT INTO listings (
            dedupe_key,
            listing_text,
            normalized_text,
            seller,
            posted_date,
            image_url,
            source_url,
            first_seen_at,
            last_seen_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(dedupe_key) DO UPDATE SET
            listing_text = excluded.listing_text,
            normalized_text = excluded.normalized_text,
            seller = excluded.seller,
            posted_date = excluded.posted_date,
            image_url = excluded.image_url,
            source_url = excluded.source_url,
            last_seen_at = excluded.last_seen_at
        """,
        (
            key,
            listing.listing_text,
            normalized_text,
            listing.seller,
            listing.posted_date,
            listing.image_url,
            listing.source_url,
            timestamp,
            timestamp,
        ),
    )
    cursor = connection.execute(
        "SELECT id FROM listings WHERE dedupe_key = ?",
        (key,),
    )
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("Failed to load listing after upsert")
    return int(row[0])


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
