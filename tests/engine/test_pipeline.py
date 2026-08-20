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
from import_orchestrator.engine import PipelineMonitor
from import_orchestrator.models import ImportStatus, PipelineRunStatus


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
    """Create a PipelineMonitor instance with a test database and mock kube client."""
    return PipelineMonitor(db, mock_kube)


class TestUpdateStatuses:
    """Test the update_statuses method."""

    def test_updates_triggered_to_running(self, monitor: PipelineMonitor, mock_kube: MagicMock):
        """Verify that TRIGGERED imports are updated to RUNNING when the PipelineRun is running."""
        ref, _ = monitor.db.add_item("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        monitor.db.update_status(ref.id, ImportStatus.TRIGGERED, pipelinerun_name="pnc-import-abc")

        mock_kube.get_pipelinerun_status.return_value = PipelineRunStatus(name="pnc-import-abc", status="Unknown")

        monitor.update_statuses()

        running = monitor.db.get_by_status(ImportStatus.RUNNING)
        assert len(running) == 1

    def test_updates_running_to_awaiting_release(self, monitor: PipelineMonitor, mock_kube: MagicMock):
        """Verify that RUNNING imports are updated to AWAITING_RELEASE when the PipelineRun succeeds."""
        ref, _ = monitor.db.add_item("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        monitor.db.update_status(ref.id, ImportStatus.RUNNING, pipelinerun_name="pnc-import-abc")

        mock_kube.get_pipelinerun_status.return_value = PipelineRunStatus(name="pnc-import-abc", status="True")

        monitor.update_statuses()

        awaiting = monitor.db.get_by_status(ImportStatus.AWAITING_RELEASE)
        assert len(awaiting) == 1

    def test_updates_running_to_failed(self, monitor: PipelineMonitor, mock_kube: MagicMock):
        """Verify that RUNNING imports are updated to FAILED when the PipelineRun fails."""
        ref, _ = monitor.db.add_item("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        monitor.db.update_status(ref.id, ImportStatus.RUNNING, pipelinerun_name="pnc-import-abc")

        mock_kube.get_pipelinerun_status.return_value = PipelineRunStatus(name="pnc-import-abc", status="False")

        monitor.update_statuses()

        failed = monitor.db.get_by_status(ImportStatus.FAILED)
        assert len(failed) == 1

    def test_skips_refs_without_pipelinerun_name(self, monitor: PipelineMonitor, mock_kube: MagicMock):
        """Verify that references without a PipelineRun name are skipped."""
        monitor.db.add_item("quay.io/repo:tag@sha256:abc")

        monitor.update_statuses()

        # Should not call kube at all
        mock_kube.get_pipelinerun_status.assert_not_called()

    def test_skips_when_pr_status_is_none(self, monitor: PipelineMonitor, mock_kube: MagicMock):
        """Verify that references are skipped when kube returns None."""
        ref, _ = monitor.db.add_item("quay.io/repo:tag@sha256:abc")
        assert ref.id is not None

        monitor.db.update_status(ref.id, ImportStatus.TRIGGERED, pipelinerun_name="pnc-import-abc")

        mock_kube.get_pipelinerun_status.return_value = None

        monitor.update_statuses()

        # Should remain triggered
        triggered = monitor.db.get_by_status(ImportStatus.TRIGGERED)
        assert len(triggered) == 1

    def test_processes_multiple_imports(self, monitor: PipelineMonitor, mock_kube: MagicMock):
        """Verify that multiple imports are processed correctly."""
        ref1, _ = monitor.db.add_item("quay.io/repo:tag1@sha256:aaa")
        ref2, _ = monitor.db.add_item("quay.io/repo:tag2@sha256:bbb")
        assert ref1.id is not None and ref2.id is not None

        monitor.db.update_status(ref1.id, ImportStatus.TRIGGERED, pipelinerun_name="pnc-import-1")
        monitor.db.update_status(ref2.id, ImportStatus.RUNNING, pipelinerun_name="pnc-import-2")

        def get_status_side_effect(name):
            if name == "pnc-import-1":
                return PipelineRunStatus(name=name, status="Unknown")  # Running
            elif name == "pnc-import-2":
                return PipelineRunStatus(name=name, status="True")  # Successful
            return None

        mock_kube.get_pipelinerun_status.side_effect = get_status_side_effect

        monitor.update_statuses()

        running = monitor.db.get_by_status(ImportStatus.RUNNING)
        awaiting = monitor.db.get_by_status(ImportStatus.AWAITING_RELEASE)
        assert len(running) == 1
        assert len(awaiting) == 1
