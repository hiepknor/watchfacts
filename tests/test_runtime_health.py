from __future__ import annotations

import sqlite3

from app.config import load_search_settings
from app.infrastructure.runtime_health import check_runtime_readiness


def test_runtime_readiness_initializes_database_and_checks_storage(tmp_path) -> None:
    settings = load_search_settings(
        env={"RESULT_PAGE_STORAGE_DIR": str(tmp_path / "pages")},
        project_root=tmp_path,
    )

    check_runtime_readiness(settings)

    with sqlite3.connect(settings.db_path) as connection:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'result_feedback'"
        ).fetchone()
    assert table == ("result_feedback",)
    assert list(settings.result_page_storage_dir.iterdir()) == []
