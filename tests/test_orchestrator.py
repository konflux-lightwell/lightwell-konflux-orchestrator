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

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from import_orchestrator.database import ImportDatabase
from import_orchestrator.kube import KubeClient
from import_orchestrator.models import ImportStatus, PipelineRunStatus
from import_orchestrator.orchestrator import ImportOrchestrator


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
def orchestrator(db: ImportDatabase, mock_kube: MagicMock, tmp_path: Path):
    trigger_script = tmp_path / "trigger.sh"
    trigger_script.touch()
    return ImportOrchestrator(
        db=db,
        kube=mock_kube,
        trigger_script=trigger_script,
        max_parallel=5,
        poll_interval=1,
        max_retries=3,
    )


class TestFetchAndStoreOciRefs:
    @patch("import_orchestrator.orchestrator.subprocess.run")
    def test_stores_fetched_refs(self, mock_run, orchestrator: ImportOrchestrator, tmp_path: Path):
        fetch_script = tmp_path / "fetch.sh"
        fetch_script.touch()

        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="quay.io/repo:tag1@sha256:aaa\nquay.io/repo:tag2@sha256:bbb\n",
            stderr="",
        )

        total, newly_added = orchestrator.fetch_and_store_oci_refs(fetch_script)
        assert total == 2
        assert newly_added == 2

    @patch("import_orchestrator.orchestrator.subprocess.run")
    def test_handles_duplicates(self, mock_run, orchestrator: ImportOrchestrator, tmp_path: Path):
        fetch_script = tmp_path / "fetch.sh"
        fetch_script.touch()

        # Pre-populate one reference
        orchestrator.db.add_oci_reference("quay.io/repo:tag1@sha256:aaa")

        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="quay.io/repo:tag1@sha256:aaa\nquay.io/repo:tag2@sha256:bbb\n",
            stderr="",
        )

        total, newly_added = orchestrator.fetch_and_store_oci_refs(fetch_script)
        assert total == 2
        assert newly_added == 1

    @patch("import_orchestrator.orchestrator.subprocess.run")
    def test_empty_output_returns_zero(self, mock_run, orchestrator: ImportOrchestrator, tmp_path: Path):
        fetch_script = tmp_path / "fetch.sh"
        fetch_script.touch()

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        total, newly_added = orchestrator.fetch_and_store_oci_refs(fetch_script)
        assert total == 0
        assert newly_added == 0

    @patch("import_orchestrator.orchestrator.subprocess.run")
    def test_script_failure_raises(self, mock_run, orchestrator: ImportOrchestrator, tmp_path: Path):
        fetch_script = tmp_path / "fetch.sh"
        fetch_script.touch()

        mock_run.side_effect = subprocess.CalledProcessError(1, "fetch", stderr="error")

        with pytest.raises(subprocess.CalledProcessError):
            orchestrator.fetch_and_store_oci_refs(fetch_script)


class TestTriggerImport:
    @patch("import_orchestrator.orchestrator.subprocess.run")
    def test_extracts_pipelinerun_name(self, mock_run, orchestrator: ImportOrchestrator):
        from import_orchestrator.models import OCIReference

        oci_ref = OCIReference(id=1, oci_ref="quay.io/repo:tag@sha256:abc", status=ImportStatus.PENDING)

        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="pipelinerun.tekton.dev/pnc-import-12345 created\n",
        )

        name = orchestrator.trigger_import(oci_ref)
        assert name == "pnc-import-12345"

    @patch("import_orchestrator.orchestrator.subprocess.run")
    def test_returns_none_when_name_not_found(self, mock_run, orchestrator: ImportOrchestrator):
        from import_orchestrator.models import OCIReference

        oci_ref = OCIReference(id=1, oci_ref="quay.io/repo:tag@sha256:abc", status=ImportStatus.PENDING)

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="some output", stderr=""
        )

        name = orchestrator.trigger_import(oci_ref)
        assert name is None


