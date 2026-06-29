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

from datetime import datetime
from pathlib import Path

import pytest

from import_orchestrator.database import ImportDatabase
from import_orchestrator.models import ImportStatus


@pytest.fixture
def db(tmp_path: Path):
    """Provide a fresh in-memory-like database for each test."""
    db_path = tmp_path / "test.db"
    with ImportDatabase(db_path) as database:
        yield database


class TestImportDatabase:
    def test_add_oci_reference_inserts_new(self, db: ImportDatabase):
        ref, was_inserted = db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert was_inserted is True
        assert ref.oci_ref == "quay.io/repo:tag@sha256:abc"
        assert ref.status == ImportStatus.PENDING
        assert ref.id is not None

    def test_add_oci_reference_duplicate_returns_existing(self, db: ImportDatabase):
        ref1, inserted1 = db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        ref2, inserted2 = db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert inserted1 is True
        assert inserted2 is False
        assert ref1.id == ref2.id

    def test_get_by_status(self, db: ImportDatabase):
        db.add_oci_reference("quay.io/repo:tag1@sha256:aaa")
        db.add_oci_reference("quay.io/repo:tag2@sha256:bbb")

        pending = db.get_by_status(ImportStatus.PENDING)
        assert len(pending) == 2

        running = db.get_by_status(ImportStatus.RUNNING)
        assert len(running) == 0

    def test_update_status(self, db: ImportDatabase):
        ref, _ = db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        db.update_status(ref.id, ImportStatus.TRIGGERED, pipelinerun_name="pnc-import-xyz")

        triggered = db.get_by_status(ImportStatus.TRIGGERED)
        assert len(triggered) == 1
        assert triggered[0].pipelinerun_name == "pnc-import-xyz"

    def test_update_status_with_timestamps(self, db: ImportDatabase):
        ref, _ = db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        now = datetime.now()
        db.update_status(
            ref.id,
            ImportStatus.SUCCESS,
            completed_at=now,
            triggered_at=now,
        )

        success = db.get_by_status(ImportStatus.SUCCESS)
        assert len(success) == 1
        assert success[0].completed_at is not None
        assert success[0].triggered_at is not None

    def test_update_status_with_error_and_retry(self, db: ImportDatabase):
        ref, _ = db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        db.update_status(
            ref.id,
            ImportStatus.FAILED,
            error_message="Connection timeout",
            retry_count=1,
        )

        failed = db.get_by_status(ImportStatus.FAILED)
        assert len(failed) == 1
        assert failed[0].error_message == "Connection timeout"
        assert failed[0].retry_count == 1

    def test_get_by_pipelinerun_name(self, db: ImportDatabase):
        ref, _ = db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        db.update_status(ref.id, ImportStatus.TRIGGERED, pipelinerun_name="pnc-import-xyz")

        found = db.get_by_pipelinerun_name("pnc-import-xyz")
        assert found is not None
        assert found.oci_ref == "quay.io/repo:tag@sha256:abc"

    def test_get_by_pipelinerun_name_not_found(self, db: ImportDatabase):
        assert db.get_by_pipelinerun_name("nonexistent") is None

    def test_get_retry_candidates(self, db: ImportDatabase):
        ref1, _ = db.add_oci_reference("quay.io/repo:tag1@sha256:aaa")
        ref2, _ = db.add_oci_reference("quay.io/repo:tag2@sha256:bbb")
        ref3, _ = db.add_oci_reference("quay.io/repo:tag3@sha256:ccc")
        assert ref1.id is not None and ref2.id is not None and ref3.id is not None

        # ref1: failed with 1 retry (eligible when max_retries=3)
        db.update_status(ref1.id, ImportStatus.FAILED, retry_count=1)
        # ref2: failed with 3 retries (not eligible when max_retries=3)
        db.update_status(ref2.id, ImportStatus.FAILED, retry_count=3)
        # ref3: still pending (not a retry candidate)

        candidates = db.get_retry_candidates(max_retries=3)
        assert len(candidates) == 1
        assert candidates[0].oci_ref == "quay.io/repo:tag1@sha256:aaa"

    def test_get_statistics(self, db: ImportDatabase):
        db.add_oci_reference("quay.io/repo:tag1@sha256:aaa")
        db.add_oci_reference("quay.io/repo:tag2@sha256:bbb")

        ref3, _ = db.add_oci_reference("quay.io/repo:tag3@sha256:ccc")
        assert ref3.id is not None
        db.update_status(ref3.id, ImportStatus.SUCCESS)

        stats = db.get_statistics()
        assert stats["pending"] == 2
        assert stats["success"] == 1
        assert stats["triggered"] == 0
        assert stats["running"] == 0
        assert stats["failed"] == 0

    def test_get_statistics_empty_database(self, db: ImportDatabase):
        stats = db.get_statistics()
        for status in ImportStatus:
            assert stats[status.value] == 0

    def test_creates_parent_directories(self, tmp_path: Path):
        nested_path = tmp_path / "deep" / "nested" / "dir" / "test.db"
        with ImportDatabase(nested_path) as db:
            ref, was_inserted = db.add_oci_reference("quay.io/repo:tag@sha256:abc")
            assert was_inserted is True

    def test_parse_timestamp_handles_none(self, db: ImportDatabase):
        result = db._parse_timestamp(None)
        assert result is None

    def test_parse_timestamp_handles_valid_iso(self, db: ImportDatabase):
        result = db._parse_timestamp("2026-01-15T10:30:00")
        assert result is not None
        assert result.year == 2026

    def test_parse_timestamp_handles_invalid_string(self, db: ImportDatabase):
        result = db._parse_timestamp("not-a-date")
        assert result is None
