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
from import_orchestrator.database import ImportDatabase
from import_orchestrator.engine import ImportOrchestrator, ImportTrigger, PipelineMonitor, ReleaseMonitor
from import_orchestrator.engine.pipelinerun import TriggerError
from import_orchestrator.models import ImportStatus, PipelineRunStatus


@pytest.fixture
def db(tmp_path: Path):
    db_path = tmp_path / "test.db"
    with ImportDatabase(db_path) as database:
        yield database


@pytest.fixture
def mock_kube():
    kube = MagicMock(spec=KubeClient)
    kube.count_running_imports.return_value = 0
    return kube


@pytest.fixture
def mock_builder():
    """Create a mock PipelineRunBuilder."""
    return MagicMock()


@pytest.fixture
def orchestrator(db: ImportDatabase, mock_kube: MagicMock, mock_builder: MagicMock):
    trigger = ImportTrigger(
        db=db,
        builder=mock_builder,
        max_parallel=5,
        max_retries=3,
    )
    pipeline_monitor = PipelineMonitor(db=db, kube=mock_kube)
    release_monitor = ReleaseMonitor(db=db, kube=mock_kube, max_parallel=5, prefix="pnc-import-")

    return ImportOrchestrator(
        db=db,
        trigger=trigger,
        pipeline_monitor=pipeline_monitor,
        release_monitor=release_monitor,
        poll_interval=1,
        max_retries=3,
    )


