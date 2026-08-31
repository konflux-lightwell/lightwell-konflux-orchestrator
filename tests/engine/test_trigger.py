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
from unittest.mock import MagicMock, call

import pytest

from import_orchestrator.database import ImportDatabase
from import_orchestrator.ecosystems.java.pipelinerun import TriggerError
from import_orchestrator.engine import ImportTrigger
from import_orchestrator.engine.errors import PipelineRunReconciliationError, PipelineRunRetryableError
from import_orchestrator.models import ImportItem, ImportStatus


@pytest.fixture
def db(tmp_path: Path):
    """Create a temporary database for testing."""
    db_path = tmp_path / "test.db"
    with ImportDatabase(db_path) as database:
        yield database


@pytest.fixture
def mock_kube():
    return MagicMock()


@pytest.fixture
def mock_build():
    return MagicMock(return_value={"kind": "PipelineRun"})


@pytest.fixture
def trigger(db: ImportDatabase, mock_kube: MagicMock, mock_build: MagicMock):
    """Create an ImportTrigger instance with a test database."""
    mock_kube.create_pipelinerun.return_value = "pnc-import-xxx"
    return ImportTrigger(
        db=db,
        kube=mock_kube,
        build_pipelinerun=mock_build,
        max_parallel=5,
        max_retries=3,
    )


class TestTriggerImport:
    """Test the trigger_import method."""

    def test_returns_pipelinerun_name(self, trigger: ImportTrigger, mock_kube: MagicMock, mock_build: MagicMock):
        item = ImportItem(id=1, ref="quay.io/repo:tag@sha256:abc", status=ImportStatus.PENDING)
        mock_kube.create_pipelinerun.return_value = "pnc-import-12345"

        name = trigger.trigger_import(item)

        assert name == "pnc-import-12345"
        mock_build.assert_called_once_with("quay.io/repo:tag@sha256:abc", 0)
        mock_kube.create_pipelinerun.assert_called_once_with({"kind": "PipelineRun"})

    def test_raises_when_kube_returns_none(self, trigger: ImportTrigger, mock_kube: MagicMock):
        item = ImportItem(id=1, ref="quay.io/repo:tag@sha256:abc", status=ImportStatus.PENDING)
        mock_kube.create_pipelinerun.return_value = None

        with pytest.raises(TriggerError, match="PipelineRun creation failed"):
            trigger.trigger_import(item)

    def test_raises_on_build_error(self, trigger: ImportTrigger, mock_build: MagicMock):
        item = ImportItem(id=1, ref="quay.io/repo:tag@sha256:abc", status=ImportStatus.PENDING)
        mock_build.side_effect = TriggerError("build error")

        with pytest.raises(TriggerError):
            trigger.trigger_import(item)


