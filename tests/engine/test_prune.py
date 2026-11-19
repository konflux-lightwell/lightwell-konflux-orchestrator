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
from import_orchestrator.engine import PruneMonitor, RequestView
from import_orchestrator.models import ClusterResource, Fingerprint, Phase, PhaseStatus, Request


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


def _seed_success(db: WorkflowDatabase, ref: str) -> Request:
    request = _seed_request(db, ref)
    attempt = db.create_attempt(request.id)
    db.upsert_phase(
        attempt.id,
        Phase.INTAKE,
        PhaseStatus.SUCCESS,
        reference_type=ClusterResource.PIPELINE_RUN,
        reference_id=f"pr-{ref}",
    )
    db.upsert_phase(
        attempt.id,
        Phase.RELEASE,
        PhaseStatus.SUCCESS,
        reference_type=ClusterResource.RELEASE,
        reference_id=f"rel-{ref}",
    )
    return request


def _seed_failed(db: WorkflowDatabase, ref: str) -> Request:
    request = _seed_request(db, ref)
    attempt = db.create_attempt(request.id)
    db.upsert_phase(attempt.id, Phase.INTAKE, PhaseStatus.FAILED, error_message="boom")
    return request


@pytest.fixture
def db(tmp_path: Path):
    with WorkflowDatabase(tmp_path / "test.db") as database:
        yield database


@pytest.fixture
def mock_kube():
    kube = MagicMock(spec=KubeClient)
    kube.prune_request_resources.return_value = True
    return kube


@pytest.fixture
def monitor(db: WorkflowDatabase, mock_kube: MagicMock):
    view = RequestView(db, skip_release=False)
    return PruneMonitor(view=view, kube=mock_kube)


def _expected_fingerprint(request: Request) -> str:
    return Fingerprint.from_request(request).hash()


class TestPruneCompleted:
    def test_prunes_successful_request_by_fingerprint(self, monitor, db, mock_kube):
        request = _seed_success(db, "r1")

        assert monitor.prune_completed() == 1

        mock_kube.prune_request_resources.assert_called_once_with(_expected_fingerprint(request))
        assert db.get_request(request.id).pruned_at is not None

    def test_skips_running_request(self, monitor, db, mock_kube):
        _seed_running(db, "r1")

        assert monitor.prune_completed() == 0
        mock_kube.prune_request_resources.assert_not_called()

    def test_skips_failed_request(self, monitor, db, mock_kube):
        _seed_failed(db, "r1")

        assert monitor.prune_completed() == 0
        mock_kube.prune_request_resources.assert_not_called()

    def test_does_not_reprune_already_pruned_request(self, monitor, db, mock_kube):
        _seed_success(db, "r1")

        assert monitor.prune_completed() == 1
        # A second pass over the same completed request issues no further deletes.
        assert monitor.prune_completed() == 0
        assert mock_kube.prune_request_resources.call_count == 1

    def test_leaves_request_unmarked_when_prune_fails(self, monitor, db, mock_kube):
        request = _seed_success(db, "r1")
        mock_kube.prune_request_resources.return_value = False

        assert monitor.prune_completed() == 0
        assert db.get_request(request.id).pruned_at is None
        # Next cycle retries because the request is still unmarked.
        mock_kube.prune_request_resources.return_value = True
        assert monitor.prune_completed() == 1
        assert db.get_request(request.id).pruned_at is not None

    def test_prunes_only_completed_requests_in_a_mixed_batch(self, monitor, db, mock_kube):
        done = _seed_success(db, "done")
        _seed_running(db, "busy")
        _seed_failed(db, "bad")

        assert monitor.prune_completed() == 1
        mock_kube.prune_request_resources.assert_called_once_with(_expected_fingerprint(done))

    def test_respects_request_scope(self, db, mock_kube):
        in_scope = _seed_success(db, "in")
        out_of_scope = _seed_success(db, "out")
        view = RequestView(db, skip_release=False, request_ids={in_scope.id})
        monitor = PruneMonitor(view=view, kube=mock_kube)

        assert monitor.prune_completed() == 1
        mock_kube.prune_request_resources.assert_called_once_with(_expected_fingerprint(in_scope))
        assert db.get_request(out_of_scope.id).pruned_at is None


class TestSkipReleasePruning:
    def test_prunes_skip_release_request_after_integration_tests(self, db, mock_kube):
        """A skip-release target is terminal-success once its ITS phase passes."""
        request = _seed_request(db, "scratch")
        attempt = db.create_attempt(request.id)
        db.upsert_phase(
            attempt.id,
            Phase.INTAKE,
            PhaseStatus.SUCCESS,
            reference_type=ClusterResource.PIPELINE_RUN,
            reference_id="pr-scratch",
        )
        db.upsert_phase(attempt.id, Phase.INTEGRATION_TESTS, PhaseStatus.SUCCESS)

        view = RequestView(db, skip_release=True)
        monitor = PruneMonitor(view=view, kube=mock_kube)

        assert monitor.prune_completed() == 1
        mock_kube.prune_request_resources.assert_called_once_with(_expected_fingerprint(request))
