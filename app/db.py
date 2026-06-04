from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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

CREATE TABLE IF NOT EXISTS llm_refinements (
    id INTEGER PRIMARY KEY,
    normalized_query TEXT NOT NULL,
    listing_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    refined_text TEXT NOT NULL,
    latency_ms INTEGER,
    created_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL,
    UNIQUE(normalized_query, listing_hash, model)
);

CREATE TABLE IF NOT EXISTS result_feedback (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    query_text TEXT NOT NULL,
    normalized_query TEXT NOT NULL,
    result_rank INTEGER NOT NULL,
    reason TEXT NOT NULL,
    report_count INTEGER NOT NULL,
    telegram_user_id INTEGER,
    listing_text TEXT NOT NULL,
    raw_listing_text TEXT,
    seller TEXT,
    posted_date TEXT,
    source_url TEXT,
    issue_status TEXT NOT NULL,
    review_notes TEXT,
    UNIQUE(normalized_query, result_rank, reason, telegram_user_id, listing_text)
);

CREATE TABLE IF NOT EXISTS suspicious_results (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    query_text TEXT NOT NULL,
    normalized_query TEXT NOT NULL,
    result_rank INTEGER NOT NULL,
    reason TEXT NOT NULL,
    severity INTEGER NOT NULL,
    listing_text TEXT NOT NULL,
    raw_listing_text TEXT,
    source_url TEXT,
    reviewed_at TEXT,
    issue_status TEXT NOT NULL DEFAULT 'open',
    review_notes TEXT,
    UNIQUE(normalized_query, result_rank, reason, listing_text)
);

