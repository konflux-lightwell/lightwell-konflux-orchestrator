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
from import_orchestrator.engine import ReleaseMonitor
from import_orchestrator.kube import KubeClient
from import_orchestrator.models import ImportStatus


@pytest.fixture
def db(tmp_path: Path):
    """Create a temporary database for testing."""
    db_path = tmp_path / "test.db"
    with ImportDatabase(db_path) as database:
        yield database


@pytest.fixture
def mock_kube():
    """Create a mock KubeClient."""
    return MagicMock(spec=KubeClient)


@pytest.fixture
def monitor(db: ImportDatabase, mock_kube: MagicMock):
    """Create a ReleaseMonitor instance with a test database and mock kube client."""
    return ReleaseMonitor(db, mock_kube, max_parallel=5)


class TestDiscoverSnapshot:
    """Test snapshot discovery phase."""

    def test_discovers_and_caches_snapshot(self, monitor: ReleaseMonitor, mock_kube: MagicMock):
        """Verify that snapshot is discovered and cached in the database."""
        ref, _ = monitor.db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        monitor.db.update_status(ref.id, ImportStatus.AWAITING_RELEASE, pipelinerun_name="pnc-import-abc")

        mock_kube.find_snapshot_by_pipelinerun.return_value = "snapshot-123"

        monitor.update_statuses()

        # Snapshot should be cached
        updated_ref = monitor.db.get_by_status(ImportStatus.AWAITING_RELEASE)[0]
        assert updated_ref.snapshot_name == "snapshot-123"
        assert updated_ref.release_name is None  # Not yet created

    def test_waits_when_snapshot_not_found(self, monitor: ReleaseMonitor, mock_kube: MagicMock):
        """Verify that monitor waits when snapshot is not yet available."""
        ref, _ = monitor.db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        monitor.db.update_status(ref.id, ImportStatus.AWAITING_RELEASE, pipelinerun_name="pnc-import-abc")

        mock_kube.find_snapshot_by_pipelinerun.return_value = None

        monitor.update_statuses()

        # Should remain in AWAITING_RELEASE with no snapshot cached
        updated_ref = monitor.db.get_by_status(ImportStatus.AWAITING_RELEASE)[0]
        assert updated_ref.snapshot_name is None


class TestFindOrCreateRelease:
    """Test release finding/creation phase."""

    def test_finds_existing_release(self, monitor: ReleaseMonitor, mock_kube: MagicMock):
        """Verify that existing release is found and cached."""
        ref, _ = monitor.db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        monitor.db.update_status(
            ref.id,
            ImportStatus.AWAITING_RELEASE,
            pipelinerun_name="pnc-import-abc",
            snapshot_name="snapshot-123",
        )

        mock_kube.find_release_for_snapshot.return_value = "release-456"
        mock_kube.get_release_status.return_value = "Unknown"  # Still running

        monitor.update_statuses()

        # Release should be cached
        updated_ref = monitor.db.get_by_status(ImportStatus.AWAITING_RELEASE)[0]
        assert updated_ref.release_name == "release-456"

    def test_creates_release_when_not_found(self, monitor: ReleaseMonitor, mock_kube: MagicMock):
        """Verify that a new release is created when none exists."""
        ref, _ = monitor.db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        monitor.db.update_status(
            ref.id,
            ImportStatus.AWAITING_RELEASE,
            pipelinerun_name="pnc-import-abc",
            snapshot_name="snapshot-123",
        )

        mock_kube.find_release_for_snapshot.return_value = None
        mock_kube.find_release_plan_for_snapshot.return_value = "release-plan-default"
        mock_kube.create_release.return_value = "release-789"
        mock_kube.get_release_status.return_value = "Unknown"

        monitor.update_statuses()

        # Release should be created and cached
        updated_ref = monitor.db.get_by_status(ImportStatus.AWAITING_RELEASE)[0]
        assert updated_ref.release_name == "release-789"
        mock_kube.create_release.assert_called_once_with("snapshot-123", "release-plan-default")

    def test_defers_when_capacity_full(self, monitor: ReleaseMonitor, mock_kube: MagicMock):
        """Verify that release creation is deferred when max_parallel is reached."""
        # Fill capacity with 5 active releases
        for i in range(5):
            ref, _ = monitor.db.add_oci_reference(f"quay.io/repo:tag{i}@sha256:abc{i}")
            assert ref.id is not None
            monitor.db.update_status(
                ref.id,
                ImportStatus.AWAITING_RELEASE,
                pipelinerun_name=f"pnc-import-{i}",
                snapshot_name=f"snapshot-{i}",
                release_name=f"release-{i}",
            )

        # Add one more that needs a release
        ref, _ = monitor.db.add_oci_reference("quay.io/repo:new@sha256:xyz")
        assert ref.id is not None
        monitor.db.update_status(
            ref.id,
            ImportStatus.AWAITING_RELEASE,
            pipelinerun_name="pnc-import-new",
            snapshot_name="snapshot-new",
        )

        mock_kube.find_release_for_snapshot.return_value = None

        monitor.update_statuses()

        # Should not create a release
        mock_kube.create_release.assert_not_called()
        updated_ref = monitor.db.get_by_status(ImportStatus.AWAITING_RELEASE)[-1]
        assert updated_ref.release_name is None

    def test_waits_when_release_plan_not_found(self, monitor: ReleaseMonitor, mock_kube: MagicMock):
        """Verify that monitor waits when ReleasePlan is not found."""
        ref, _ = monitor.db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        monitor.db.update_status(
            ref.id,
            ImportStatus.AWAITING_RELEASE,
            pipelinerun_name="pnc-import-abc",
            snapshot_name="snapshot-123",
        )

        mock_kube.find_release_for_snapshot.return_value = None
        mock_kube.find_release_plan_for_snapshot.return_value = None

        monitor.update_statuses()

        # Should not create a release
        mock_kube.create_release.assert_not_called()
        updated_ref = monitor.db.get_by_status(ImportStatus.AWAITING_RELEASE)[0]
        assert updated_ref.release_name is None

    def test_waits_when_release_creation_fails(self, monitor: ReleaseMonitor, mock_kube: MagicMock):
        """Verify that monitor waits when release creation fails."""
        ref, _ = monitor.db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        monitor.db.update_status(
            ref.id,
            ImportStatus.AWAITING_RELEASE,
            pipelinerun_name="pnc-import-abc",
            snapshot_name="snapshot-123",
        )

        mock_kube.find_release_for_snapshot.return_value = None
        mock_kube.find_release_plan_for_snapshot.return_value = "release-plan-default"
        mock_kube.create_release.return_value = None  # Creation failed

        monitor.update_statuses()

        # Should remain without a release
        updated_ref = monitor.db.get_by_status(ImportStatus.AWAITING_RELEASE)[0]
        assert updated_ref.release_name is None


