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
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from import_orchestrator.models import (
    Attempt,
    AttemptPhase,
    ClusterResource,
    Phase,
    PhaseStatus,
    Request,
)


class WorkflowDatabase:
    """SQLite store for the Request / Attempt / AttemptPhase model (ADR-0001).

    Persists the intent to release an artifact (``Request``), each execution of
    the workflow (``Attempt``), and the per-phase state of each execution
    (``AttemptPhase``). This class only reads and writes rows. Which phases carry
    forward on a retry, how a phase write is validated, and how overall state is
    derived live on the model types in :mod:`import_orchestrator.models`.

    Used as a context manager to ensure the connection is properly opened and
    closed::

        with WorkflowDatabase(Path("state.db")) as store:
            request, _ = store.get_or_create_request(
                namespace="ns", application="app", component="comp",
                pipeline="import-pnc", release_plan="rp",
                artifact="quay.io/repo@sha256:abc", target="remediated",
            )
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> WorkflowDatabase:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
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
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                namespace TEXT NOT NULL,
                application TEXT NOT NULL,
                component TEXT NOT NULL,
                pipeline TEXT NOT NULL,
                release_plan TEXT NOT NULL,
                artifact TEXT NOT NULL,
                target TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                UNIQUE(namespace, application, component, pipeline, release_plan, target, artifact)
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL REFERENCES requests(id),
                created_at TIMESTAMP NOT NULL
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS attempt_phases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                attempt_id INTEGER NOT NULL REFERENCES attempts(id),
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                reference_type TEXT,
                reference_id TEXT,
                error_message TEXT,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                UNIQUE(attempt_id, name)
            )
        """
        )

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_attempts_request ON attempts(request_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_phases_attempt ON attempt_phases(attempt_id)")

        self.conn.commit()

    # -- Requests ----------------------------------------------------------

    def get_or_create_request(
        self,
        namespace: str,
        application: str,
        component: str,
        pipeline: str,
        release_plan: str,
        artifact: str,
        target: str,
    ) -> tuple[Request, bool]:
        """Create a Request for an artifact/target, or return the existing one.

        The seven identifying fields form the request fingerprint (ADR-0001);
        re-importing the same artifact for the same target is a no-op.

        Returns:
            Tuple of (Request, was_inserted) where was_inserted is True if newly
            created.
        """
        assert self.conn is not None
        cursor = self.conn.cursor()

        now = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            """
            INSERT OR IGNORE INTO requests
                (namespace, application, component, pipeline, release_plan, artifact, target, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (namespace, application, component, pipeline, release_plan, artifact, target, now),
        )
        was_inserted = cursor.rowcount > 0
        self.conn.commit()

        cursor.execute(
            """
            SELECT * FROM requests
            WHERE namespace = ? AND application = ? AND component = ? AND pipeline = ?
              AND release_plan = ? AND target = ? AND artifact = ?
        """,
            (namespace, application, component, pipeline, release_plan, target, artifact),
        )
        return self._row_to_request(cursor.fetchone()), was_inserted

    def get_request(self, request_id: int) -> Request | None:
        """Look up a Request by id."""
        assert self.conn is not None
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM requests WHERE id = ?", (request_id,))
        row = cursor.fetchone()
        return self._row_to_request(row) if row else None

    def list_requests(self) -> list[Request]:
        """Return every Request, ordered by id."""
        assert self.conn is not None
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM requests ORDER BY id")
        return [self._row_to_request(row) for row in cursor.fetchall()]

    # -- Attempts ----------------------------------------------------------

    def create_attempt(
        self,
        request_id: int,
        initial_phases: Sequence[AttemptPhase] = (),
    ) -> Attempt:
        """Start a new Attempt for a Request, seeding it with ``initial_phases``.

        Each retry of the workflow is a fresh Attempt; prior attempts are left
        untouched so the history is preserved. ``initial_phases`` are written
        verbatim under the new Attempt in the same transaction -- their status,
        reference and original ``created_at``/``updated_at`` are preserved, so a
        retry carrying forward settled phases cannot be left half-written. Deciding
        *which* phases to carry is the caller's job; this store only persists them.
        """
        assert self.conn is not None
        cursor = self.conn.cursor()
        now = datetime.now(timezone.utc)
        try:
            cursor.execute(
                "INSERT INTO attempts (request_id, created_at) VALUES (?, ?)",
                (request_id, now.isoformat()),
            )
            attempt_id = cursor.lastrowid
            assert attempt_id is not None
            for phase in initial_phases:
                created = (phase.created_at or now).isoformat()
                updated = (phase.updated_at or now).isoformat()
                cursor.execute(
                    """
                    INSERT INTO attempt_phases
                        (attempt_id, name, status, reference_type, reference_id, error_message,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        attempt_id,
                        phase.name.value,
                        phase.status.value,
                        phase.reference_type.value if phase.reference_type else None,
                        phase.reference_id,
                        phase.error_message,
                        created,
                        updated,
                    ),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self.get_attempt(attempt_id)  # type: ignore[return-value]

    def get_attempt(self, attempt_id: int) -> Attempt | None:
        """Look up an Attempt by id."""
        assert self.conn is not None
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,))
        row = cursor.fetchone()
        return self._row_to_attempt(row) if row else None

    def list_attempts(self, request_id: int) -> list[Attempt]:
        """Return all Attempts for a Request, oldest first."""
        assert self.conn is not None
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM attempts WHERE request_id = ? ORDER BY id", (request_id,))
        return [self._row_to_attempt(row) for row in cursor.fetchall()]

    def latest_attempt(self, request_id: int) -> Attempt | None:
        """Return the most recent Attempt for a Request, or None if there are none."""
        assert self.conn is not None
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM attempts WHERE request_id = ? ORDER BY id DESC LIMIT 1",
            (request_id,),
        )
        row = cursor.fetchone()
        return self._row_to_attempt(row) if row else None

    # -- Attempt phases ----------------------------------------------------

    def upsert_phase(
        self,
        attempt_id: int,
        name: Phase,
        status: PhaseStatus,
        reference_type: ClusterResource | None = None,
        reference_id: str | None = None,
        error_message: str | None = None,
    ) -> AttemptPhase:
        """Create or advance the given phase of an Attempt.

        A missing phase is inserted. An existing one has its status updated and
        ``updated_at`` bumped. ``reference_type`` and ``reference_id`` must be
        supplied together and must match the phase. ``error_message`` is stored
        only while the phase is FAILED. Omitting the reference leaves the one
        already recorded on this attempt in place.

        Raises:
            ValueError: if only one of reference_type/reference_id is supplied,
                or reference_type does not match the phase.
        """
        assert self.conn is not None
        # AttemptPhase enforces the phase-model invariants on construction, so the
        # store persists a ready-made, valid object rather than applying any rules.
        incoming = AttemptPhase(
            id=None,
            attempt_id=attempt_id,
            name=name,
            status=status,
            reference_type=reference_type,
            reference_id=reference_id,
            error_message=error_message,
        )
        existing = self.get_phase(attempt_id, name)
        if existing is None:
            return self._insert_phase(incoming)
        return self._update_phase(existing, incoming)

    def _insert_phase(self, phase: AttemptPhase) -> AttemptPhase:
        assert self.conn is not None
        cursor = self.conn.cursor()
        now = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            """
            INSERT INTO attempt_phases
                (attempt_id, name, status, reference_type, reference_id, error_message,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                phase.attempt_id,
                phase.name.value,
                phase.status.value,
                phase.reference_type.value if phase.reference_type else None,
                phase.reference_id,
                phase.error_message,
                now,
                now,
            ),
        )
        self.conn.commit()
        phase_id = cursor.lastrowid
        assert phase_id is not None
        return self._get_phase_by_id(phase_id)  # type: ignore[return-value]

    def _update_phase(self, existing: AttemptPhase, incoming: AttemptPhase) -> AttemptPhase:
        assert self.conn is not None
        assert existing.id is not None
        cursor = self.conn.cursor()

        fields = ["status = ?", "updated_at = ?"]
        values: list = [incoming.status.value, datetime.now(timezone.utc).isoformat()]

        # The (reference_type, reference_id) pair is a single unit; when supplied,
        # write both. When omitted, the existing reference is preserved.
        if incoming.reference_type is not None:
            fields.append("reference_type = ?")
            values.append(incoming.reference_type.value)
            fields.append("reference_id = ?")
            values.append(incoming.reference_id)

        # error_message already tracks the status on ``incoming`` (None unless
        # FAILED). Clear it whenever not FAILED; when FAILED, only overwrite with a
        # freshly supplied message so an earlier failure detail is not lost.
        if incoming.status != PhaseStatus.FAILED:
            fields.append("error_message = ?")
            values.append(None)
        elif incoming.error_message is not None:
            fields.append("error_message = ?")
            values.append(incoming.error_message)

        values.append(existing.id)
        cursor.execute(
            f"""
            UPDATE attempt_phases
            SET {", ".join(fields)}
            WHERE id = ?
        """,  # nosec B608 - field names are hardcoded, not user input
            values,
        )
        self.conn.commit()
        return self._get_phase_by_id(existing.id)  # type: ignore[return-value]

    def get_phase(self, attempt_id: int, name: Phase) -> AttemptPhase | None:
        """Look up a single phase of an Attempt by name."""
        assert self.conn is not None
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM attempt_phases WHERE attempt_id = ? AND name = ?",
            (attempt_id, name.value),
        )
        row = cursor.fetchone()
        return self._row_to_phase(row) if row else None

    def list_phases(self, attempt_id: int) -> list[AttemptPhase]:
        """Return all phases recorded for an Attempt, ordered by id."""
        assert self.conn is not None
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM attempt_phases WHERE attempt_id = ? ORDER BY id", (attempt_id,))
        return [self._row_to_phase(row) for row in cursor.fetchall()]

    # -- Row mappers -------------------------------------------------------

    def _get_phase_by_id(self, phase_id: int) -> AttemptPhase | None:
        assert self.conn is not None
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM attempt_phases WHERE id = ?", (phase_id,))
        row = cursor.fetchone()
        return self._row_to_phase(row) if row else None

    def _row_to_request(self, row: sqlite3.Row) -> Request:
        return Request(
            id=row["id"],
            namespace=row["namespace"],
            application=row["application"],
            component=row["component"],
            pipeline=row["pipeline"],
            release_plan=row["release_plan"],
            artifact=row["artifact"],
            target=row["target"],
            created_at=self._parse_timestamp(row["created_at"]),
        )

    def _row_to_attempt(self, row: sqlite3.Row) -> Attempt:
        return Attempt(
            id=row["id"],
            request_id=row["request_id"],
            created_at=self._parse_timestamp(row["created_at"]),
        )

    def _row_to_phase(self, row: sqlite3.Row) -> AttemptPhase:
        reference_type = row["reference_type"]
        return AttemptPhase(
            id=row["id"],
            attempt_id=row["attempt_id"],
            name=Phase(row["name"]),
            status=PhaseStatus(row["status"]),
            reference_type=ClusterResource(reference_type) if reference_type else None,
            reference_id=row["reference_id"],
            error_message=row["error_message"],
            created_at=self._parse_timestamp(row["created_at"]),
            updated_at=self._parse_timestamp(row["updated_at"]),
        )

    def _parse_timestamp(self, value: str | None) -> datetime | None:
        """Parse an ISO-format timestamp string to a datetime, or return None."""
        if value is None:
            return None
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None
