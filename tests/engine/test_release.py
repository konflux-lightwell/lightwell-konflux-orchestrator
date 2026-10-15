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

from import_orchestrator.clients import KubeClient
from import_orchestrator.database import WorkflowDatabase
from import_orchestrator.engine import ReleaseMonitor, RequestView
from import_orchestrator.models import ClusterResource, Phase, PhaseStatus, Request, RequestState


def _identity(ref: str) -> dict[str, str]:
    return {
        "namespace": "ns",
        "application": "app",
        "component": "comp",
        "pipeline": "import-pnc",
        "release_plan": "rp",
        "target": "remediated",
        "artifact": ref,
    }


def _seed(
    db: WorkflowDatabase,
    ref: str,
    *,
    snapshot: str | None = None,
    release: str | None = None,
) -> Request:
    """Seed a Request whose latest attempt has a successful intake, and optionally
    a discovered snapshot and/or a tracked (RUNNING) release."""
    request, _ = db.get_or_create_request(**_identity(ref))
    attempt = db.create_attempt(request.id)
    db.upsert_phase(
        attempt.id,
        Phase.INTAKE,
        PhaseStatus.SUCCESS,
        reference_type=ClusterResource.PIPELINE_RUN,
        reference_id=f"pr-{ref}",
    )
    if snapshot is not None:
        db.upsert_phase(
            attempt.id,
            Phase.SNAPSHOT,
            PhaseStatus.SUCCESS,
            reference_type=ClusterResource.SNAPSHOT,
            reference_id=snapshot,
        )
    if release is not None:
        db.upsert_phase(
            attempt.id,
            Phase.RELEASE,
            PhaseStatus.RUNNING,
            reference_type=ClusterResource.RELEASE,
            reference_id=release,
        )
    return request


def _phase(db: WorkflowDatabase, request: Request, name: Phase):
    return db.latest_phase_map(request.id).get(name)


@pytest.fixture
def db(tmp_path: Path):
    db_path = tmp_path / "test.db"
    with WorkflowDatabase(db_path) as database:
        yield database


@pytest.fixture
def mock_kube():
    return MagicMock(spec=KubeClient)


@pytest.fixture
def monitor(db: WorkflowDatabase, mock_kube: MagicMock):
    return ReleaseMonitor(RequestView(db, skip_release=False), mock_kube, max_parallel=5, prefix="pnc-import-")


class TestDiscoverSnapshot:
    """Test snapshot discovery phase."""

    def test_discovers_and_records_snapshot(self, monitor: ReleaseMonitor, db: WorkflowDatabase, mock_kube: MagicMock):
        """Verify that the snapshot is discovered and recorded on the snapshot phase."""
        request = _seed(db, "r1")
        mock_kube.find_snapshot_by_pipelinerun.return_value = "snapshot-123"

        monitor.update_statuses()

        snapshot = _phase(db, request, Phase.SNAPSHOT)
        assert snapshot.status == PhaseStatus.SUCCESS
        assert snapshot.reference_id == "snapshot-123"
        assert _phase(db, request, Phase.RELEASE) is None  # not yet created

    def test_waits_when_snapshot_not_found(self, monitor: ReleaseMonitor, db: WorkflowDatabase, mock_kube: MagicMock):
        """Verify that monitor waits when snapshot is not yet available."""
        request = _seed(db, "r1")
        mock_kube.find_snapshot_by_pipelinerun.return_value = None

        monitor.update_statuses()

        assert _phase(db, request, Phase.SNAPSHOT) is None