class TestCheckReleaseCompletion:
    """Test release completion checking phase."""

    def test_updates_to_success_when_release_succeeds(self, monitor: ReleaseMonitor, mock_kube: MagicMock):
        """Verify that import is marked SUCCESS when release succeeds."""
        ref, _ = monitor.db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        monitor.db.update_status(
            ref.id,
            ImportStatus.AWAITING_RELEASE,
            pipelinerun_name="pnc-import-abc",
            snapshot_name="snapshot-123",
            release_name="release-456",
        )

        mock_kube.get_release_status.return_value = "True"

        monitor.update_statuses()

        # Should be marked as SUCCESS
        success = monitor.db.get_by_status(ImportStatus.SUCCESS)
        assert len(success) == 1
        assert success[0].completed_at is not None

    def test_updates_to_failed_when_release_fails(self, monitor: ReleaseMonitor, mock_kube: MagicMock):
        """Verify that import is marked FAILED when release fails."""
        ref, _ = monitor.db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        monitor.db.update_status(
            ref.id,
            ImportStatus.AWAITING_RELEASE,
            pipelinerun_name="pnc-import-abc",
            snapshot_name="snapshot-123",
            release_name="release-456",
        )

        mock_kube.get_release_status.return_value = "False"

        monitor.update_statuses()

        # Should be marked as FAILED
        failed = monitor.db.get_by_status(ImportStatus.FAILED)
        assert len(failed) == 1
        assert failed[0].error_message == "Release release-456 failed"
        assert failed[0].completed_at is not None

    def test_waits_when_release_still_running(self, monitor: ReleaseMonitor, mock_kube: MagicMock):
        """Verify that monitor waits when release is still running."""
        ref, _ = monitor.db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        monitor.db.update_status(
            ref.id,
            ImportStatus.AWAITING_RELEASE,
            pipelinerun_name="pnc-import-abc",
            snapshot_name="snapshot-123",
            release_name="release-456",
        )

        mock_kube.get_release_status.return_value = "Unknown"  # Still running

        monitor.update_statuses()

        # Should remain in AWAITING_RELEASE
        awaiting = monitor.db.get_by_status(ImportStatus.AWAITING_RELEASE)
        assert len(awaiting) == 1


class TestIntegration:
    """Test complete flow through all phases."""

    def test_complete_flow_from_awaiting_to_success(self, monitor: ReleaseMonitor, mock_kube: MagicMock):
        """Verify complete flow: discover snapshot, create release, mark success."""
        ref, _ = monitor.db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        monitor.db.update_status(ref.id, ImportStatus.AWAITING_RELEASE, pipelinerun_name="pnc-import-abc")

        # Poll 1: Discover snapshot
        mock_kube.find_snapshot_by_pipelinerun.return_value = "snapshot-123"
        monitor.update_statuses()
        assert monitor.db.get_by_status(ImportStatus.AWAITING_RELEASE)[0].snapshot_name == "snapshot-123"

        # Poll 2: Create release
        mock_kube.find_release_for_snapshot.return_value = None
        mock_kube.find_release_plan_for_snapshot.return_value = "release-plan-default"
        mock_kube.create_release.return_value = "release-456"
        mock_kube.get_release_status.return_value = "Unknown"
        monitor.update_statuses()
        assert monitor.db.get_by_status(ImportStatus.AWAITING_RELEASE)[0].release_name == "release-456"

        # Poll 3: Release completes
        mock_kube.get_release_status.return_value = "True"
        monitor.update_statuses()
        assert len(monitor.db.get_by_status(ImportStatus.SUCCESS)) == 1