class TestUpdatePipelineRunStatuses:
    def test_updates_triggered_to_running(self, orchestrator: ImportOrchestrator, mock_kube: MagicMock):
        ref, _ = orchestrator.db.add_item("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        orchestrator.db.update_status(ref.id, ImportStatus.TRIGGERED, pipelinerun_name="pnc-import-abc")

        mock_kube.get_pipelinerun_status.return_value = PipelineRunStatus(name="pnc-import-abc", status="Unknown")

        orchestrator.update_pipelinerun_statuses()

        running = orchestrator.db.get_by_status(ImportStatus.RUNNING)
        assert len(running) == 1

    def test_updates_running_to_success(self, orchestrator: ImportOrchestrator, mock_kube: MagicMock):
        ref, _ = orchestrator.db.add_item("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        orchestrator.db.update_status(ref.id, ImportStatus.RUNNING, pipelinerun_name="pnc-import-abc")

        mock_kube.get_pipelinerun_status.return_value = PipelineRunStatus(name="pnc-import-abc", status="True")

        orchestrator.update_pipelinerun_statuses()

        awaiting = orchestrator.db.get_by_status(ImportStatus.AWAITING_RELEASE)
        assert len(awaiting) == 1

    def test_updates_running_to_failed(self, orchestrator: ImportOrchestrator, mock_kube: MagicMock):
        ref, _ = orchestrator.db.add_item("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        orchestrator.db.update_status(ref.id, ImportStatus.RUNNING, pipelinerun_name="pnc-import-abc")

        mock_kube.get_pipelinerun_status.return_value = PipelineRunStatus(name="pnc-import-abc", status="False")

        orchestrator.update_pipelinerun_statuses()

        failed = orchestrator.db.get_by_status(ImportStatus.FAILED)
        assert len(failed) == 1

    def test_skips_refs_without_pipelinerun_name(self, orchestrator: ImportOrchestrator, mock_kube: MagicMock):
        orchestrator.db.add_item("quay.io/repo:tag@sha256:abc")

        orchestrator.update_pipelinerun_statuses()

        # Should not call kube at all
        mock_kube.get_pipelinerun_status.assert_not_called()

    def test_skips_when_pr_status_is_none(self, orchestrator: ImportOrchestrator, mock_kube: MagicMock):
        ref, _ = orchestrator.db.add_item("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        orchestrator.db.update_status(ref.id, ImportStatus.TRIGGERED, pipelinerun_name="pnc-import-abc")

        mock_kube.get_pipelinerun_status.return_value = None

        orchestrator.update_pipelinerun_statuses()

        # Should remain triggered
        triggered = orchestrator.db.get_by_status(ImportStatus.TRIGGERED)
        assert len(triggered) == 1


class TestTriggerNextBatch:
    def test_triggers_up_to_available_slots(
        self, orchestrator: ImportOrchestrator, mock_kube: MagicMock, mock_builder: MagicMock
    ):
        # Add 3 already in-flight imports (simulating running/triggered)
        for i in range(3):
            ref, _ = orchestrator.db.add_item(f"quay.io/repo:inflight{i}@sha256:bbb{i}")
            assert ref.id is not None
            orchestrator.db.update_status(ref.id, ImportStatus.RUNNING)

        # Add 5 pending imports
        for i in range(5):
            orchestrator.db.add_item(f"quay.io/repo:tag{i}@sha256:aaa{i}")

        mock_builder.trigger.return_value = "pnc-import-xxx"

        # 5 max - 3 in-flight = 2 slots available
        triggered = orchestrator.trigger_next_batch()
        assert triggered == 2

    def test_returns_zero_when_no_slots(self, orchestrator: ImportOrchestrator, mock_kube: MagicMock):
        # Fill all 5 slots with in-flight imports
        for i in range(5):
            ref, _ = orchestrator.db.add_item(f"quay.io/repo:inflight{i}@sha256:bbb{i}")
            assert ref.id is not None
            orchestrator.db.update_status(ref.id, ImportStatus.RUNNING)

        # Add a pending import
        orchestrator.db.add_item("quay.io/repo:tag@sha256:abc")

        # No slots available, should return 0 without triggering
        triggered = orchestrator.trigger_next_batch()
        assert triggered == 0

    def test_handles_trigger_failure(
        self, orchestrator: ImportOrchestrator, mock_kube: MagicMock, mock_builder: MagicMock
    ):
        orchestrator.db.add_item("quay.io/repo:tag@sha256:abc")

        mock_builder.trigger.side_effect = TriggerError("connection refused")

        triggered = orchestrator.trigger_next_batch()
        assert triggered == 0

        # Should be marked as failed
        failed = orchestrator.db.get_by_status(ImportStatus.FAILED)
        assert len(failed) == 1


class TestIsComplete:
    def test_complete_when_all_success(self, orchestrator: ImportOrchestrator):
        ref, _ = orchestrator.db.add_item("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None
        orchestrator.db.update_status(ref.id, ImportStatus.SUCCESS)

        assert orchestrator.is_complete() is True

    def test_not_complete_with_pending(self, orchestrator: ImportOrchestrator):
        orchestrator.db.add_item("quay.io/repo:tag@sha256:abc")

        assert orchestrator.is_complete() is False

    def test_not_complete_with_retryable_failure(self, orchestrator: ImportOrchestrator):
        ref, _ = orchestrator.db.add_item("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None
        orchestrator.db.update_status(ref.id, ImportStatus.FAILED, retry_count=1)

        assert orchestrator.is_complete() is False

    def test_complete_when_failure_exhausted_retries(self, orchestrator: ImportOrchestrator):
        ref, _ = orchestrator.db.add_item("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None
        orchestrator.db.update_status(ref.id, ImportStatus.FAILED, retry_count=3)

        assert orchestrator.is_complete() is True

    def test_complete_with_empty_database(self, orchestrator: ImportOrchestrator):
        assert orchestrator.is_complete() is True


class TestRunUntilComplete:
    def test_completes_with_success(
        self, orchestrator: ImportOrchestrator, mock_kube: MagicMock, mock_builder: MagicMock
    ):
        ref, _ = orchestrator.db.add_item("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        # Mock builder to return PipelineRun name
        mock_builder.trigger.return_value = "pnc-import-abc"

        # After trigger, the status check will show success
        call_count = 0

        def fake_pr_status(name):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # First check: not found yet
            return PipelineRunStatus(name=name, status="True")

        mock_kube.get_pipelinerun_status.side_effect = fake_pr_status

        # Mock the release flow
        mock_kube.find_snapshot_by_pipelinerun.return_value = "snapshot-abc"
        mock_kube.find_release_for_snapshot.return_value = "release-abc"
        mock_kube.get_release_status.return_value = "True"

        exit_code = orchestrator.run_until_complete()
        assert exit_code == 0

    def test_returns_1_when_failures(self, orchestrator: ImportOrchestrator, mock_kube: MagicMock):
        ref, _ = orchestrator.db.add_item("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        # Pre-set as permanently failed
        orchestrator.db.update_status(ref.id, ImportStatus.FAILED, retry_count=3)

        exit_code = orchestrator.run_until_complete()
        assert exit_code == 1