class TestUpdatePipelineRunStatuses:
    def test_updates_triggered_to_running(self, orchestrator: ImportOrchestrator, mock_kube: MagicMock):
        ref, _ = orchestrator.db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        orchestrator.db.update_status(
            ref.id, ImportStatus.TRIGGERED, pipelinerun_name="pnc-import-abc"
        )

        mock_kube.get_pipelinerun_status.return_value = PipelineRunStatus(
            name="pnc-import-abc", status="Unknown"
        )

        orchestrator.update_pipelinerun_statuses()

        running = orchestrator.db.get_by_status(ImportStatus.RUNNING)
        assert len(running) == 1

    def test_updates_running_to_success(self, orchestrator: ImportOrchestrator, mock_kube: MagicMock):
        ref, _ = orchestrator.db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        orchestrator.db.update_status(
            ref.id, ImportStatus.RUNNING, pipelinerun_name="pnc-import-abc"
        )

        mock_kube.get_pipelinerun_status.return_value = PipelineRunStatus(
            name="pnc-import-abc", status="True"
        )

        orchestrator.update_pipelinerun_statuses()

        success = orchestrator.db.get_by_status(ImportStatus.SUCCESS)
        assert len(success) == 1

    def test_updates_running_to_failed(self, orchestrator: ImportOrchestrator, mock_kube: MagicMock):
        ref, _ = orchestrator.db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        orchestrator.db.update_status(
            ref.id, ImportStatus.RUNNING, pipelinerun_name="pnc-import-abc"
        )

        mock_kube.get_pipelinerun_status.return_value = PipelineRunStatus(
            name="pnc-import-abc", status="False"
        )

        orchestrator.update_pipelinerun_statuses()

        failed = orchestrator.db.get_by_status(ImportStatus.FAILED)
        assert len(failed) == 1

    def test_skips_refs_without_pipelinerun_name(self, orchestrator: ImportOrchestrator, mock_kube: MagicMock):
        orchestrator.db.add_oci_reference("quay.io/repo:tag@sha256:abc")

        orchestrator.update_pipelinerun_statuses()

        # Should not call kube at all
        mock_kube.get_pipelinerun_status.assert_not_called()

    def test_skips_when_pr_status_is_none(self, orchestrator: ImportOrchestrator, mock_kube: MagicMock):
        ref, _ = orchestrator.db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        orchestrator.db.update_status(
            ref.id, ImportStatus.TRIGGERED, pipelinerun_name="pnc-import-abc"
        )

        mock_kube.get_pipelinerun_status.return_value = None

        orchestrator.update_pipelinerun_statuses()

        # Should remain triggered
        triggered = orchestrator.db.get_by_status(ImportStatus.TRIGGERED)
        assert len(triggered) == 1


class TestTriggerNextBatch:
    @patch("import_orchestrator.orchestrator.subprocess.run")
    def test_triggers_up_to_available_slots(self, mock_run, orchestrator: ImportOrchestrator, mock_kube: MagicMock):
        mock_kube.count_running_imports.return_value = 3  # 5 max - 3 running = 2 slots

        for i in range(5):
            orchestrator.db.add_oci_reference(f"quay.io/repo:tag{i}@sha256:aaa{i}")

        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="pipelinerun.tekton.dev/pnc-import-xxx created\n",
        )

        triggered = orchestrator.trigger_next_batch()
        assert triggered == 2

    def test_returns_zero_when_no_slots(self, orchestrator: ImportOrchestrator, mock_kube: MagicMock):
        mock_kube.count_running_imports.return_value = 5

        orchestrator.db.add_oci_reference("quay.io/repo:tag@sha256:abc")

        triggered = orchestrator.trigger_next_batch()
        assert triggered == 0

    @patch("import_orchestrator.orchestrator.subprocess.run")
    def test_handles_trigger_failure(self, mock_run, orchestrator: ImportOrchestrator, mock_kube: MagicMock):
        orchestrator.db.add_oci_reference("quay.io/repo:tag@sha256:abc")

        error = subprocess.CalledProcessError(1, "trigger")
        error.stderr = "connection refused"
        mock_run.side_effect = error

        triggered = orchestrator.trigger_next_batch()
        assert triggered == 0

        # Should be marked as failed
        failed = orchestrator.db.get_by_status(ImportStatus.FAILED)
        assert len(failed) == 1


class TestIsComplete:
    def test_complete_when_all_success(self, orchestrator: ImportOrchestrator):
        ref, _ = orchestrator.db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None
        orchestrator.db.update_status(ref.id, ImportStatus.SUCCESS)

        assert orchestrator.is_complete() is True

    def test_not_complete_with_pending(self, orchestrator: ImportOrchestrator):
        orchestrator.db.add_oci_reference("quay.io/repo:tag@sha256:abc")

        assert orchestrator.is_complete() is False

    def test_not_complete_with_retryable_failure(self, orchestrator: ImportOrchestrator):
        ref, _ = orchestrator.db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None
        orchestrator.db.update_status(ref.id, ImportStatus.FAILED, retry_count=1)

        assert orchestrator.is_complete() is False

    def test_complete_when_failure_exhausted_retries(self, orchestrator: ImportOrchestrator):
        ref, _ = orchestrator.db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None
        orchestrator.db.update_status(ref.id, ImportStatus.FAILED, retry_count=3)

        assert orchestrator.is_complete() is True

    def test_complete_with_empty_database(self, orchestrator: ImportOrchestrator):
        assert orchestrator.is_complete() is True


class TestRunUntilComplete:
    @patch("import_orchestrator.orchestrator.subprocess.run")
    def test_completes_with_success(self, mock_run, orchestrator: ImportOrchestrator, mock_kube: MagicMock):
        ref, _ = orchestrator.db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        # First call: trigger import
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="pipelinerun.tekton.dev/pnc-import-abc created\n",
        )

        # After trigger, the status check will show success
        call_count = 0

        def fake_pr_status(name):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # First check: not found yet
            return PipelineRunStatus(name=name, status="True")

        mock_kube.get_pipelinerun_status.side_effect = fake_pr_status

        exit_code = orchestrator.run_until_complete()
        assert exit_code == 0

    def test_returns_1_when_failures(self, orchestrator: ImportOrchestrator, mock_kube: MagicMock):
        ref, _ = orchestrator.db.add_oci_reference("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        # Pre-set as permanently failed
        orchestrator.db.update_status(ref.id, ImportStatus.FAILED, retry_count=3)

        exit_code = orchestrator.run_until_complete()
        assert exit_code == 1
