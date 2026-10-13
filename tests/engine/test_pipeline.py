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
from import_orchestrator.engine import PipelineMonitor, WorkflowView
from import_orchestrator.models import ClusterResource, Phase, PhaseStatus, PipelineRunStatus, Request, WorkflowState
from import_orchestrator.workflow_db import WorkflowDatabase


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


def _seed_running(db: WorkflowDatabase, ref: str, pr_name: str) -> Request:
    """Seed a Request whose latest attempt has an intake RUNNING with a reference."""
    request, _ = db.get_or_create_request(**_identity(ref))
    attempt = db.create_attempt(request.id)
    db.upsert_phase(
        attempt.id,
        Phase.INTAKE,
        PhaseStatus.RUNNING,
        reference_type=ClusterResource.PIPELINE_RUN,
        reference_id=pr_name,
    )
    return request


def _intake(db: WorkflowDatabase, request: Request):
    return db.latest_phase_map(request.id).get(Phase.INTAKE)


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
    return PipelineMonitor(WorkflowView(db, skip_release=False), mock_kube)


@pytest.fixture
def scratch_monitor(db: WorkflowDatabase, mock_kube: MagicMock):
    """PipelineMonitor with skip_release=True (scratch/non-releasing target)."""
    return PipelineMonitor(WorkflowView(db, skip_release=True), mock_kube)


class TestUpdateStatuses:
    """Test the update_statuses method."""

    def test_running_pipeline_stays_running(self, monitor: PipelineMonitor, db: WorkflowDatabase, mock_kube: MagicMock):
        """An intake whose PipelineRun is still running stays RUNNING."""
        request = _seed_running(db, "r1", "pnc-import-abc")
        mock_kube.get_pipelinerun_status.return_value = PipelineRunStatus(name="pnc-import-abc", status="Unknown")

        monitor.update_statuses()

        assert _intake(db, request).status == PhaseStatus.RUNNING

    def test_successful_pipeline_advances_to_success(
        self, monitor: PipelineMonitor, db: WorkflowDatabase, mock_kube: MagicMock
    ):
        """A successful PipelineRun advances the intake phase to SUCCESS."""
        request = _seed_running(db, "r1", "pnc-import-abc")
        mock_kube.get_pipelinerun_status.return_value = PipelineRunStatus(name="pnc-import-abc", status="True")

        monitor.update_statuses()

        assert _intake(db, request).status == PhaseStatus.SUCCESS
        assert monitor.view.state(request) == WorkflowState.AWAITING_RELEASE

    def test_failed_pipeline_advances_to_failed(
        self, monitor: PipelineMonitor, db: WorkflowDatabase, mock_kube: MagicMock
    ):
        """A failed PipelineRun advances the intake phase to FAILED."""
        request = _seed_running(db, "r1", "pnc-import-abc")
        mock_kube.get_pipelinerun_status.return_value = PipelineRunStatus(name="pnc-import-abc", status="False")
        mock_kube.get_pipelinerun_failure_detail.return_value = None

        monitor.update_statuses()

        assert _intake(db, request).status == PhaseStatus.FAILED

    def test_failure_captures_diagnostics_in_error_message(
        self, monitor: PipelineMonitor, db: WorkflowDatabase, mock_kube: MagicMock
    ):
        """Verify that the captured failure detail is stored as the error message."""
        request = _seed_running(db, "r1", "pnc-import-abc")
        mock_kube.get_pipelinerun_status.return_value = PipelineRunStatus(name="pnc-import-abc", status="False")
        mock_kube.get_pipelinerun_failure_detail.return_value = (
            "TaskRun pnc-import-abc-build: Failed - step failed\n  step 'build' terminated: reason=Error exitCode=1"
        )

        monitor.update_statuses()

        intake = _intake(db, request)
        assert intake.status == PhaseStatus.FAILED
        assert "exitCode=1" in intake.error_message
        mock_kube.get_pipelinerun_failure_detail.assert_called_once_with("pnc-import-abc")

    def test_failure_falls_back_to_generic_message(
        self, monitor: PipelineMonitor, db: WorkflowDatabase, mock_kube: MagicMock
    ):
        """Verify the generic message is used when no diagnostics can be gathered."""
        request = _seed_running(db, "r1", "pnc-import-abc")
        mock_kube.get_pipelinerun_status.return_value = PipelineRunStatus(name="pnc-import-abc", status="False")
        mock_kube.get_pipelinerun_failure_detail.return_value = None

        monitor.update_statuses()

        assert _intake(db, request).error_message == "PipelineRun failed"

    def test_skips_requests_without_an_attempt(
        self, monitor: PipelineMonitor, db: WorkflowDatabase, mock_kube: MagicMock
    ):
        """A pending Request (no attempt/intake) is skipped; kube is not polled."""
        db.get_or_create_request(**_identity("r1"))

        monitor.update_statuses()

        mock_kube.get_pipelinerun_status.assert_not_called()

    def test_skips_when_pr_status_is_none(self, monitor: PipelineMonitor, db: WorkflowDatabase, mock_kube: MagicMock):
        """When kube returns no status, the intake stays RUNNING."""
        request = _seed_running(db, "r1", "pnc-import-abc")
        mock_kube.get_pipelinerun_status.return_value = None

        monitor.update_statuses()

        assert _intake(db, request).status == PhaseStatus.RUNNING

    def test_skip_release_success_is_terminal(
        self, scratch_monitor: PipelineMonitor, db: WorkflowDatabase, mock_kube: MagicMock
    ):
        """With skip_release=True a successful pipeline is terminal SUCCESS."""
        request = _seed_running(db, "scratch", "scratch-abc")
        mock_kube.get_pipelinerun_status.return_value = PipelineRunStatus(name="scratch-abc", status="True")

        scratch_monitor.update_statuses()

        assert _intake(db, request).status == PhaseStatus.SUCCESS
        assert scratch_monitor.view.state(request) == WorkflowState.SUCCESS

    def test_processes_multiple_imports(self, monitor: PipelineMonitor, db: WorkflowDatabase, mock_kube: MagicMock):
        """Verify that multiple imports are processed correctly."""
        r1 = _seed_running(db, "r1", "pnc-import-1")
        r2 = _seed_running(db, "r2", "pnc-import-2")

        def get_status_side_effect(name):
            if name == "pnc-import-1":
                return PipelineRunStatus(name=name, status="Unknown")  # Running
            elif name == "pnc-import-2":
                return PipelineRunStatus(name=name, status="True")  # Successful
            return None

        mock_kube.get_pipelinerun_status.side_effect = get_status_side_effect

        monitor.update_statuses()

        assert _intake(db, r1).status == PhaseStatus.RUNNING
        assert _intake(db, r2).status == PhaseStatus.SUCCESS