class TestFindOrCreateRelease:
    """Test release finding/creation phase."""

    def test_records_integration_tests_as_skipped(
        self, monitor: ReleaseMonitor, db: WorkflowDatabase, mock_kube: MagicMock
    ):
        """Once the snapshot is known, the integration-tests phase is represented as SKIPPED."""
        request = _seed(db, "r1", snapshot="snapshot-123")
        mock_kube.find_release_for_snapshot.return_value = "release-456"

        monitor.update_statuses()

        it = _phase(db, request, Phase.INTEGRATION_TESTS)
        assert it is not None
        assert it.status == PhaseStatus.SKIPPED

    def test_finds_existing_release(self, monitor: ReleaseMonitor, db: WorkflowDatabase, mock_kube: MagicMock):
        """Verify that an existing release is adopted and tracked."""
        request = _seed(db, "r1", snapshot="snapshot-123")
        mock_kube.find_release_for_snapshot.return_value = "release-456"

        monitor.update_statuses()

        release = _phase(db, request, Phase.RELEASE)
        assert release.status == PhaseStatus.RUNNING
        assert release.reference_id == "release-456"

    def test_creates_release_when_not_found(self, monitor: ReleaseMonitor, db: WorkflowDatabase, mock_kube: MagicMock):
        """Verify that a new release is created when none exists."""
        request = _seed(db, "r1", snapshot="snapshot-123")
        mock_kube.find_release_for_snapshot.return_value = None
        mock_kube.find_release_plan_for_snapshot.return_value = "release-plan-default"
        mock_kube.create_release.return_value = "release-789"

        monitor.update_statuses()

        release = _phase(db, request, Phase.RELEASE)
        assert release.reference_id == "release-789"
        mock_kube.create_release.assert_called_once_with("snapshot-123", "release-plan-default", "pnc-import-")

    def test_defers_when_capacity_full(self, db: WorkflowDatabase, mock_kube: MagicMock):
        """Verify that release creation is deferred when max_parallel is reached."""
        monitor = ReleaseMonitor(RequestView(db, skip_release=False), mock_kube, max_parallel=5, prefix="pnc-import-")
        # 5 active releases already tracked.
        for i in range(5):
            _seed(db, f"active{i}", snapshot=f"snapshot-{i}", release=f"release-{i}")
        # One more needs a release.
        new_request = _seed(db, "new", snapshot="snapshot-new")

        mock_kube.find_release_for_snapshot.return_value = None
        mock_kube.get_release_status.return_value = "Unknown"

        monitor.update_statuses()

        mock_kube.create_release.assert_not_called()
        assert _phase(db, new_request, Phase.RELEASE) is None

    def test_waits_when_release_plan_not_found(
        self, monitor: ReleaseMonitor, db: WorkflowDatabase, mock_kube: MagicMock
    ):
        """Verify that monitor waits when ReleasePlan is not found."""
        request = _seed(db, "r1", snapshot="snapshot-123")
        mock_kube.find_release_for_snapshot.return_value = None
        mock_kube.find_release_plan_for_snapshot.return_value = None

        monitor.update_statuses()

        mock_kube.create_release.assert_not_called()
        assert _phase(db, request, Phase.RELEASE) is None

    def test_waits_when_release_creation_fails(
        self, monitor: ReleaseMonitor, db: WorkflowDatabase, mock_kube: MagicMock
    ):
        """Verify that monitor waits when release creation fails."""
        request = _seed(db, "r1", snapshot="snapshot-123")
        mock_kube.find_release_for_snapshot.return_value = None
        mock_kube.find_release_plan_for_snapshot.return_value = "release-plan-default"
        mock_kube.create_release.return_value = None  # Creation failed

        monitor.update_statuses()

        assert _phase(db, request, Phase.RELEASE) is None


class TestReleasePlanOverride:
    """Test the explicit --release-plan override path."""

    @pytest.fixture
    def pinned_monitor(self, db: WorkflowDatabase, mock_kube: MagicMock):
        """A monitor pinned to the novel-stage ReleasePlan."""
        return ReleaseMonitor(
            RequestView(db, skip_release=False),
            mock_kube,
            max_parallel=5,
            prefix="pnc-import-",
            release_plan="pnc-import-java-pulp-novel-stage",
        )

    def test_creates_release_against_pinned_plan(
        self, pinned_monitor: ReleaseMonitor, db: WorkflowDatabase, mock_kube: MagicMock
    ):
        """The override plan is used directly; the ambiguous lookup is skipped."""
        request = _seed(db, "r1", snapshot="snapshot-stage-123")

        mock_kube.find_release_for_snapshot_and_plan.return_value = None
        mock_kube.create_release.return_value = "release-novel-1"
        mock_kube.get_release_status.return_value = "Unknown"

        pinned_monitor.update_statuses()

        # Snapshot-based plan resolution must be bypassed entirely.
        mock_kube.find_release_plan_for_snapshot.assert_not_called()
        mock_kube.create_release.assert_called_once_with(
            "snapshot-stage-123", "pnc-import-java-pulp-novel-stage", "pnc-import-"
        )
        assert _phase(db, request, Phase.RELEASE).reference_id == "release-novel-1"

    def test_existing_lookup_is_scoped_to_pinned_plan(
        self, pinned_monitor: ReleaseMonitor, db: WorkflowDatabase, mock_kube: MagicMock
    ):
        """The 'already released?' check is scoped to the pinned plan.

        A Release created against a different stage plan (e.g. validated-stage) for
        the same snapshot must not be mistaken for the novel-stage release.
        """
        _seed(db, "r1", snapshot="snapshot-stage-123")

        # No novel-stage release exists yet (even though other plans may have one).
        mock_kube.find_release_for_snapshot_and_plan.return_value = None
        mock_kube.create_release.return_value = "release-novel-1"
        mock_kube.get_release_status.return_value = "Unknown"

        pinned_monitor.update_statuses()

        mock_kube.find_release_for_snapshot_and_plan.assert_called_once_with(
            "snapshot-stage-123", "pnc-import-java-pulp-novel-stage"
        )
        # The unscoped lookup must not be used when a plan is pinned.
        mock_kube.find_release_for_snapshot.assert_not_called()

    def test_adopts_existing_pinned_release(
        self, pinned_monitor: ReleaseMonitor, db: WorkflowDatabase, mock_kube: MagicMock
    ):
        """An existing Release against the pinned plan is adopted, not recreated."""
        request = _seed(db, "r1", snapshot="snapshot-stage-123")

        mock_kube.find_release_for_snapshot_and_plan.return_value = "release-novel-existing"
        mock_kube.get_release_status.return_value = "Unknown"

        pinned_monitor.update_statuses()

        mock_kube.create_release.assert_not_called()
        assert _phase(db, request, Phase.RELEASE).reference_id == "release-novel-existing"


