"""
Copyright (C) 2026 Lightwell

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

         http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import sqlite3
from pathlib import Path

from import_orchestrator.database import WorkflowDatabase, warn_if_legacy_database


def _write_legacy_db(db_path: Path, rows: int) -> None:
    """Create a pre-cutover ``import_items`` table with ``rows`` entries."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE import_items (id INTEGER PRIMARY KEY, ref TEXT, status TEXT)")
    for i in range(rows):
        conn.execute("INSERT INTO import_items (ref, status) VALUES (?, ?)", (f"ref-{i}", "pending"))
    conn.commit()
    conn.close()


class TestLegacyDetection:
    def test_counts_legacy_items_on_open(self, tmp_path: Path):
        db_path = tmp_path / "legacy.db"
        _write_legacy_db(db_path, rows=3)

        with WorkflowDatabase(db_path) as db:
            assert db.legacy_item_count == 3

    def test_no_legacy_table_is_zero(self, tmp_path: Path):
        with WorkflowDatabase(tmp_path / "fresh.db") as db:
            assert db.legacy_item_count == 0


class TestWarnIfLegacyDatabase:
    def test_warns_when_legacy_items_present(self, tmp_path: Path, capsys):
        db_path = tmp_path / "legacy.db"
        _write_legacy_db(db_path, rows=2)

        with WorkflowDatabase(db_path) as db:
            warned = warn_if_legacy_database(db)

        assert warned is True
        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "2 legacy import item(s)" in err
        assert "--reset" in err

    def test_silent_on_fresh_database(self, tmp_path: Path, capsys):
        with WorkflowDatabase(tmp_path / "fresh.db") as db:
            warned = warn_if_legacy_database(db)

        assert warned is False
        assert capsys.readouterr().err == ""
