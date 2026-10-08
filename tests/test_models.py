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

import pytest

from import_orchestrator.models import (
    PHASE_REFERENCE_TYPE,
    Attempt,
    AttemptPhase,
    ClusterResource,
    ImportItem,
    ImportStatus,
    Phase,
    PhaseStatus,
    PipelineRunStatus,
    Request,
)


class TestImportStatus:
    def test_values(self):
        assert ImportStatus.PENDING.value == "pending"
        assert ImportStatus.TRIGGERED.value == "triggered"
        assert ImportStatus.RUNNING.value == "running"
        assert ImportStatus.SUCCESS.value == "success"
        assert ImportStatus.FAILED.value == "failed"

    def test_is_string_enum(self):
        assert isinstance(ImportStatus.PENDING, str)
        assert ImportStatus.PENDING == "pending"

    def test_round_trip_from_value(self):
        for status in ImportStatus:
            assert ImportStatus(status.value) is status


class TestImportItem:
    def test_defaults(self):
        item = ImportItem(id=1, ref="quay.io/repo:tag@sha256:abc", status=ImportStatus.PENDING)
        assert item.pipelinerun_name is None
        assert item.triggered_at is None
        assert item.completed_at is None
        assert item.last_checked_at is None
        assert item.error_message is None
        assert item.retry_count == 0

    def test_all_fields(self):
        from datetime import datetime

        now = datetime.now()
        item = ImportItem(
            id=42,
            ref="quay.io/repo:tag@sha256:abc",
            status=ImportStatus.RUNNING,
            pipelinerun_name="pnc-import-abc",
            triggered_at=now,
            completed_at=None,
            last_checked_at=now,
            error_message=None,
            retry_count=2,
        )
        assert item.id == 42
        assert item.retry_count == 2
        assert item.pipelinerun_name == "pnc-import-abc"


class TestPipelineRunStatus:
    def test_running(self):
        pr = PipelineRunStatus(name="pr-1", status="Unknown")
        assert pr.is_running is True
        assert pr.is_successful is False
        assert pr.is_failed is False

    def test_successful(self):
        pr = PipelineRunStatus(name="pr-2", status="True")
        assert pr.is_running is False
        assert pr.is_successful is True
        assert pr.is_failed is False

    def test_failed(self):
        pr = PipelineRunStatus(name="pr-3", status="False")
        assert pr.is_running is False
        assert pr.is_successful is False
        assert pr.is_failed is True


class TestPhase:
    def test_values(self):
        assert Phase.INTAKE.value == "intake"
        assert Phase.SNAPSHOT.value == "snapshot"
        assert Phase.INTEGRATION_TESTS.value == "integration_tests"
        assert Phase.RELEASE.value == "release"

    def test_round_trip_from_value(self):
        for phase in Phase:
            assert Phase(phase.value) is phase


class TestPhaseStatus:
    def test_values(self):
        assert PhaseStatus.PENDING.value == "pending"
        assert PhaseStatus.RUNNING.value == "running"
        assert PhaseStatus.SUCCESS.value == "success"
        assert PhaseStatus.FAILED.value == "failed"
        assert PhaseStatus.SKIPPED.value == "skipped"

    def test_is_string_enum(self):
        assert isinstance(PhaseStatus.SUCCESS, str)
        assert PhaseStatus.SUCCESS == "success"


class TestClusterResource:
    def test_values(self):
        assert ClusterResource.PIPELINE_RUN.value == "pipelineRun"
        assert ClusterResource.SNAPSHOT.value == "snapshot"
        assert ClusterResource.RELEASE.value == "release"


class TestRequest:
    def test_defaults(self):
        request = Request(
            id=1,
            namespace="ns",
            application="app",
            component="comp",
            pipeline="import-pnc",
            release_plan="rp",
            artifact="quay.io/repo@sha256:abc",
            target="remediated",
        )
        assert request.created_at is None
        assert request.artifact == "quay.io/repo@sha256:abc"
        assert request.target == "remediated"


class TestAttempt:
    def test_defaults(self):
        attempt = Attempt(id=1, request_id=7)
        assert attempt.request_id == 7
        assert attempt.created_at is None


