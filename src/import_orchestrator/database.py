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

from import_orchestrator.models import ImportStatus, OCIReference


class ImportDatabase:
    """SQLite database for tracking import state.

    Used as a context manager to ensure the connection is properly opened and closed::

        with ImportDatabase(Path("state.db")) as db:
            db.add_oci_reference("quay.io/repo:tag@sha256:abc")
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
            CREATE TABLE IF NOT EXISTS oci_references (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                oci_ref TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL CHECK(status IN ('pending', 'triggered', 'running', 'success', 'failed')),
                pipelinerun_name TEXT,
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
            CREATE INDEX IF NOT EXISTS idx_status ON oci_references(status)
        """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_pipelinerun_name ON oci_references(pipelinerun_name)
        """
        )

        self.conn.commit()

    def add_oci_reference(self, oci_ref: str) -> tuple[OCIReference, bool]:
        """Add an OCI reference with status 'pending', or return the existing record.

        Returns:
            Tuple of (OCIReference, was_inserted) where was_inserted is True if newly added.
        """
        assert self.conn is not None
        cursor = self.conn.cursor()

        cursor.execute(
            """
            INSERT OR IGNORE INTO oci_references (oci_ref, status)
            VALUES (?, ?)
        """,
            (oci_ref, ImportStatus.PENDING.value),
        )
        was_inserted = cursor.rowcount > 0
        self.conn.commit()

        cursor.execute(
            """
            SELECT * FROM oci_references WHERE oci_ref = ?
        """,
            (oci_ref,),
        )
        row = cursor.fetchone()
        return self._row_to_oci_reference(row), was_inserted

    def get_by_status(self, status: ImportStatus) -> list[OCIReference]:
        """Get all references with the given status, ordered by id."""
        assert self.conn is not None
        cursor = self.conn.cursor()

        cursor.execute(
            """
            SELECT * FROM oci_references WHERE status = ?
            ORDER BY id
        """,
            (status.value,),
        )

        return [self._row_to_oci_reference(row) for row in cursor.fetchall()]

    def update_status(
        self,
        oci_ref_id: int,
        status: ImportStatus,
        pipelinerun_name: str | None = None,
        error_message: str | None = None,
        triggered_at: datetime | None = None,
        completed_at: datetime | None = None,
        retry_count: int | None = None,
    ) -> None:
        """Update the status and optional fields for an OCI reference."""
        assert self.conn is not None
        cursor = self.conn.cursor()

        fields = ["status = ?", "last_checked_at = ?"]
        values: list = [status.value, datetime.now().isoformat()]

        if pipelinerun_name is not None:
            fields.append("pipelinerun_name = ?")
            values.append(pipelinerun_name)

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

        values.append(oci_ref_id)

        cursor.execute(
            f"""
            UPDATE oci_references
            SET {', '.join(fields)}
            WHERE id = ?
        """,
            values,
        )

        self.conn.commit()

    def get_by_pipelinerun_name(self, name: str) -> OCIReference | None:
        """Look up a reference by its PipelineRun name."""
        assert self.conn is not None
        cursor = self.conn.cursor()

        cursor.execute(
            """
            SELECT * FROM oci_references WHERE pipelinerun_name = ?
        """,
            (name,),
        )

        row = cursor.fetchone()
        return self._row_to_oci_reference(row) if row else None

    def get_retry_candidates(self, max_retries: int) -> list[OCIReference]:
        """Get failed imports that are eligible for retry (retry_count < max_retries)."""
        assert self.conn is not None
        cursor = self.conn.cursor()

        cursor.execute(
            """
            SELECT * FROM oci_references
            WHERE status = ? AND retry_count < ?
            ORDER BY id
        """,
            (ImportStatus.FAILED.value, max_retries),
        )

        return [self._row_to_oci_reference(row) for row in cursor.fetchall()]

    def get_statistics(self) -> dict[str, int]:
        """Return counts grouped by status for progress reporting."""
        assert self.conn is not None
        cursor = self.conn.cursor()

        cursor.execute(
            """
            SELECT status, COUNT(*) as count
            FROM oci_references
            GROUP BY status
        """
        )

        stats = {row["status"]: row["count"] for row in cursor.fetchall()}

        for status in ImportStatus:
            if status.value not in stats:
                stats[status.value] = 0

        return stats

    def _row_to_oci_reference(self, row: sqlite3.Row) -> OCIReference:
        """Convert a database row to an OCIReference dataclass."""
        return OCIReference(
            id=row["id"],
            oci_ref=row["oci_ref"],
            status=ImportStatus(row["status"]),
            pipelinerun_name=row["pipelinerun_name"],
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
