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

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from import_orchestrator.models import ImportItem, ImportStatus


class ImportDatabase:
    """SQLite database for tracking import state.

    Used as a context manager to ensure the connection is properly opened and closed::

        with ImportDatabase(Path("state.db")) as db:
            db.add_item("quay.io/repo:tag@sha256:abc")
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> ImportDatabase:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._initialize_schema()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            self.conn.close()

    def _initialize_schema(self) -> None:
        """Create tables and indexes if they don't exist."""
        assert self.conn is not None
        cursor = self.conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS import_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ref TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL CHECK(
                   status IN ('pending', 'triggered', 'running', 'releasing', 'success', 'failed')
                ),
                pipelinerun_name TEXT,
                snapshot_name TEXT,
                release_name TEXT,
                triggered_at TIMESTAMP,
                completed_at TIMESTAMP,
                last_checked_at TIMESTAMP,
                error_message TEXT,
                retry_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_status ON import_items(status)
        """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_pipelinerun_name ON import_items(pipelinerun_name)
        """
        )

        for col in ("release_name TEXT", "snapshot_name TEXT"):
            try:
                cursor.execute(f"ALTER TABLE import_items ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass  # column already exists

        self.conn.commit()

    def add_item(self, ref: str) -> tuple[ImportItem, bool]:
        """Add an import item with status 'pending', or return the existing record.

        Returns:
            Tuple of (ImportItem, was_inserted) where was_inserted is True if newly added.
        """
        assert self.conn is not None
        cursor = self.conn.cursor()

        cursor.execute(
            """
            INSERT OR IGNORE INTO import_items (ref, status)
            VALUES (?, ?)
        """,
            (ref, ImportStatus.PENDING.value),
        )
        was_inserted = cursor.rowcount > 0
        self.conn.commit()

        cursor.execute(
            """
            SELECT * FROM import_items WHERE ref = ?
        """,
            (ref,),
        )
        row = cursor.fetchone()
        return self._row_to_item(row), was_inserted

    def get_by_status(self, status: ImportStatus) -> list[ImportItem]:
        """Get all items with the given status, ordered by id."""
        assert self.conn is not None
        cursor = self.conn.cursor()

        cursor.execute(
            """
            SELECT * FROM import_items WHERE status = ?
            ORDER BY id
        """,
            (status.value,),
        )

        return [self._row_to_item(row) for row in cursor.fetchall()]

    def update_status(
        self,
        item_id: int,
        status: ImportStatus,
        pipelinerun_name: str | None = None,
        snapshot_name: str | None = None,
        release_name: str | None = None,
        error_message: str | None = None,
        triggered_at: datetime | None = None,
        completed_at: datetime | None = None,
        retry_count: int | None = None,
    ) -> None:
        """Update the status and optional fields for an import item."""
        assert self.conn is not None
        cursor = self.conn.cursor()

        fields = ["status = ?", "last_checked_at = ?"]
        values: list = [status.value, datetime.now().isoformat()]

        if pipelinerun_name is not None:
            fields.append("pipelinerun_name = ?")
            values.append(pipelinerun_name)

        if release_name is not None:
            fields.append("release_name = ?")
            # Empty string is a sentinel meaning "clear to NULL" (e.g. on retry reset)
            values.append(None if release_name == "" else release_name)

        if snapshot_name is not None:
            fields.append("snapshot_name = ?")
            values.append(None if snapshot_name == "" else snapshot_name)

        if error_message is not None:
            fields.append("error_message = ?")
            values.append(error_message)

        if triggered_at is not None:
            fields.append("triggered_at = ?")
            values.append(triggered_at.isoformat())

        if completed_at is not None:
            fields.append("completed_at = ?")
            values.append(completed_at.isoformat())

        if retry_count is not None:
            fields.append("retry_count = ?")
            values.append(retry_count)

        values.append(item_id)

        cursor.execute(
            f"""
            UPDATE import_items
            SET {", ".join(fields)}
            WHERE id = ?
        """,  # nosec B608 - field names are hardcoded, not user input
            values,
        )

        self.conn.commit()

    def get_by_pipelinerun_name(self, name: str) -> ImportItem | None:
        """Look up an item by its PipelineRun name."""
        assert self.conn is not None
        cursor = self.conn.cursor()

        cursor.execute(
            """
            SELECT * FROM import_items WHERE pipelinerun_name = ?
        """,
            (name,),
        )

        row = cursor.fetchone()
        return self._row_to_item(row) if row else None

    def get_retry_candidates(self, max_retries: int) -> list[ImportItem]:
        """Get failed imports that are eligible for retry (retry_count < max_retries)."""
        assert self.conn is not None
        cursor = self.conn.cursor()

        cursor.execute(
            """
            SELECT * FROM import_items
            WHERE status = ? AND retry_count < ?
            ORDER BY id
        """,
            (ImportStatus.FAILED.value, max_retries),
        )

        return [self._row_to_item(row) for row in cursor.fetchall()]

    def count_in_flight(self) -> int:
        """Count entries actively being processed (total minus pending, success, and failed)."""
        assert self.conn is not None
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) FROM import_items
            WHERE status NOT IN (?, ?, ?)
        """,
            (ImportStatus.PENDING.value, ImportStatus.SUCCESS.value, ImportStatus.FAILED.value),
        )
        return cursor.fetchone()[0]

    def get_statistics(self) -> dict[str, int]:
        """Return counts grouped by status for progress reporting."""
        assert self.conn is not None
        cursor = self.conn.cursor()

        cursor.execute(
            """
            SELECT status, COUNT(*) as count
            FROM import_items
            GROUP BY status
        """
        )

        stats = {row["status"]: row["count"] for row in cursor.fetchall()}

        for status in ImportStatus:
            if status.value not in stats:
                stats[status.value] = 0

        return stats

    def _row_to_item(self, row: sqlite3.Row) -> ImportItem:
        """Convert a database row to an ImportItem dataclass."""
        return ImportItem(
            id=row["id"],
            ref=row["ref"],
            status=ImportStatus(row["status"]),
            pipelinerun_name=row["pipelinerun_name"],
            snapshot_name=row["snapshot_name"],
            release_name=row["release_name"],
            triggered_at=self._parse_timestamp(row["triggered_at"]),
            completed_at=self._parse_timestamp(row["completed_at"]),
            last_checked_at=self._parse_timestamp(row["last_checked_at"]),
            error_message=row["error_message"],
            retry_count=row["retry_count"],
        )

    def _parse_timestamp(self, value: str | None) -> datetime | None:
        """Parse an ISO-format timestamp string to a datetime, or return None."""
        if value is None:
            return None
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None
