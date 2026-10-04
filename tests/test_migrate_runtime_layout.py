from __future__ import annotations

import json
import sqlite3

from scripts.ops.migrate_runtime_layout import migrate_runtime_layout


def test_migrate_runtime_layout_uses_sqlite_backup_and_preserves_sources(tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    browser_source = data_dir / "watchfacts_state.json"
    browser_source.write_text(json.dumps({"cookies": [{"name": "session"}]}))
    database_source = data_dir / "bot.db"
    with sqlite3.connect(database_source) as connection:
        connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample VALUES ('preserved')")

    migrated = migrate_runtime_layout(tmp_path)

    assert migrated == (True, True)
    assert browser_source.exists()
    assert database_source.exists()
    assert (data_dir / "browser" / "watchfacts_state.json").read_text() == (
        browser_source.read_text()
    )
    with sqlite3.connect(data_dir / "database" / "bot.db") as connection:
        assert connection.execute("SELECT value FROM sample").fetchone() == (
            "preserved",
        )


def test_migrate_runtime_layout_does_not_overwrite_destinations(tmp_path) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "browser").mkdir(parents=True)
    (data_dir / "database").mkdir(parents=True)
    (data_dir / "watchfacts_state.json").write_text("legacy")
    (data_dir / "browser" / "watchfacts_state.json").write_text("current")
    with sqlite3.connect(data_dir / "bot.db") as connection:
        connection.execute("CREATE TABLE legacy (value TEXT)")
    with sqlite3.connect(data_dir / "database" / "bot.db") as connection:
        connection.execute("CREATE TABLE current (value TEXT)")

    migrated = migrate_runtime_layout(tmp_path)

    assert migrated == (False, False)
    assert (data_dir / "browser" / "watchfacts_state.json").read_text() == "current"
    with sqlite3.connect(data_dir / "database" / "bot.db") as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'current'"
        ).fetchone() == ("current",)
