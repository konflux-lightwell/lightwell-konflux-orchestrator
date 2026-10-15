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

import pytest

from import_orchestrator.database import WorkflowDatabase
from import_orchestrator.engine import RequestView
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


@pytest.fixture
def db(tmp_path: Path):
    with WorkflowDatabase(tmp_path / "test.db") as database:
        yield database


@pytest.fixture
def view(db: WorkflowDatabase):
    return RequestView(db, skip_release=False)


def _pending(db: WorkflowDatabase, ref: str) -> Request:
    request, _ = db.get_or_create_request(**_identity(ref))
    return request


def _running(db: WorkflowDatabase, ref: str) -> Request:
    request = _pending(db, ref)
    attempt = db.create_attempt(request.id)
    db.upsert_phase(
        attempt.id, Phase.INTAKE, PhaseStatus.RUNNING, reference_type=ClusterResource.PIPELINE_RUN, reference_id=ref
    )
    return request


def _failed(db: WorkflowDatabase, ref: str, attempts: int = 1) -> Request:
    request = _pending(db, ref)
    for _ in range(attempts):
        attempt = db.create_attempt(request.id)
        db.upsert_phase(attempt.id, Phase.INTAKE, PhaseStatus.FAILED, error_message="boom")
    return request


class TestState:
    def test_pending(self, db, view):
        assert view.state(_pending(db, "r1")) == RequestState.PENDING

    def test_running(self, db, view):
        assert view.state(_running(db, "r1")) == RequestState.RUNNING

    def test_failed(self, db, view):
        assert view.state(_failed(db, "r1")) == RequestState.FAILED


class TestSelectors:
    def test_pending_lists_only_unattempted(self, db, view):
        _pending(db, "p1")
        _running(db, "r1")
        pending = view.pending()
        assert [r.artifact for r in pending] == ["p1"]

    def test_in_flight_count(self, db, view):
        _running(db, "r1")
        _running(db, "r2")
        _pending(db, "p1")
        assert view.in_flight_count() == 2

    def test_retry_candidates_respect_max_retries(self, db, view):
        _failed(db, "retriable", attempts=1)
        _failed(db, "exhausted", attempts=4)  # 4 > max_retries=3
        candidates = view.retry_candidates(max_retries=3)
        assert [r.artifact for r in candidates] == ["retriable"]

    def test_statistics(self, db, view):
        _pending(db, "p1")
        _running(db, "r1")
        _failed(db, "f1", attempts=4)
        stats = view.statistics()
        assert stats == {"pending": 1, "running": 1, "releasing": 0, "success": 0, "failed": 1}


class TestIsComplete:
    def test_empty_is_complete(self, view):
        assert view.is_complete(max_retries=3) is True

    def test_pending_not_complete(self, db, view):
        _pending(db, "p1")
        assert view.is_complete(max_retries=3) is False

    def test_retriable_failure_not_complete(self, db, view):
        _failed(db, "f1", attempts=1)
        assert view.is_complete(max_retries=3) is False

    def test_exhausted_failure_is_complete(self, db, view):
        _failed(db, "f1", attempts=4)
        assert view.is_complete(max_retries=3) is True


class TestSkipRelease:
    def test_intake_success_is_terminal_when_skip_release(self, db):
        view = RequestView(db, skip_release=True)
        request = _pending(db, "r1")
        attempt = db.create_attempt(request.id)
        db.upsert_phase(
            attempt.id,
            Phase.INTAKE,
            PhaseStatus.SUCCESS,
            reference_type=ClusterResource.PIPELINE_RUN,
            reference_id="pr-1",
        )
        assert view.state(request) == RequestState.SUCCESS
        assert view.is_complete(max_retries=3) is True
