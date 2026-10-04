from __future__ import annotations

import os
import secrets
from pathlib import Path

from app.config import Settings
from app.db import Database


def check_runtime_readiness(settings: Settings) -> None:
    """Validate the shared persistence required by the bot and web adapters."""

    _check_database_ready(Database(settings.db_path))
    _check_directory_writable(settings.result_page_storage_dir)


def _check_database_ready(database: Database) -> None:
    with database.connect() as connection:
        table = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'result_feedback'
            """
        ).fetchone()
    if table is None:
        database.initialize()


def _check_directory_writable(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / f".watchfacts-health-{secrets.token_hex(6)}.tmp"
    try:
        marker.write_text("ok", encoding="utf-8")
        with marker.open("r+b") as handle:
            os.fsync(handle.fileno())
    finally:
        try:
            marker.unlink()
        except FileNotFoundError:
            pass
