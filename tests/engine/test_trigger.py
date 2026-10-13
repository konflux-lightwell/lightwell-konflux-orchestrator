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

from import_orchestrator.ecosystems.java.pipelinerun import TriggerError
from import_orchestrator.engine import ImportTrigger, WorkflowView
from import_orchestrator.models import ClusterResource, Phase, PhaseStatus, Request, WorkflowState
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


def _seed_request(db: WorkflowDatabase, ref: str) -> Request:
    request, _ = db.get_or_create_request(**_identity(ref))
    return request


def _seed_running(db: WorkflowDatabase, ref: str) -> Request:
    """Seed a Request whose latest attempt has an in-flight intake."""
    request = _seed_request(db, ref)
    attempt = db.create_attempt(request.id)
    db.upsert_phase(
        attempt.id,
        Phase.INTAKE,
        PhaseStatus.RUNNING,
        reference_type=ClusterResource.PIPELINE_RUN,
        reference_id=f"pr-{ref}",
    )
    return request


def _seed_failed_intake(db: WorkflowDatabase, ref: str) -> Request:
    """Seed a Request whose latest attempt failed at intake (retriable)."""
    request = _seed_request(db, ref)
    attempt = db.create_attempt(request.id)
    db.upsert_phase(attempt.id, Phase.INTAKE, PhaseStatus.FAILED, error_message="boom")
    return request


@pytest.fixture
def db(tmp_path: Path):
    db_path = tmp_path / "test.db"
    with WorkflowDatabase(db_path) as database:
        yield database


@pytest.fixture
def mock_kube():
    kube = MagicMock()
    kube.create_pipelinerun.return_value = "pnc-import-xxx"
    return kube


@pytest.fixture
def mock_build():
    return MagicMock(return_value={"kind": "PipelineRun"})


@pytest.fixture
def trigger(db: WorkflowDatabase, mock_kube: MagicMock, mock_build: MagicMock):
    view = WorkflowView(db, skip_release=False)
    return ImportTrigger(
        view=view,
        kube=mock_kube,
        build_pipelinerun=mock_build,
        max_parallel=5,
        max_retries=3,
    )