class TestAttemptPhase:
    def test_defaults(self):
        phase = AttemptPhase(
            id=1,
            attempt_id=3,
            name=Phase.INTAKE,
            status=PhaseStatus.PENDING,
        )
        assert phase.reference_type is None
        assert phase.reference_id is None
        assert phase.error_message is None
        assert phase.created_at is None
        assert phase.updated_at is None

    def test_all_fields(self):
        phase = AttemptPhase(
            id=9,
            attempt_id=3,
            name=Phase.RELEASE,
            status=PhaseStatus.FAILED,
            reference_type=ClusterResource.RELEASE,
            reference_id="release-abc",
            error_message="Release release-abc failed",
        )
        assert phase.name is Phase.RELEASE
        assert phase.reference_type is ClusterResource.RELEASE
        assert phase.error_message == "Release release-abc failed"


class TestPhaseClusterResource:
    @pytest.mark.parametrize(
        "phase,reference_type",
        [
            (Phase.INTAKE, ClusterResource.PIPELINE_RUN),
            (Phase.INTEGRATION_TESTS, ClusterResource.PIPELINE_RUN),
            (Phase.SNAPSHOT, ClusterResource.SNAPSHOT),
            (Phase.RELEASE, ClusterResource.RELEASE),
        ],
    )
    def test_each_phase_knows_its_reference_type(self, phase, reference_type):
        assert PHASE_REFERENCE_TYPE[phase] is reference_type

    def test_every_phase_is_in_the_map(self):
        assert set(PHASE_REFERENCE_TYPE) == set(Phase)

    def test_phase_value_is_the_string(self):
        assert Phase.INTAKE.value == "intake"
        assert Phase("intake") is Phase.INTAKE


class TestPhaseStatusCarriesForward:
    def test_only_success_carries_forward(self):
        assert PhaseStatus.SUCCESS.carries_forward is True

    @pytest.mark.parametrize(
        "status",
        [PhaseStatus.PENDING, PhaseStatus.RUNNING, PhaseStatus.FAILED, PhaseStatus.SKIPPED],
    )
    def test_others_do_not_carry_forward(self, status):
        assert status.carries_forward is False


class TestAttemptPhaseValidation:
    """AttemptPhase enforces the phase-model invariants in __post_init__."""

    def test_reference_pair_both_absent_is_ok(self):
        phase = AttemptPhase(id=None, attempt_id=1, name=Phase.INTAKE, status=PhaseStatus.RUNNING)
        assert phase.reference_type is None
        assert phase.reference_id is None

    def test_reference_type_without_id_is_rejected(self):
        with pytest.raises(ValueError, match="supplied together"):
            AttemptPhase(
                id=None,
                attempt_id=1,
                name=Phase.INTAKE,
                status=PhaseStatus.RUNNING,
                reference_type=ClusterResource.PIPELINE_RUN,
            )

    def test_reference_id_without_type_is_rejected(self):
        with pytest.raises(ValueError, match="supplied together"):
            AttemptPhase(
                id=None,
                attempt_id=1,
                name=Phase.INTAKE,
                status=PhaseStatus.RUNNING,
                reference_id="pr-1",
            )

    def test_reference_type_must_match_phase(self):
        with pytest.raises(ValueError, match="intake phase must reference a pipelineRun"):
            AttemptPhase(
                id=None,
                attempt_id=1,
                name=Phase.INTAKE,
                status=PhaseStatus.RUNNING,
                reference_type=ClusterResource.SNAPSHOT,
                reference_id="snap-1",
            )

    @pytest.mark.parametrize(
        "name,reference_type",
        [
            (Phase.INTAKE, ClusterResource.PIPELINE_RUN),
            (Phase.INTEGRATION_TESTS, ClusterResource.PIPELINE_RUN),
            (Phase.SNAPSHOT, ClusterResource.SNAPSHOT),
            (Phase.RELEASE, ClusterResource.RELEASE),
        ],
    )
    def test_matching_reference_type_is_accepted(self, name, reference_type):
        phase = AttemptPhase(
            id=None,
            attempt_id=1,
            name=name,
            status=PhaseStatus.SUCCESS,
            reference_type=reference_type,
            reference_id="ref-1",
        )
        assert phase.reference_type is reference_type

    def test_error_message_kept_while_failed(self):
        phase = AttemptPhase(
            id=None,
            attempt_id=1,
            name=Phase.RELEASE,
            status=PhaseStatus.FAILED,
            error_message="boom",
        )
        assert phase.error_message == "boom"

    @pytest.mark.parametrize(
        "status", [PhaseStatus.PENDING, PhaseStatus.RUNNING, PhaseStatus.SUCCESS, PhaseStatus.SKIPPED]
    )
    def test_error_message_dropped_when_not_failed(self, status):
        phase = AttemptPhase(
            id=None,
            attempt_id=1,
            name=Phase.INTAKE,
            status=status,
            error_message="ignored",
        )
        assert phase.error_message is None
