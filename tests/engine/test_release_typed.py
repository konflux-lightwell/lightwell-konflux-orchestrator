from pathlib import Path

import pytest

from import_orchestrator.database import ImportDatabase
from import_orchestrator.engine import ReleaseMonitor
from import_orchestrator.models import ImportStatus, ReleaseLookup, ReleaseLookupState


class ProductionShapedKube:
    """Small adapter with the same typed surface as the production KubeClient."""

    def __init__(self, lookups, created=None, status="Unknown"):
        self.lookups = iter(lookups)
        self.created = created
        self.status = status
        self.create_calls = []
        self.pipeline_calls = 0

    def find_snapshot_by_pipelinerun(self, name):
        return "snapshot-1"

    def lookup_release_for_snapshot(self, snapshot, release_plan=None):
        return next(self.lookups)

    def find_release_plan_for_snapshot(self, snapshot):
        return "plan-1"

    def create_release(self, snapshot, plan, prefix):
        self.create_calls.append((snapshot, plan, prefix))
        return self.created

    def get_release_status(self, release):
        return self.status

    def create_pipelinerun(self, *args, **kwargs):
        self.pipeline_calls += 1


def make_db(path: Path):
    db = ImportDatabase(path)
    db.__enter__()
    item, _ = db.add_item("quay.io/example/image@sha256:" + "a" * 64)
    assert item.id is not None
    db.update_status(
        item.id,
        ImportStatus.AWAITING_RELEASE,
        pipelinerun_name="build-1",
        snapshot_name="snapshot-1",
    )
    return db, item.id


def test_production_shaped_typed_empty_creates_release(tmp_path):
    db, item_id = make_db(tmp_path / "typed.db")
    kube = ProductionShapedKube([ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY)], created="release-1")
    try:
        ReleaseMonitor(db, kube, max_parallel=1, prefix="import-").update_statuses()
        assert kube.create_calls == [("snapshot-1", "plan-1", "import-")]
        assert db.get_by_status(ImportStatus.AWAITING_RELEASE)[0].release_name == "release-1"
    finally:
        db.__exit__(None, None, None)


@pytest.mark.parametrize("state", list(ReleaseLookupState))
def test_create_recovery_is_conservative_for_each_lookup_state(tmp_path, state):
    db, item_id = make_db(tmp_path / f"{state.value}.db")
    kube = ProductionShapedKube(
        [
            ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY),
            ReleaseLookup(state, "found" if state is ReleaseLookupState.FOUND else None),
        ],
        created=None,
    )
    try:
        ReleaseMonitor(db, kube, max_parallel=1, prefix="import-").update_statuses()
        item = db.get_by_status(ImportStatus.AWAITING_RELEASE)[0]
        if state is ReleaseLookupState.FOUND:
            assert item.release_name == "found"
            assert not item.release_creation_pending
        else:
            assert item.release_name is None
            assert item.release_creation_pending
        assert len(db.get_release_attempts(item_id)) == 1
    finally:
        db.__exit__(None, None, None)


def test_pending_marker_survives_database_reopen(tmp_path):
    path = tmp_path / "reopen.db"
    db, _ = make_db(path)
    kube = ProductionShapedKube(
        [ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY), ReleaseLookup(ReleaseLookupState.UNKNOWN)], created=None
    )
    ReleaseMonitor(db, kube, max_parallel=1, prefix="import-").update_statuses()
    db.__exit__(None, None, None)

    reopened = ImportDatabase(path)
    reopened.__enter__()
    try:
        item = reopened.get_by_status(ImportStatus.AWAITING_RELEASE)[0]
        assert item.release_creation_pending
        kube2 = ProductionShapedKube([ReleaseLookup(ReleaseLookupState.UNKNOWN)], created="should-not-create")
        ReleaseMonitor(reopened, kube2, max_parallel=1, prefix="import-").update_statuses()
        assert not kube2.create_calls
    finally:
        reopened.__exit__(None, None, None)


def test_terminal_failed_release_is_replaced(tmp_path):
    db, _ = make_db(tmp_path / "failed.db")
    item = db.get_by_status(ImportStatus.AWAITING_RELEASE)[0]
    db.update_status(item.id, ImportStatus.AWAITING_RELEASE, release_name="failed-release")
    kube = ProductionShapedKube(
        [ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY)], created="replacement", status="False"
    )
    try:
        ReleaseMonitor(db, kube, max_parallel=1, prefix="import-").update_statuses()
        assert kube.create_calls
        assert db.get_by_status(ImportStatus.AWAITING_RELEASE)[0].release_name == "replacement"
    finally:
        db.__exit__(None, None, None)


def test_release_reconciliation_never_creates_pipeline_run(tmp_path):
    db, _ = make_db(tmp_path / "release-only.db")
    kube = ProductionShapedKube([ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY)], created="release-1")
    try:
        ReleaseMonitor(db, kube, max_parallel=1, prefix="import-").update_statuses()
        assert kube.pipeline_calls == 0
    finally:
        db.__exit__(None, None, None)
