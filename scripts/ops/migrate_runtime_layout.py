from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def migrate_runtime_layout(project_root: Path = PROJECT_ROOT) -> tuple[bool, bool]:
    """Copy legacy runtime files into isolated directories without overwriting."""

    data_dir = project_root / "data"
    browser_source = data_dir / "watchfacts_state.json"
    browser_destination = data_dir / "browser" / "watchfacts_state.json"
    database_source = data_dir / "bot.db"
    database_destination = data_dir / "database" / "bot.db"

    browser_destination.parent.mkdir(parents=True, exist_ok=True)
    database_destination.parent.mkdir(parents=True, exist_ok=True)

    browser_migrated = False
    if browser_source.is_file() and not browser_destination.exists():
        shutil.copy2(browser_source, browser_destination)
        browser_migrated = True

    database_migrated = False
    if database_source.is_file() and not database_destination.exists():
        with sqlite3.connect(database_source) as source:
            with sqlite3.connect(database_destination) as destination:
                source.backup(destination)
        shutil.copystat(database_source, database_destination)
        database_migrated = True

    return browser_migrated, database_migrated


def main() -> int:
    browser_migrated, database_migrated = migrate_runtime_layout()
    if browser_migrated:
        print("Migrated legacy WatchFacts browser state.")
    if database_migrated:
        print("Migrated legacy WatchFacts SQLite database.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
