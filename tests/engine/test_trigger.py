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

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from import_orchestrator.database import ImportDatabase
from import_orchestrator.engine import ImportTrigger
from import_orchestrator.engine.pipelinerun import PipelineRunBuilder, TriggerError
from import_orchestrator.models import ImportStatus, OCIReference


@pytest.fixture
def db(tmp_path: Path):
    """Create a temporary database for testing."""
    db_path = tmp_path / "test.db"
    with ImportDatabase(db_path) as database:
        yield database


@pytest.fixture
def mock_builder():
    """Create a mock PipelineRunBuilder."""
    return MagicMock(spec=PipelineRunBuilder)


@pytest.fixture
def trigger(db: ImportDatabase, mock_builder: MagicMock):
    """Create an ImportTrigger instance with a test database."""
    return ImportTrigger(
        db=db,
        builder=mock_builder,
        max_parallel=5,
        max_retries=3,
    )


class TestTriggerImport:
    """Test the trigger_import method."""

    def test_returns_pipelinerun_name(self, trigger: ImportTrigger, mock_builder: MagicMock):
        """Verify that PipelineRun name is returned from PipelineRunBuilder."""
        oci_ref = OCIReference(id=1, oci_ref="quay.io/repo:tag@sha256:abc", status=ImportStatus.PENDING)
        mock_builder.trigger.return_value = "pnc-import-12345"

        name = trigger.trigger_import(oci_ref, tag="tag")
        assert name == "pnc-import-12345"
        mock_builder.trigger.assert_called_once_with(source_image="quay.io/repo:tag@sha256:abc", tag="tag")

    def test_returns_none_when_builder_returns_none(self, trigger: ImportTrigger, mock_builder: MagicMock):
        """Verify that None is returned when PipelineRunBuilder returns None."""
        oci_ref = OCIReference(id=1, oci_ref="quay.io/repo:tag@sha256:abc", status=ImportStatus.PENDING)
        mock_builder.trigger.return_value = None

        name = trigger.trigger_import(oci_ref, tag="tag")
        assert name is None

    def test_raises_on_trigger_error(self, trigger: ImportTrigger, mock_builder: MagicMock):
        """Verify that TriggerError is raised when PipelineRunBuilder fails."""
        oci_ref = OCIReference(id=1, oci_ref="quay.io/repo:tag@sha256:abc", status=ImportStatus.PENDING)
        mock_builder.trigger.side_effect = TriggerError("build error")

        with pytest.raises(TriggerError):
            trigger.trigger_import(oci_ref, tag="tag")


class TestTriggerNextBatch:
    """Test the trigger_next_batch method."""

    def test_triggers_up_to_available_slots(self, trigger: ImportTrigger, mock_builder: MagicMock):
        """Verify that imports are triggered up to the available capacity."""
        # Add 3 already in-flight imports (simulating running/triggered)
        for i in range(3):
            ref, _ = trigger.db.add_oci_reference(f"quay.io/repo:inflight{i}@sha256:bbb{i}")
            assert ref.id is not None
            trigger.db.update_status(ref.id, ImportStatus.RUNNING)

        # Add 5 pending imports
        for i in range(5):
            trigger.db.add_oci_reference(f"quay.io/repo:tag{i}@sha256:aaa{i}")

        mock_builder.trigger.return_value = "pnc-import-xxx"

        # 5 max - 3 in-flight = 2 slots available
        triggered = trigger.trigger_next_batch()
        assert triggered == 2

        # Verify they were marked as TRIGGERED
        triggered_refs = trigger.db.get_by_status(ImportStatus.TRIGGERED)
        assert len(triggered_refs) == 2

    def test_returns_zero_when_no_slots(self, trigger: ImportTrigger):
        """Verify that no imports are triggered when capacity is full."""
        # Fill all 5 slots with in-flight imports
        for i in range(5):
            ref, _ = trigger.db.add_oci_reference(f"quay.io/repo:inflight{i}@sha256:bbb{i}")
            assert ref.id is not None
            trigger.db.update_status(ref.id, ImportStatus.RUNNING)

        # Add a pending import
        trigger.db.add_oci_reference("quay.io/repo:tag@sha256:abc")

        # No slots available, should return 0 without triggering
        triggered = trigger.trigger_next_batch()
        assert triggered == 0

        # Pending import should remain pending
        pending = trigger.db.get_by_status(ImportStatus.PENDING)
        assert len(pending) == 1

    def test_handles_trigger_failure(self, trigger: ImportTrigger, mock_builder: MagicMock):
        """Verify that trigger failures are recorded in the database."""
        trigger.db.add_oci_reference("quay.io/repo:tag@sha256:abc")

        mock_builder.trigger.side_effect = TriggerError("connection refused")

        triggered = trigger.trigger_next_batch()
        assert triggered == 0

        # Should be marked as failed
        failed = trigger.db.get_by_status(ImportStatus.FAILED)
        assert len(failed) == 1
        assert "connection refused" in failed[0].error_message

    def test_triggers_retry_candidates(self, trigger: ImportTrigger, mock_builder: MagicMock):
        """Verify that failed imports are retried within the retry limit."""
        # Add a failed import that can be retried
        ref, _ = trigger.db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None
        trigger.db.update_status(
            ref.id,
            ImportStatus.FAILED,
            error_message="Temporary error",
            retry_count=1,
        )

        mock_builder.trigger.return_value = "pnc-import-retry"

        triggered = trigger.trigger_next_batch()
        assert triggered == 1

        # Should be marked as TRIGGERED with incremented retry count
        triggered_refs = trigger.db.get_by_status(ImportStatus.TRIGGERED)
        assert len(triggered_refs) == 1
        assert triggered_refs[0].retry_count == 2

    def test_clears_cached_fields_on_retry(self, trigger: ImportTrigger, mock_builder: MagicMock):
        """Verify that snapshot and release names are cleared when retrying a failed import."""
        # Add a failed import with cached fields from a previous attempt
        ref, _ = trigger.db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None
        trigger.db.update_status(
            ref.id,
            ImportStatus.FAILED,
            snapshot_name="old-snapshot",
            release_name="old-release",
            retry_count=1,
        )

        mock_builder.trigger.return_value = "pnc-import-retry"

        trigger.trigger_next_batch()

        # Cached fields should be cleared (database stores None for empty strings)
        triggered_refs = trigger.db.get_by_status(ImportStatus.TRIGGERED)
        assert len(triggered_refs) == 1
        # The database update with "" clears to None
        assert triggered_refs[0].snapshot_name is None or triggered_refs[0].snapshot_name == ""
        assert triggered_refs[0].release_name is None or triggered_refs[0].release_name == ""

    def test_marks_failure_with_incremented_retry_count(self, trigger: ImportTrigger, mock_builder: MagicMock):
        """Verify that TriggerError failures increment retry count."""
        trigger.db.add_oci_reference("quay.io/repo:tag@sha256:abc")

        mock_builder.trigger.side_effect = TriggerError("validation error")

        triggered = trigger.trigger_next_batch()
        assert triggered == 0

        # Should be marked as failed with retry_count = 1
        failed = trigger.db.get_by_status(ImportStatus.FAILED)
        assert len(failed) == 1
        assert failed[0].retry_count == 1
        assert "validation error" in failed[0].error_message