class TestCheckReleaseCompletion:
    """Test release completion checking phase."""

    def test_marks_success_when_release_succeeds(
        self, monitor: ReleaseMonitor, db: WorkflowDatabase, mock_kube: MagicMock
    ):
        """Verify the release phase is marked SUCCESS when the release succeeds."""
        request = _seed(db, "r1", snapshot="snapshot-123", release="release-456")
        mock_kube.get_release_status.return_value = "True"

        monitor.update_statuses()

        assert _phase(db, request, Phase.RELEASE).status == PhaseStatus.SUCCESS
        assert monitor.view.state(request) == RequestState.SUCCESS

    def test_marks_failed_when_release_fails(self, monitor: ReleaseMonitor, db: WorkflowDatabase, mock_kube: MagicMock):
        """Verify the release phase is marked FAILED when the release fails."""
        request = _seed(db, "r1", snapshot="snapshot-123", release="release-456")
        mock_kube.get_release_status.return_value = "False"

        monitor.update_statuses()

        release = _phase(db, request, Phase.RELEASE)
        assert release.status == PhaseStatus.FAILED
        assert release.error_message == "Release release-456 failed"
        assert monitor.view.state(request) == RequestState.FAILED

    def test_waits_when_release_still_running(
        self, monitor: ReleaseMonitor, db: WorkflowDatabase, mock_kube: MagicMock
    ):
        """Verify that monitor waits when release is still running."""
        request = _seed(db, "r1", snapshot="snapshot-123", release="release-456")
        mock_kube.get_release_status.return_value = "Unknown"

        monitor.update_statuses()

        assert _phase(db, request, Phase.RELEASE).status == PhaseStatus.RUNNING

    def test_skip_release_is_noop(self, db: WorkflowDatabase, mock_kube: MagicMock):
        """A skip_release monitor does nothing even for a request past intake."""
        monitor = ReleaseMonitor(RequestView(db, skip_release=True), mock_kube, max_parallel=5, prefix="pnc-import-")
        request = _seed(db, "r1")

        monitor.update_statuses()

        mock_kube.find_snapshot_by_pipelinerun.assert_not_called()
        assert _phase(db, request, Phase.SNAPSHOT) is None


class TestIntegration:
    """Test complete flow through all phases."""

    def test_complete_flow_from_intake_to_success(
        self, monitor: ReleaseMonitor, db: WorkflowDatabase, mock_kube: MagicMock
    ):
        """Verify complete flow: discover snapshot, create release, mark success."""
        request = _seed(db, "r1")

        # Poll 1: Discover snapshot
        mock_kube.find_snapshot_by_pipelinerun.return_value = "snapshot-123"
        monitor.update_statuses()
        assert _phase(db, request, Phase.SNAPSHOT).reference_id == "snapshot-123"

        # Poll 2: Create release
        mock_kube.find_release_for_snapshot.return_value = None
        mock_kube.find_release_plan_for_snapshot.return_value = "release-plan-default"
        mock_kube.create_release.return_value = "release-456"
        monitor.update_statuses()
        assert _phase(db, request, Phase.RELEASE).reference_id == "release-456"

        # Poll 3: Release completes
        mock_kube.get_release_status.return_value = "True"
        monitor.update_statuses()
        assert monitor.view.state(request) == RequestState.SUCCESS