CREATE TABLE IF NOT EXISTS search_cache (
    cache_key TEXT PRIMARY KEY,
    query_text TEXT NOT NULL,
    normalized_query TEXT NOT NULL,
    result_json TEXT NOT NULL,
    result_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_refinement_suggestions (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT '',
    query_text TEXT NOT NULL,
    normalized_query TEXT NOT NULL,
    result_rank INTEGER NOT NULL,
    mode TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL DEFAULT 'watchfacts-refine-v1',
    raw_listing_hash TEXT NOT NULL DEFAULT '',
    suggestion_key TEXT NOT NULL DEFAULT '',
    issue_type TEXT,
    issue_id INTEGER,
    deterministic_text TEXT NOT NULL,
    suggested_text TEXT NOT NULL,
    raw_listing_text TEXT,
    source_url TEXT,
    gate_status TEXT NOT NULL,
    gate_reasons TEXT NOT NULL,
    latency_ms INTEGER,
    review_status TEXT NOT NULL DEFAULT 'open',
    review_notes TEXT,
    reviewed_at TEXT
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


@dataclass(frozen=True)
class IssueRecord:
    id: int
    issue_type: str
    query_text: str
    result_rank: int
    reason: str
    listing_text: str
    raw_listing_text: str | None
    seller: str | None
    posted_date: str | None
    source_url: str | None
    report_count: int
    severity: int | None
    issue_status: str


@dataclass(frozen=True)
class SuspiciousIssueSummary:
    reason: str
    severity: int
    issue_count: int
    query_count: int
    latest_issue_id: int
    sample_query: str


@dataclass(frozen=True)
class AIRefinementSuggestionRecord:
    id: int
    query_text: str
    normalized_query: str
    result_rank: int
    mode: str
    model: str
    prompt_version: str
    issue_type: str | None
    issue_id: int | None
    deterministic_text: str
    suggested_text: str
    raw_listing_text: str | None
    source_url: str | None
    gate_status: str
    gate_reasons: tuple[str, ...]
    latency_ms: int | None
    review_status: str
    review_notes: str | None


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            _ensure_schema(connection)

    def record_query_results(
        self,
        query_text: str,
        listings: Iterable[ListingLike],
    ) -> QueryRecord:
        listing_rows = list(listings)
        now = _utc_now()
        normalized_query = normalize_text(query_text)

        with self.connect() as connection:
            _ensure_schema(connection)
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

    def get_fresh_search_cache(self, cache_key: str) -> str | None:
        now = _utc_now()
        with self.connect() as connection:
            _ensure_schema(connection)
            row = connection.execute(
                """
                SELECT result_json
                FROM search_cache
                WHERE cache_key = ? AND expires_at > ?
                """,
                (cache_key, now),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE search_cache
                SET last_used_at = ?
                WHERE cache_key = ?
                """,
                (now, cache_key),
            )
            return str(row[0])

    def record_search_cache(
        self,
        *,
        cache_key: str,
        query_text: str,
        result_json: str,
        result_count: int,
        ttl_seconds: int,
    ) -> None:
        now = datetime.now(UTC)
        created_at = now.isoformat(timespec="seconds")
        expires_at_text = (now + timedelta(seconds=ttl_seconds)).isoformat(
            timespec="seconds"
        )
        normalized_query = normalize_text(query_text)

        with self.connect() as connection:
            _ensure_schema(connection)
            connection.execute(
                """
                INSERT INTO search_cache (
                    cache_key,
                    query_text,
                    normalized_query,
                    result_json,
                    result_count,
                    created_at,
                    expires_at,
                    last_used_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    query_text = excluded.query_text,
                    normalized_query = excluded.normalized_query,
                    result_json = excluded.result_json,
                    result_count = excluded.result_count,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at,
                    last_used_at = excluded.last_used_at
                """,
                (
                    cache_key,
                    query_text,
                    normalized_query,
                    result_json,
                    result_count,
                    created_at,
                    expires_at_text,
                    created_at,
                ),
            )

    def get_llm_refinement(
        self,
        query_text: str,
        listing_text: str,
        model: str,
    ) -> str | None:
        now = _utc_now()
        normalized_query = normalize_text(query_text)
        listing_hash = _listing_hash(listing_text)

        with self.connect() as connection:
            _ensure_schema(connection)
            row = connection.execute(
                """
                SELECT refined_text
                FROM llm_refinements
                WHERE normalized_query = ? AND listing_hash = ? AND model = ?
                """,
                (normalized_query, listing_hash, model),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE llm_refinements
                SET last_used_at = ?
                WHERE normalized_query = ? AND listing_hash = ? AND model = ?
                """,
                (now, normalized_query, listing_hash, model),
            )
            return str(row[0])

    def record_llm_refinement(
        self,
        query_text: str,
        listing_text: str,
        model: str,
        refined_text: str,
        *,
        latency_ms: int | None = None,
    ) -> None:
        now = _utc_now()
        normalized_query = normalize_text(query_text)
        listing_hash = _listing_hash(listing_text)

        with self.connect() as connection:
            _ensure_schema(connection)
            connection.execute(
                """
                INSERT INTO llm_refinements (
                    normalized_query,
                    listing_hash,
                    model,
                    refined_text,
                    latency_ms,
                    created_at,
                    last_used_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(normalized_query, listing_hash, model) DO UPDATE SET
                    refined_text = excluded.refined_text,
                    latency_ms = excluded.latency_ms,
                    last_used_at = excluded.last_used_at
                """,
                (
                    normalized_query,
                    listing_hash,
                    model,
                    refined_text,
                    latency_ms,
                    now,
                    now,
                ),
            )

    def record_ai_refinement_suggestion(
        self,
        *,
        query_text: str,
        result_rank: int,
        mode: str,
        model: str,
        deterministic_text: str,
        suggested_text: str,
        raw_listing_text: str | None = None,
        source_url: str | None = None,
        gate_status: str,
        gate_reasons: Iterable[str],
        latency_ms: int | None = None,
        prompt_version: str = "watchfacts-refine-v1",
    ) -> int:
        now = _utc_now()
        normalized_query = normalize_text(query_text)
        raw_text_for_key = raw_listing_text or deterministic_text
        raw_listing_hash = _listing_hash(raw_text_for_key)
        suggested_text_hash = _listing_hash(suggested_text)
        suggestion_key = _suggestion_key(
            normalized_query,
            raw_listing_hash,
            suggested_text_hash,
            model,
            prompt_version,
        )
        reasons_json = json.dumps(list(gate_reasons), separators=(",", ":"))

        with self.connect() as connection:
            _ensure_schema(connection)
            issue_type, issue_id = _find_related_issue(
                connection,
                normalized_query=normalized_query,
                result_rank=result_rank,
                listing_text=deterministic_text,
                raw_listing_text=raw_listing_text,
            )
            existing = connection.execute(
                """
                SELECT id
                FROM ai_refinement_suggestions
                WHERE suggestion_key = ?
                LIMIT 1
                """,
                (suggestion_key,),
            ).fetchone()
            if existing is not None:
                suggestion_id = int(existing[0])
                connection.execute(
                    """
                    UPDATE ai_refinement_suggestions
                    SET updated_at = ?,
                        query_text = ?,
                        result_rank = ?,
                        mode = ?,
                        deterministic_text = ?,
                        suggested_text = ?,
                        raw_listing_text = ?,
                        source_url = ?,
                        gate_status = ?,
                        gate_reasons = ?,
                        latency_ms = ?,
                        issue_type = COALESCE(?, issue_type),
                        issue_id = COALESCE(?, issue_id)
                    WHERE id = ?
                    """,
                    (
                        now,
                        query_text,
                        result_rank,
                        mode,
                        deterministic_text,
                        suggested_text,
                        raw_listing_text,
                        source_url,
                        gate_status,
                        reasons_json,
                        latency_ms,
                        issue_type,
                        issue_id,
                        suggestion_id,
                    ),
                )
                return suggestion_id
            cursor = connection.execute(
                """
                INSERT INTO ai_refinement_suggestions (
                    created_at,
                    updated_at,
                    query_text,
                    normalized_query,
                    result_rank,
                    mode,
                    model,
                    prompt_version,
                    raw_listing_hash,
                    suggestion_key,
                    issue_type,
                    issue_id,
                    deterministic_text,
                    suggested_text,
                    raw_listing_text,
                    source_url,
                    gate_status,
                    gate_reasons,
                    latency_ms,
                    review_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
                """,
                (
                    now,
                    now,
                    query_text,
                    normalized_query,
                    result_rank,
                    mode,
                    model,
                    prompt_version,
                    raw_listing_hash,
                    suggestion_key,
                    issue_type,
                    issue_id,
                    deterministic_text,
                    suggested_text,
                    raw_listing_text,
                    source_url,
                    gate_status,
                    reasons_json,
                    latency_ms,
                ),
            )
        return int(cursor.lastrowid)

    def list_ai_refinement_suggestions(
        self,
        *,
        limit: int = 20,
        review_status: str | None = None,
    ) -> list[AIRefinementSuggestionRecord]:
        with self.connect() as connection:
            _ensure_schema(connection)
            where = ""
            params: tuple[object, ...] = (limit,)
            if review_status is not None:
                where = "WHERE review_status = ?"
                params = (review_status, limit)
            rows = connection.execute(
                f"""
                SELECT
                    id,
                    query_text,
                    normalized_query,
                    result_rank,
                    mode,
                    model,
                    prompt_version,
                    issue_type,
                    issue_id,
                    deterministic_text,
                    suggested_text,
                    raw_listing_text,
                    source_url,
                    gate_status,
                    gate_reasons,
                    latency_ms,
                    review_status,
                    review_notes
                FROM ai_refinement_suggestions
                {where}
                ORDER BY id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [_ai_refinement_suggestion_from_row(row) for row in rows]

    def get_ai_refinement_suggestion(
        self,
        suggestion_id: int,
    ) -> AIRefinementSuggestionRecord | None:
        with self.connect() as connection:
            _ensure_schema(connection)
            row = connection.execute(
                """
                SELECT
                    id,
                    query_text,
                    normalized_query,
                    result_rank,
                    mode,
                    model,
                    prompt_version,
                    issue_type,
                    issue_id,
                    deterministic_text,
                    suggested_text,
                    raw_listing_text,
                    source_url,
                    gate_status,
                    gate_reasons,
                    latency_ms,
                    review_status,
                    review_notes
                FROM ai_refinement_suggestions
                WHERE id = ?
                """,
                (suggestion_id,),
            ).fetchone()
        return _ai_refinement_suggestion_from_row(row) if row is not None else None

    def mark_ai_refinement_suggestion_status(
        self,
        suggestion_id: int,
        *,
        status: str,
        notes: str | None = None,
    ) -> AIRefinementSuggestionRecord | None:
        if status not in {"open", "accepted", "ignored"}:
            raise ValueError(f"Unsupported AI suggestion status: {status}")

        now = _utc_now()
        reviewed_at = None if status == "open" else now
        with self.connect() as connection:
            _ensure_schema(connection)
            cursor = connection.execute(
                """
                UPDATE ai_refinement_suggestions
                SET review_status = ?,
                    review_notes = ?,
                    reviewed_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (status, notes, reviewed_at, now, suggestion_id),
            )
            if not cursor.rowcount:
                return None
        return self.get_ai_refinement_suggestion(suggestion_id)

    def export_reviewed_ai_suggestions(
        self,
        *,
        status: str = "accepted",
        limit: int = 50,
    ) -> list[dict[str, object]]:
        return [
            {
                "id": suggestion.id,
                "type": "ai_suggestion",
                "query": suggestion.query_text,
                "reason": "ai_reviewed_refinement",
                "shown_text": suggestion.deterministic_text,
                "raw_text": suggestion.raw_listing_text or suggestion.deterministic_text,
                "expected_text": suggestion.suggested_text,
                "suggested_text": suggestion.suggested_text,
                "source_url": suggestion.source_url,
                "gate_status": suggestion.gate_status,
                "gate_reasons": list(suggestion.gate_reasons),
                "review_status": suggestion.review_status,
                "issue_type": suggestion.issue_type,
                "issue_id": suggestion.issue_id,
                "model": suggestion.model,
                "prompt_version": suggestion.prompt_version,
            }
            for suggestion in self.list_ai_refinement_suggestions(
                limit=limit,
                review_status=status,
            )
        ]

    def record_result_feedback(
        self,
        *,
        query_text: str,
        result_rank: int,
        reason: str,
        listing_text: str,
        raw_listing_text: str | None = None,
        seller: str | None = None,
        posted_date: str | None = None,
        source_url: str | None = None,
        telegram_user_id: int | None = None,
    ) -> int:
        now = _utc_now()
        normalized_query = normalize_text(query_text)

        with self.connect() as connection:
            _ensure_schema(connection)
            connection.execute(
                """
                INSERT INTO result_feedback (
                    created_at,
                    updated_at,
                    query_text,
                    normalized_query,
                    result_rank,
                    reason,
                    report_count,
                    telegram_user_id,
                    listing_text,
                    raw_listing_text,
                    seller,
                    posted_date,
                    source_url,
                    issue_status
                )
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, 'open')
                ON CONFLICT(
                    normalized_query,
                    result_rank,
                    reason,
                    telegram_user_id,
                    listing_text
                ) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    report_count = result_feedback.report_count + 1,
                    raw_listing_text = COALESCE(excluded.raw_listing_text, result_feedback.raw_listing_text),
                    seller = COALESCE(excluded.seller, result_feedback.seller),
                    posted_date = COALESCE(excluded.posted_date, result_feedback.posted_date),
                    source_url = COALESCE(excluded.source_url, result_feedback.source_url),
                    issue_status = 'open',
                    review_notes = NULL
                """,
                (
                    now,
                    now,
                    query_text,
                    normalized_query,
                    result_rank,
                    reason,
                    telegram_user_id,
                    listing_text,
                    raw_listing_text,
                    seller,
                    posted_date,
                    source_url,
                ),
            )
            row = connection.execute(
                """
                SELECT id
                FROM result_feedback
                WHERE normalized_query = ?
                  AND result_rank = ?
                  AND reason = ?
                  AND telegram_user_id IS ?
                  AND listing_text = ?
                """,
                (
                    normalized_query,
                    result_rank,
                    reason,
                    telegram_user_id,
                    listing_text,
                ),
            ).fetchone()
        if row is None:
            raise RuntimeError("Failed to load feedback issue after upsert")
        return int(row[0])

    def record_suspicious_result(
        self,
        *,
        query_text: str,
        result_rank: int,
        reason: str,
        severity: int,
        listing_text: str,
        raw_listing_text: str | None = None,
        source_url: str | None = None,
    ) -> None:
        now = _utc_now()
        normalized_query = normalize_text(query_text)

        with self.connect() as connection:
            _ensure_schema(connection)
            connection.execute(
                """
                INSERT INTO suspicious_results (
                    created_at,
                    query_text,
                    normalized_query,
                    result_rank,
                    reason,
                    severity,
                    listing_text,
                    raw_listing_text,
                    source_url
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(normalized_query, result_rank, reason, listing_text)
                DO UPDATE SET
                    severity = max(suspicious_results.severity, excluded.severity),
                    raw_listing_text = COALESCE(excluded.raw_listing_text, suspicious_results.raw_listing_text),
                    source_url = COALESCE(excluded.source_url, suspicious_results.source_url),
                    issue_status = CASE
                        WHEN suspicious_results.issue_status = 'ignored' THEN 'ignored'
                        ELSE 'open'
                    END,
                    reviewed_at = CASE
                        WHEN suspicious_results.issue_status = 'ignored' THEN suspicious_results.reviewed_at
                        ELSE NULL
                    END,
                    review_notes = CASE
                        WHEN suspicious_results.issue_status = 'ignored' THEN suspicious_results.review_notes
                        ELSE NULL
                    END
                """,
                (
                    now,
                    query_text,
                    normalized_query,
                    result_rank,
                    reason,
                    severity,
                    listing_text,
                    raw_listing_text,
                    source_url,
                ),
            )

    def list_open_issues(self, *, limit: int = 10) -> list[IssueRecord]:
        return self.list_open_feedback_issues(limit=limit)

    def list_open_feedback_issues(self, *, limit: int = 10) -> list[IssueRecord]:
        with self.connect() as connection:
            _ensure_schema(connection)
            rows = connection.execute(
                """
                SELECT
                    id,
                    'feedback' AS issue_type,
                    query_text,
                    result_rank,
                    reason,
                    listing_text,
                    raw_listing_text,
                    seller,
                    posted_date,
                    source_url,
                    report_count,
                    NULL AS severity,
                    issue_status
                FROM result_feedback
                WHERE issue_status = 'open'
                ORDER BY report_count DESC, updated_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_issue_record_from_row(row) for row in rows]

    def list_open_suspicious_issues(
        self,
        *,
        limit: int = 10,
        min_severity: int | None = None,
    ) -> list[IssueRecord]:
        where = "WHERE issue_status = 'open'"
        params: tuple[object, ...]
        if min_severity is not None:
            where += " AND severity >= ?"
            params = (min_severity, limit)
        else:
            params = (limit,)

        with self.connect() as connection:
            _ensure_schema(connection)
            rows = connection.execute(
                f"""
                SELECT
                    id,
                    'suspicious' AS issue_type,
                    query_text,
                    result_rank,
                    reason,
                    listing_text,
                    raw_listing_text,
                    NULL AS seller,
                    NULL AS posted_date,
                    source_url,
                    1 AS report_count,
                    severity,
                    issue_status
                FROM suspicious_results
                {where}
                ORDER BY severity DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [_issue_record_from_row(row) for row in rows]

    def summarize_open_suspicious_issues(
        self,
        *,
        limit: int = 20,
    ) -> list[SuspiciousIssueSummary]:
        with self.connect() as connection:
            _ensure_schema(connection)
            rows = connection.execute(
                """
                SELECT
                    reason,
                    severity,
                    COUNT(*) AS issue_count,
                    COUNT(DISTINCT normalized_query) AS query_count,
                    MAX(id) AS latest_issue_id,
                    (
                        SELECT s2.query_text
                        FROM suspicious_results AS s2
                        WHERE s2.issue_status = 'open'
                          AND s2.reason = suspicious_results.reason
                          AND s2.severity = suspicious_results.severity
                        ORDER BY s2.id DESC
                        LIMIT 1
                    ) AS sample_query
                FROM suspicious_results
                WHERE issue_status = 'open'
                GROUP BY reason, severity
                ORDER BY severity DESC, issue_count DESC, reason
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            SuspiciousIssueSummary(
                reason=str(row[0]),
                severity=int(row[1]),
                issue_count=int(row[2]),
                query_count=int(row[3]),
                latest_issue_id=int(row[4]),
                sample_query=str(row[5]),
            )
            for row in rows
        ]

    def get_issue(self, issue_id: int, *, issue_type: str | None = None) -> IssueRecord | None:
        with self.connect() as connection:
            _ensure_schema(connection)
            feedback = None
            if issue_type in {None, "feedback"}:
                feedback = connection.execute(
                    """
                    SELECT
                        id,
                        'feedback' AS issue_type,
                        query_text,
                        result_rank,
                        reason,
                        listing_text,
                        raw_listing_text,
                        seller,
                        posted_date,
                        source_url,
                        report_count,
                        NULL AS severity,
                        issue_status
                    FROM result_feedback
                    WHERE id = ?
                    """,
                    (issue_id,),
                ).fetchone()
                if feedback is not None:
                    return _issue_record_from_row(feedback)

            suspicious = None
            if issue_type in {None, "suspicious"}:
                suspicious = connection.execute(
                    """
                    SELECT
                        id,
                        'suspicious' AS issue_type,
                        query_text,
                        result_rank,
                        reason,
                        listing_text,
                        raw_listing_text,
                        NULL AS seller,
                        NULL AS posted_date,
                        source_url,
                        1 AS report_count,
                        severity,
                        issue_status
                    FROM suspicious_results
                    WHERE id = ?
                    """,
                    (issue_id,),
                ).fetchone()
        return _issue_record_from_row(suspicious) if suspicious is not None else None

    def mark_issue_status(
        self,
        issue_id: int,
        *,
        issue_type: str | None = None,
        status: str,
        notes: str | None = None,
    ) -> IssueRecord | None:
        if status not in {"open", "fixed", "ignored"}:
            raise ValueError(f"Unsupported issue status: {status}")

        now = _utc_now()
        updated_type: str | None = None
        with self.connect() as connection:
            _ensure_schema(connection)
            if issue_type in {None, "feedback"}:
                cursor = connection.execute(
                    """
                    UPDATE result_feedback
                    SET issue_status = ?,
                        review_notes = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (status, notes, now, issue_id),
                )
                if cursor.rowcount:
                    updated_type = "feedback"

            if updated_type is None and issue_type in {None, "suspicious"}:
                reviewed_at = None if status == "open" else now
                cursor = connection.execute(
                    """
                    UPDATE suspicious_results
                    SET issue_status = ?,
                        review_notes = ?,
                        reviewed_at = ?
                    WHERE id = ?
                    """,
                    (status, notes, reviewed_at, issue_id),
                )
                if cursor.rowcount:
                    updated_type = "suspicious"

        return self.get_issue(issue_id, issue_type=updated_type) if updated_type else None

    def export_open_issues(self, *, limit: int = 50) -> list[dict[str, object]]:
        return self._export_issues(self.list_open_feedback_issues(limit=limit))

    def export_open_suspicious_issues(
        self,
        *,
        limit: int = 50,
        min_severity: int | None = None,
    ) -> list[dict[str, object]]:
        return self._export_issues(
            self.list_open_suspicious_issues(
                limit=limit,
                min_severity=min_severity,
            )
        )

    def _export_issues(self, issues: list[IssueRecord]) -> list[dict[str, object]]:
        return [
            {
                "id": issue.id,
                "type": issue.issue_type,
                "query": issue.query_text,
                "reason": issue.reason,
                "shown_text": issue.listing_text,
                "raw_text": issue.raw_listing_text,
                "seller": issue.seller,
                "posted_date": issue.posted_date,
                "source_url": issue.source_url,
                "report_count": issue.report_count,
                "severity": issue.severity,
                "status": issue.issue_status,
            }
            for issue in issues
        ]


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


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    _add_column_if_missing(
        connection,
        "suspicious_results",
        "issue_status",
        "TEXT NOT NULL DEFAULT 'open'",
    )
    _add_column_if_missing(
        connection,
        "suspicious_results",
        "review_notes",
        "TEXT",
    )
    for column, definition in {
        "updated_at": "TEXT NOT NULL DEFAULT ''",
        "prompt_version": "TEXT NOT NULL DEFAULT 'watchfacts-refine-v1'",
        "raw_listing_hash": "TEXT NOT NULL DEFAULT ''",
        "suggestion_key": "TEXT NOT NULL DEFAULT ''",
        "issue_type": "TEXT",
        "issue_id": "INTEGER",
        "review_status": "TEXT NOT NULL DEFAULT 'open'",
        "review_notes": "TEXT",
        "reviewed_at": "TEXT",
    }.items():
        _add_column_if_missing(
            connection,
            "ai_refinement_suggestions",
            column,
            definition,
        )


def _add_column_if_missing(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    if column in {str(row[1]) for row in rows}:
        return
    connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _listing_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _suggestion_key(
    normalized_query: str,
    raw_listing_hash: str,
    suggested_text_hash: str,
    model: str,
    prompt_version: str,
) -> str:
    return _listing_hash(
        "\x1f".join(
            (
                normalized_query,
                raw_listing_hash,
                suggested_text_hash,
                model,
                prompt_version,
            )
        )
    )


def _find_related_issue(
    connection: sqlite3.Connection,
    *,
    normalized_query: str,
    result_rank: int,
    listing_text: str,
    raw_listing_text: str | None,
) -> tuple[str | None, int | None]:
    params = (
        normalized_query,
        result_rank,
        listing_text,
        raw_listing_text,
        raw_listing_text,
    )
    feedback = connection.execute(
        """
        SELECT id
        FROM result_feedback
        WHERE normalized_query = ?
          AND result_rank = ?
          AND listing_text = ?
          AND (? IS NULL OR raw_listing_text = ?)
        ORDER BY id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    if feedback is not None:
        return "feedback", int(feedback[0])

    suspicious = connection.execute(
        """
        SELECT id
        FROM suspicious_results
        WHERE normalized_query = ?
          AND result_rank = ?
          AND listing_text = ?
          AND (? IS NULL OR raw_listing_text = ?)
        ORDER BY id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    if suspicious is not None:
        return "suspicious", int(suspicious[0])
    return None, None


def _issue_record_from_row(row) -> IssueRecord:
    return IssueRecord(
        id=int(row[0]),
        issue_type=str(row[1]),
        query_text=str(row[2]),
        result_rank=int(row[3]),
        reason=str(row[4]),
        listing_text=str(row[5]),
        raw_listing_text=str(row[6]) if row[6] is not None else None,
        seller=str(row[7]) if row[7] is not None else None,
        posted_date=str(row[8]) if row[8] is not None else None,
        source_url=str(row[9]) if row[9] is not None else None,
        report_count=int(row[10]),
        severity=int(row[11]) if row[11] is not None else None,
        issue_status=str(row[12]),
    )


def _ai_refinement_suggestion_from_row(row) -> AIRefinementSuggestionRecord:
    try:
        reasons = json.loads(str(row[14]))
    except json.JSONDecodeError:
        reasons = []
    if not isinstance(reasons, list):
        reasons = []
    return AIRefinementSuggestionRecord(
        id=int(row[0]),
        query_text=str(row[1]),
        normalized_query=str(row[2]),
        result_rank=int(row[3]),
        mode=str(row[4]),
        model=str(row[5]),
        prompt_version=str(row[6]),
        issue_type=str(row[7]) if row[7] is not None else None,
        issue_id=int(row[8]) if row[8] is not None else None,
        deterministic_text=str(row[9]),
        suggested_text=str(row[10]),
        raw_listing_text=str(row[11]) if row[11] is not None else None,
        source_url=str(row[12]) if row[12] is not None else None,
        gate_status=str(row[13]),
        gate_reasons=tuple(str(reason) for reason in reasons),
        latency_ms=int(row[15]) if row[15] is not None else None,
        review_status=str(row[16]),
        review_notes=str(row[17]) if row[17] is not None else None,
    )