class TestTriggerNextBatch:
    """Test the trigger_next_batch method."""

    def test_triggers_up_to_available_slots(self, trigger: ImportTrigger):
        """Verify that imports are triggered up to the available capacity."""
        # Add 3 already in-flight imports (simulating running/triggered)
        for i in range(3):
            ref, _ = trigger.db.add_item(f"quay.io/repo:inflight{i}@sha256:bbb{i}")
            assert ref.id is not None
            trigger.db.update_status(ref.id, ImportStatus.RUNNING)

        # Add 5 pending imports
        for i in range(5):
            trigger.db.add_item(f"quay.io/repo:tag{i}@sha256:aaa{i}")

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
            ref, _ = trigger.db.add_item(f"quay.io/repo:inflight{i}@sha256:bbb{i}")
            assert ref.id is not None
            trigger.db.update_status(ref.id, ImportStatus.RUNNING)

        # Add a pending import
        trigger.db.add_item("quay.io/repo:tag@sha256:abc")

        # No slots available, should return 0 without triggering
        triggered = trigger.trigger_next_batch()
        assert triggered == 0

        # Pending import should remain pending
        pending = trigger.db.get_by_status(ImportStatus.PENDING)
        assert len(pending) == 1

    def test_handles_trigger_failure(self, trigger: ImportTrigger, mock_kube: MagicMock):
        """Verify that trigger failures are recorded in the database."""
        trigger.db.add_item("quay.io/repo:tag@sha256:abc")
        mock_kube.create_pipelinerun.side_effect = TriggerError("connection refused")

        triggered = trigger.trigger_next_batch()
        assert triggered == 0

        # Should be marked as failed
        failed = trigger.db.get_by_status(ImportStatus.FAILED)
        assert len(failed) == 1
        assert "connection refused" in failed[0].error_message

    def test_triggers_retry_candidates(self, trigger: ImportTrigger):
        """Verify that failed imports are retried within the retry limit."""
        # Add a failed import that can be retried
        ref, _ = trigger.db.add_item("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None
        trigger.db.update_status(
            ref.id,
            ImportStatus.FAILED,
            error_message="Temporary error",
            retry_count=1,
        )

        triggered = trigger.trigger_next_batch()
        assert triggered == 1

        # Should be marked as TRIGGERED with incremented retry count
        triggered_refs = trigger.db.get_by_status(ImportStatus.TRIGGERED)
        assert len(triggered_refs) == 1
        assert triggered_refs[0].retry_count == 2

    def test_retry_builds_a_new_pipeline_run_attempt(self, trigger: ImportTrigger, mock_build: MagicMock):
        """A retry after a terminal PipelineRun failure receives a new attempt number."""
        ref, _ = trigger.db.add_item("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None
        trigger.db.update_status(ref.id, ImportStatus.FAILED)

        trigger.trigger_next_batch()

        mock_build.assert_called_once_with("quay.io/repo:tag@sha256:abc", 1)

    @pytest.mark.parametrize(
        "message",
        [
            "pre-create GET failed",
            "pre-create identity mismatch",
            "POST timed out and reconciliation failed",
            "POST conflicted with a mismatched identity",
        ],
    )
    def test_reconciliation_failure_preserves_attempt_without_retry(
        self, trigger: ImportTrigger, mock_kube: MagicMock, mock_build: MagicMock, message: str
    ):
        """An unresolved current attempt is persisted without making it retryable."""
        ref, _ = trigger.db.add_item("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None
        mock_kube.create_pipelinerun.side_effect = PipelineRunReconciliationError("pnc-import-current", message)

        assert trigger.trigger_next_batch() == 0
        failed = trigger.db.get_by_status(ImportStatus.FAILED)
        assert len(failed) == 1
        assert failed[0].pipelinerun_name == "pnc-import-current"
        assert failed[0].retry_count == trigger.max_retries

        mock_build.reset_mock()
        assert trigger.trigger_next_batch() == 0
        mock_build.assert_not_called()
        assert mock_kube.create_pipelinerun.call_count == 1

    def test_clears_cached_fields_on_retry(self, trigger: ImportTrigger):
        """Verify that snapshot and release names are cleared when retrying a failed import."""
        ref, _ = trigger.db.add_item("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None
        trigger.db.update_status(
            ref.id,
            ImportStatus.FAILED,
            snapshot_name="old-snapshot",
            release_name="old-release",
            retry_count=1,
        )

        trigger.trigger_next_batch()

        # Cached fields should be cleared (database stores None for empty strings)
        triggered_refs = trigger.db.get_by_status(ImportStatus.TRIGGERED)
        assert len(triggered_refs) == 1
        assert triggered_refs[0].snapshot_name is None or triggered_refs[0].snapshot_name == ""
        assert triggered_refs[0].release_name is None or triggered_refs[0].release_name == ""

    def test_marks_failure_with_incremented_retry_count(self, trigger: ImportTrigger, mock_kube: MagicMock):
        """Verify that generic trigger failures are terminal instead of advancing attempts."""
        trigger.db.add_item("quay.io/repo:tag@sha256:abc")
        mock_kube.create_pipelinerun.side_effect = TriggerError("validation error")

        triggered = trigger.trigger_next_batch()
        assert triggered == 0

        # Generic trigger failures are not a confirmed-absence outcome.
        failed = trigger.db.get_by_status(ImportStatus.FAILED)
        assert len(failed) == 1
        assert failed[0].retry_count == trigger.max_retries
        assert "validation error" in failed[0].error_message

        trigger.trigger_next_batch()
        mock_kube.create_pipelinerun.assert_called_once()

    def test_malformed_manifest_failure_does_not_advance_attempt(
        self, trigger: ImportTrigger, mock_kube: MagicMock, mock_build: MagicMock
    ):
        """A manifest validation failure is terminal and cannot create a later attempt."""
        mock_build.return_value = {"kind": "PipelineRun"}
        mock_kube.create_pipelinerun.side_effect = TriggerError("metadata.name and import identity are required")

        trigger.db.add_item("quay.io/repo:tag@sha256:abc")
        assert trigger.trigger_next_batch() == 0
        mock_build.reset_mock()

        assert trigger.trigger_next_batch() == 0
        mock_build.assert_not_called()
        assert mock_kube.create_pipelinerun.call_count == 1

    def test_confirmed_absence_failure_allows_next_attempt(
        self, trigger: ImportTrigger, mock_kube: MagicMock, mock_build: MagicMock
    ):
        """Only confirmed absence opts the engine into a newly numbered attempt."""
        trigger.db.add_item("quay.io/repo:tag@sha256:abc")
        mock_kube.create_pipelinerun.side_effect = [
            PipelineRunRetryableError("confirmed absence"),
            "pnc-import-next",
        ]

        assert trigger.trigger_next_batch() == 0
        assert trigger.trigger_next_batch() == 1
        assert mock_build.call_args_list == [
            call("quay.io/repo:tag@sha256:abc", 0),
            call("quay.io/repo:tag@sha256:abc", 2),
        ]