class TestTriggerNextBatch:
    """Test the trigger_next_batch method."""

    def test_triggers_up_to_available_slots(self, trigger: ImportTrigger, db: WorkflowDatabase, mock_build: MagicMock):
        """Verify that imports are triggered up to the available capacity."""
        # 3 already in-flight (intake RUNNING)
        for i in range(3):
            _seed_running(db, f"inflight{i}")
        # 5 fresh pending requests (no attempt yet)
        for i in range(5):
            _seed_request(db, f"tag{i}")

        # 5 max - 3 in-flight = 2 slots available
        triggered = trigger.trigger_next_batch()
        assert triggered == 2

        # The two triggered requests now have a RUNNING intake with a reference.
        running_intakes = [
            r
            for r in db.list_requests()
            if (p := db.latest_phase_map(r.id).get(Phase.INTAKE)) is not None
            and p.status == PhaseStatus.RUNNING
            and r.artifact.startswith("tag")
        ]
        assert len(running_intakes) == 2
        assert mock_build.call_count == 2

    def test_returns_zero_when_no_slots(self, trigger: ImportTrigger, db: WorkflowDatabase):
        """Verify that no imports are triggered when capacity is full."""
        for i in range(5):
            _seed_running(db, f"inflight{i}")
        _seed_request(db, "tag-pending")

        triggered = trigger.trigger_next_batch()
        assert triggered == 0

        # Pending request still has no attempt.
        pending = next(r for r in db.list_requests() if r.artifact == "tag-pending")
        assert db.attempt_count(pending.id) == 0

    def test_handles_trigger_failure(self, trigger: ImportTrigger, db: WorkflowDatabase, mock_kube: MagicMock):
        """Verify that trigger failures are recorded on the intake phase."""
        request = _seed_request(db, "tag-fail")
        mock_kube.create_pipelinerun.side_effect = TriggerError("connection refused")

        triggered = trigger.trigger_next_batch()
        assert triggered == 0

        intake = db.latest_phase_map(request.id)[Phase.INTAKE]
        assert intake.status == PhaseStatus.FAILED
        assert "connection refused" in intake.error_message

    def test_records_failure_when_kube_returns_no_name(
        self, trigger: ImportTrigger, db: WorkflowDatabase, mock_kube: MagicMock
    ):
        """A None PipelineRun name is a failure recorded on the intake phase."""
        request = _seed_request(db, "tag-none")
        mock_kube.create_pipelinerun.return_value = None

        triggered = trigger.trigger_next_batch()
        assert triggered == 0

        intake = db.latest_phase_map(request.id)[Phase.INTAKE]
        assert intake.status == PhaseStatus.FAILED
        assert "PipelineRun creation failed" in intake.error_message

    def test_records_intake_reference_on_success(
        self, trigger: ImportTrigger, db: WorkflowDatabase, mock_kube: MagicMock
    ):
        """A successful trigger records the PipelineRun as the intake reference."""
        request = _seed_request(db, "tag-ok")
        mock_kube.create_pipelinerun.return_value = "pnc-import-12345"

        triggered = trigger.trigger_next_batch()
        assert triggered == 1

        intake = db.latest_phase_map(request.id)[Phase.INTAKE]
        assert intake.status == PhaseStatus.RUNNING
        assert intake.reference_type == ClusterResource.PIPELINE_RUN
        assert intake.reference_id == "pnc-import-12345"

    def test_triggers_retry_candidate_as_new_attempt(self, trigger: ImportTrigger, db: WorkflowDatabase):
        """A failed import is retried as a brand-new Attempt; history is preserved."""
        request = _seed_failed_intake(db, "tag-retry")
        assert db.attempt_count(request.id) == 1

        triggered = trigger.trigger_next_batch()
        assert triggered == 1

        # A second Attempt now exists with a RUNNING intake.
        assert db.attempt_count(request.id) == 2
        intake = db.latest_phase_map(request.id)[Phase.INTAKE]
        assert intake.status == PhaseStatus.RUNNING

    def test_resumes_past_carried_forward_intake(
        self, trigger: ImportTrigger, db: WorkflowDatabase, mock_kube: MagicMock
    ):
        """A retry whose intake already succeeded resumes without a new PipelineRun."""
        request = _seed_request(db, "tag-resume")
        attempt = db.create_attempt(request.id)
        db.upsert_phase(
            attempt.id,
            Phase.INTAKE,
            PhaseStatus.SUCCESS,
            reference_type=ClusterResource.PIPELINE_RUN,
            reference_id="pr-resume",
        )
        # The release failed, so the request is FAILED and retriable.
        db.upsert_phase(
            attempt.id,
            Phase.RELEASE,
            PhaseStatus.FAILED,
            reference_type=ClusterResource.RELEASE,
            reference_id="rel-resume",
            error_message="release failed",
        )

        triggered = trigger.trigger_next_batch()
        assert triggered == 0  # resumed, not re-triggered

        # No new PipelineRun was submitted; the new attempt carried intake SUCCESS forward.
        mock_kube.create_pipelinerun.assert_not_called()
        assert db.attempt_count(request.id) == 2
        latest = db.latest_phase_map(request.id)
        assert latest[Phase.INTAKE].status == PhaseStatus.SUCCESS
        # The failed release was NOT carried forward; only settled phases are.
        assert Phase.RELEASE not in latest

    def test_does_not_retry_once_attempts_exhausted(self, trigger: ImportTrigger, db: WorkflowDatabase):
        """Once attempts exceed max_retries the failed request is not retried."""
        request = _seed_request(db, "tag-exhausted")
        for _ in range(4):  # max_retries=3 -> 4th attempt is the last allowed
            attempt = db.create_attempt(request.id)
            db.upsert_phase(attempt.id, Phase.INTAKE, PhaseStatus.FAILED, error_message="boom")

        view = trigger.view
        assert view.state(request) == WorkflowState.FAILED
        assert trigger.trigger_next_batch() == 0
        assert db.attempt_count(request.id) == 4  # unchanged

    def test_trigger_error_from_build_is_recorded_and_batch_survives(
        self, trigger: ImportTrigger, db: WorkflowDatabase, mock_build: MagicMock
    ):
        """An expected TriggerError from the manifest builder is recorded as a
        failed intake and does not abort the rest of the batch."""
        bad = _seed_request(db, "bad-ref")
        good = _seed_request(db, "good-ref")

        def build(ref: str) -> dict:
            if ref == "bad-ref":
                raise TriggerError("malformed ref")
            return {"kind": "PipelineRun"}

        mock_build.side_effect = build

        # The bad ref fails but the good ref is still triggered in the same batch.
        triggered = trigger.trigger_next_batch()
        assert triggered == 1

        bad_intake = db.latest_phase_map(bad.id)[Phase.INTAKE]
        assert bad_intake.status == PhaseStatus.FAILED
        assert "malformed ref" in bad_intake.error_message
        assert db.latest_phase_map(good.id)[Phase.INTAKE].status == PhaseStatus.RUNNING

    def test_unexpected_build_error_propagates(
        self, trigger: ImportTrigger, db: WorkflowDatabase, mock_build: MagicMock
    ):
        """An unexpected (non-TriggerError) error from the manifest builder is a
        programming bug: it propagates immediately instead of being swallowed and
        retried every poll."""
        _seed_request(db, "bad-ref")

        mock_build.side_effect = ValueError("malformed ref")

        with pytest.raises(ValueError, match="malformed ref"):
            trigger.trigger_next_batch()

    def test_recovers_phase_less_attempt(self, trigger: ImportTrigger, db: WorkflowDatabase, mock_kube: MagicMock):
        """A latest Attempt with no intake phase (e.g. a crash between creating the
        Attempt and recording its intake) is reused and re-triggered, not stacked."""
        request = _seed_request(db, "tag-bare")
        bare = db.create_attempt(request.id)  # committed Attempt, no intake phase yet
        assert db.attempt_count(request.id) == 1
        mock_kube.create_pipelinerun.return_value = "pnc-import-recovered"

        triggered = trigger.trigger_next_batch()
        assert triggered == 1

        # The bare Attempt was reused rather than a second one created.
        assert db.attempt_count(request.id) == 1
        intake = db.latest_phase_map(request.id)[Phase.INTAKE]
        assert intake.status == PhaseStatus.RUNNING
        assert intake.attempt_id == bare.id
