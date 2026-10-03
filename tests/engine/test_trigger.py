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
from import_orchestrator.ecosystems.java.pipelinerun import TriggerError
from import_orchestrator.engine import ImportTrigger
from import_orchestrator.models import ImportItem, ImportStatus, SnapshotLookup, SnapshotLookupState


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
        mock_build.assert_called_once_with("quay.io/repo:tag@sha256:abc")
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

    def test_reuses_pending_snapshot_without_triggering_import(self, db: ImportDatabase, mock_kube: MagicMock):
        """An exact digest match enters release reconciliation without a new PipelineRun."""
        item, _ = db.add_item("quay.io/repo:tag@sha256:" + "a" * 64)
        mock_kube.find_snapshot_by_component_digest.return_value = "snapshot-existing"
        trigger = ImportTrigger(db, mock_kube, MagicMock(), max_parallel=1, max_retries=3)

        assert trigger.trigger_next_batch() == 0
        assert db.get_by_status(ImportStatus.AWAITING_RELEASE)[0].snapshot_name == "snapshot-existing"
        mock_kube.create_pipelinerun.assert_not_called()

    def test_unknown_legacy_snapshot_result_stays_pending(self, trigger: ImportTrigger, mock_kube: MagicMock):
        """An uncontracted legacy miss is UNKNOWN and must fail closed."""
        mock_kube.find_snapshot_by_component_digest.return_value = None
        trigger.db.add_item("quay.io/repo:tag@sha256:" + "b" * 64)

        assert trigger.trigger_next_batch() == 0
        pending = trigger.db.get_by_status(ImportStatus.PENDING)
        assert len(pending) == 1
        assert "unavailable" in pending[0].error_message
        mock_kube.create_pipelinerun.assert_not_called()

    @pytest.mark.parametrize("state", [SnapshotLookupState.AMBIGUOUS, SnapshotLookupState.UNKNOWN])
    def test_typed_uncertain_snapshot_result_stays_pending(self, trigger: ImportTrigger, mock_kube: MagicMock, state):
        trigger.db.add_item("quay.io/repo:tag@sha256:" + "d" * 64)
        mock_kube.find_snapshot_by_component_digest.return_value = SnapshotLookup(state)

        assert trigger.trigger_next_batch() == 0
        assert len(trigger.db.get_by_status(ImportStatus.PENDING)) == 1
        mock_kube.create_pipelinerun.assert_not_called()

    @pytest.mark.parametrize(
        "lookup",
        [
            SnapshotLookup(SnapshotLookupState.CONFIRMED_EMPTY),
            SnapshotLookup(SnapshotLookupState.AMBIGUOUS),
            SnapshotLookup(SnapshotLookupState.UNKNOWN),
        ],
    )
    def test_java_source_lookup_uses_fresh_import_only_for_confirmed_source_miss(
        self, db: ImportDatabase, mock_kube: MagicMock, lookup: SnapshotLookup
    ):
        db.add_item("quay.io/repo:tag@sha256:" + "a" * 64)
        mock_kube.find_snapshot_for_import.return_value = lookup
        mock_kube.find_snapshot_by_component_digest.return_value = SnapshotLookup(
            SnapshotLookupState.FOUND, "shared-component-snapshot"
        )
        trigger = ImportTrigger(
            db,
            mock_kube,
            MagicMock(),
            max_parallel=1,
            max_retries=3,
            expected_application="app",
            import_snapshot_resolver=True,
        )

        mock_kube.create_pipelinerun.return_value = "fresh-pr"
        expected = 1 if lookup.state is SnapshotLookupState.CONFIRMED_EMPTY else 0
        assert trigger.trigger_next_batch() == expected
        assert len(db.get_by_status(ImportStatus.TRIGGERED if expected else ImportStatus.PENDING)) == 1
        mock_kube.find_snapshot_by_component_digest.assert_not_called()
        if expected:
            mock_kube.create_pipelinerun.assert_called_once()
        else:
            mock_kube.create_pipelinerun.assert_not_called()

    def test_java_source_match_reuses_snapshot_without_import(self, db: ImportDatabase, mock_kube: MagicMock):
        item, _ = db.add_item("quay.io/repo:tag@sha256:" + "a" * 64)
        mock_kube.find_snapshot_for_import.return_value = SnapshotLookup(SnapshotLookupState.FOUND, "source-snapshot")
        trigger = ImportTrigger(
            db,
            mock_kube,
            MagicMock(),
            max_parallel=1,
            max_retries=3,
            expected_application="app",
            import_snapshot_resolver=True,
        )

        assert trigger.trigger_next_batch() == 0
        assert db.get_by_status(ImportStatus.AWAITING_RELEASE)[0].id == item.id
        assert db.get_by_status(ImportStatus.AWAITING_RELEASE)[0].snapshot_name == "source-snapshot"
        mock_kube.find_snapshot_by_component_digest.assert_not_called()
        mock_kube.create_pipelinerun.assert_not_called()

    def test_force_import_ignores_matching_snapshot(self, db: ImportDatabase, mock_kube: MagicMock):
        """Force import bypasses Snapshot reuse and submits a PipelineRun."""
        db.add_item("quay.io/repo:tag@sha256:" + "c" * 64)
        mock_kube.find_snapshot_by_component_digest.return_value = "snapshot-existing"
        mock_kube.create_pipelinerun.return_value = "import-forced"
        trigger = ImportTrigger(db, mock_kube, MagicMock(), max_parallel=1, max_retries=3, force_import=True)

        assert trigger.trigger_next_batch() == 1
        mock_kube.find_snapshot_by_component_digest.assert_not_called()
        mock_kube.create_pipelinerun.assert_called_once()

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
        """Verify that TriggerError failures increment retry count."""
        trigger.db.add_item("quay.io/repo:tag@sha256:abc")
        mock_kube.create_pipelinerun.side_effect = TriggerError("validation error")

        triggered = trigger.trigger_next_batch()
        assert triggered == 0

        # Should be marked as failed with retry_count = 1
        failed = trigger.db.get_by_status(ImportStatus.FAILED)
        assert len(failed) == 1
        assert failed[0].retry_count == 1
        assert "validation error" in failed[0].error_message
