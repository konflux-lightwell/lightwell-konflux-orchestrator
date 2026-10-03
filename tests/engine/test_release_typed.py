from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import pytest

from import_orchestrator.database import ImportDatabase
from import_orchestrator.engine import ReleaseMonitor
from import_orchestrator.models import ImportItem, ImportStatus, ReleaseLookup, ReleaseLookupState


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


def test_release_primitive_validation_and_tracked():
    from import_orchestrator.engine.release import ReleasePrimitive

    with pytest.raises(ValueError, match="max_parallel must be positive"):
        ReleasePrimitive(None, None, "pfx", 0)

    # snapshot method edge cases
    mock_kube = MagicMock()
    mock_db = MagicMock()
    prim = ReleasePrimitive(mock_db, mock_kube, "pfx", 2)
    # Already has snapshot_name
    item = ImportItem(id=1, ref="r", status=ImportStatus.AWAITING_RELEASE, snapshot_name="cached-snap")
    assert prim.snapshot(item) == "cached-snap"
    # No pipelinerun
    item_no_plr = ImportItem(id=1, ref="r", status=ImportStatus.AWAITING_RELEASE)
    assert prim.snapshot(item_no_plr) is None
    # No id
    item_no_id = ImportItem(id=None, ref="r", status=ImportStatus.AWAITING_RELEASE, pipelinerun_name="plr")
    assert prim.snapshot(item_no_id) is None

    # snapshot found in dry_run (doesn't update db)
    item_plr = ImportItem(id=1, ref="r", status=ImportStatus.AWAITING_RELEASE, pipelinerun_name="plr")
    mock_kube.find_snapshot_by_pipelinerun.return_value = "new-snap"
    assert prim.snapshot(item_plr, dry_run=True) == "new-snap"
    mock_db.update_status.assert_not_called()

    # reconcile_tracked edge cases
    assert prim.reconcile_tracked(ImportItem(id=None, ref="r", status=ImportStatus.AWAITING_RELEASE)) is None
    assert prim.reconcile_tracked(ImportItem(id=1, ref="r", status=ImportStatus.AWAITING_RELEASE)) is None

    item_tracked = ImportItem(id=1, ref="r", status=ImportStatus.AWAITING_RELEASE, release_name="rel-1")
    # dry run
    assert prim.reconcile_tracked(item_tracked, dry_run=True) is True

    # status == "True" -> success
    mock_kube.get_release_status.return_value = "True"
    assert prim.reconcile_tracked(item_tracked) is True
    mock_db.update_status.assert_called_with(
        1, ImportStatus.SUCCESS, release_name="rel-1", completed_at=ANY, clear_error_message=True
    )

    # status not in ("False", "True") (e.g. "Unknown" or None) -> returns False
    mock_kube.get_release_status.return_value = "Unknown"
    assert prim.reconcile_tracked(item_tracked) is False

    # status == "False" with capacity full
    mock_kube.get_release_status.return_value = "False"
    mock_db.count_in_flight.return_value = 5
    assert prim.reconcile_tracked(item_tracked) is False

    # status == "False" with capacity available -> retires pointer
    mock_db.count_in_flight.return_value = 0
    assert prim.reconcile_tracked(item_tracked) is False
    assert item_tracked.release_name is None
    mock_db.update_status.assert_called_with(
        1,
        ImportStatus.AWAITING_RELEASE,
        release_name="",
        error_message="Release rel-1 failed; retrying with a new Release",
    )


def test_release_primitive_reconcile_edge_cases(tmp_path):
    from import_orchestrator.engine.release import ReleasePrimitive

    path = tmp_path / "edge.db"
    db, _ = make_db(path)
    kube = ProductionShapedKube([ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY)])
    prim = ReleasePrimitive(db, kube, "pfx", 2)

    # item id is None
    assert prim.reconcile(ImportItem(id=None, ref="r", status=ImportStatus.AWAITING_RELEASE)) is False

    item = db.get_by_status(ImportStatus.AWAITING_RELEASE)[0]
    # snapshot not found (clear item.snapshot_name)
    item.snapshot_name = None
    with patch.object(kube, "find_snapshot_by_pipelinerun", return_value=None):
        assert prim.reconcile(item) is False

    # plan discovery
    item.snapshot_name = "snap-1"
    kube.lookups = iter([ReleaseLookup(ReleaseLookupState.UNKNOWN)])
    # lookup UNKNOWN returns False
    assert prim.reconcile(item) is False

    # Existing cached pointer with status None
    item.release_name = "rel-old"
    kube.lookups = iter([ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY)])
    with patch.object(kube, "get_release_status", return_value=None):
        assert prim.reconcile(item) is False

    # Cached pointer terminally failed with capacity full
    with (
        patch.object(kube, "get_release_status", return_value="False"),
        patch.object(db, "count_in_flight", return_value=10),
    ):
        assert prim.reconcile(item) is False

    # Discovered existing release terminally failed with capacity full
    kube.lookups = iter([ReleaseLookup(ReleaseLookupState.FOUND, "rel-disc")])
    with (
        patch.object(kube, "get_release_status", return_value="False"),
        patch.object(db, "count_in_flight", return_value=10),
    ):
        assert prim.reconcile(item) is False

    # Discovered existing release status is None
    kube.lookups = iter([ReleaseLookup(ReleaseLookupState.FOUND, "rel-disc")])
    with patch.object(kube, "get_release_status", return_value=None):
        assert prim.reconcile(item) is False

    # release_creation_pending is True -> blocked
    kube.lookups = iter([ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY)])
    item.release_name = None
    item.release_creation_pending = True
    assert prim.reconcile(item) is False

    # Capacity full for new release creation
    item.release_creation_pending = False
    kube.lookups = iter([ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY)])
    with patch.object(db, "count_in_flight", return_value=10):
        assert prim.reconcile(item) is False

    # Dry run creation
    kube.lookups = iter([ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY)])
    with patch.object(db, "count_in_flight", return_value=0):
        assert prim.reconcile(item, plan="test-plan", dry_run=True) is True

    # Failed slot claim
    kube.lookups = iter([ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY)])
    with patch.object(db, "claim_release_slot", return_value=False):
        assert prim.reconcile(item, plan="test-plan") is False

    # Ambiguous create failure -> recovers adopted
    with patch.object(db, "claim_release_slot", return_value=True):
        kube.lookups = iter(
            [
                ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY),
                ReleaseLookup(ReleaseLookupState.FOUND, "recovered-rel"),
            ]
        )
        with patch.object(kube, "create_release", side_effect=Exception("network drop")):
            assert prim.reconcile(item, plan="test-plan") is True

    # Ambiguous create failure -> remains ambiguous (not found)
    with patch.object(db, "claim_release_slot", return_value=True):
        kube.lookups = iter(
            [
                ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY),
                ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY),
            ]
        )
        with patch.object(kube, "create_release", side_effect=Exception("network drop")):
            assert prim.reconcile(item, plan="test-plan") is False


def test_promote_snapshot_and_monitor_compat():
    from import_orchestrator.engine.release import ReleaseMonitor, ReleasePrimitive

    kube = ProductionShapedKube([ReleaseLookup(ReleaseLookupState.FOUND, "prom-rel")])
    prim = ReleasePrimitive(MagicMock(), kube, "pfx", 2)

    # promote_snapshot found
    rel, adopted = prim.promote_snapshot("snap-1", "plan-1")
    assert rel == "prom-rel" and adopted is True

    # promote_snapshot unknown
    kube.lookups = iter([ReleaseLookup(ReleaseLookupState.UNKNOWN)])
    rel, adopted = prim.promote_snapshot("snap-1", "plan-1")
    assert rel is None and adopted is False

    # promote_snapshot create
    kube.lookups = iter([ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY)])
    kube.created = "new-prom"
    rel, adopted = prim.promote_snapshot("snap-1", "plan-1")
    assert rel == "new-prom" and adopted is False

    # ReleaseMonitor compatibility methods
    mon = ReleaseMonitor(MagicMock(), kube, 2, "pfx")
    with patch.object(mon.primitive, "snapshot", return_value="snap-x"):
        assert mon._discover_snapshot(MagicMock(), "tag") is True
    with patch.object(mon.primitive, "reconcile", return_value=True):
        item = ImportItem(id=1, ref="r", status=ImportStatus.AWAITING_RELEASE, release_name="rel-z")
        assert mon._find_or_create_release(item, "tag") == "rel-z"
        assert mon._check_release_completion(item, "tag") is True


def test_release_primitive_record_branches(tmp_path):
    from import_orchestrator.engine.release import ReleasePrimitive

    path = tmp_path / "record.db"
    db, _ = make_db(path)
    kube = ProductionShapedKube([])
    prim = ReleasePrimitive(db, kube, "pfx", 2)
    item = db.get_by_status(ImportStatus.AWAITING_RELEASE)[0]

    # status True
    with patch.object(kube, "get_release_status", return_value="True"):
        assert prim._record(item, "rel-1", dry_run=False, adopted=True) is True
        assert db.get_by_status(ImportStatus.SUCCESS)[0].release_name == "rel-1"

    # status False
    with patch.object(kube, "get_release_status", return_value="False"):
        assert prim._record(item, "rel-1", dry_run=False, adopted=True) is True
        assert db.get_by_status(ImportStatus.AWAITING_RELEASE)[0].release_name is None

    # status None
    with patch.object(kube, "get_release_status", return_value=None):
        assert prim._record(item, "rel-1", dry_run=False, adopted=True) is False

    # status progressing
    with patch.object(kube, "get_release_status", return_value="Unknown"):
        assert prim._record(item, "rel-1", dry_run=False, adopted=True) is True


def test_legacy_kube_adapter_compatibility(tmp_path):
    from import_orchestrator.engine.release import ReleasePrimitive

    path = tmp_path / "legacy.db"
    db, _ = make_db(path)

    class LegacyKube:
        def __init__(self):
            self.created = "rel-leg"

        def find_snapshot_by_pipelinerun(self, name):
            return "snap-leg"

        def find_release_for_snapshot(self, snapshot):
            return None

        def find_release_for_snapshot_and_plan(self, snapshot, plan):
            return "existing-leg"

        def create_release(self, snapshot, plan, prefix):
            return self.created

        def get_release_status(self, release):
            return "Unknown"

    kube = LegacyKube()
    prim = ReleasePrimitive(db, kube, "pfx", 2)
    item = db.get_by_status(ImportStatus.AWAITING_RELEASE)[0]
    item.snapshot_name = "snap-leg"

    # Legacy find_release_for_snapshot_and_plan returns existing
    assert prim.reconcile(item, plan="plan-leg") is True
    assert item.release_name == "existing-leg"

    # Legacy promote_snapshot
    rel, adopted = prim.promote_snapshot("snap-leg", "plan-leg")
    assert rel == "existing-leg" and adopted is True

    # Durable promotion state keeps the prior pointer even if a later lookup
    # is empty, avoiding a duplicate Release after an ambiguous restart.
    kube.find_release_for_snapshot_and_plan = lambda snap, plan: None
    rel, adopted = prim.promote_snapshot("snap-leg", "plan-leg")
    assert rel == "existing-leg" and adopted is True


def test_discovered_failed_release_retirement(tmp_path):
    from import_orchestrator.engine.release import ReleasePrimitive

    path = tmp_path / "retire.db"
    db, _ = make_db(path)
    kube = ProductionShapedKube([ReleaseLookup(ReleaseLookupState.FOUND, "rel-failed")])
    prim = ReleasePrimitive(db, kube, "pfx", 2)
    item = db.get_by_status(ImportStatus.AWAITING_RELEASE)[0]
    item.snapshot_name = "snap-1"

    # Discovered release has status "False" and capacity is available -> retires and creates new
    with (
        patch.object(kube, "get_release_status", side_effect=["False", "Unknown"]),
        patch.object(kube, "create_release", return_value="rel-replacement"),
        patch.object(db, "count_in_flight", return_value=0),
    ):
        assert prim.reconcile(item, plan="plan-1") is True
        assert item.release_name == "rel-replacement"


def test_stale_cached_release_handling(tmp_path):
    from import_orchestrator.engine.release import ReleasePrimitive

    path = tmp_path / "stale.db"
    db, _ = make_db(path)
    kube = ProductionShapedKube([ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY)])
    prim = ReleasePrimitive(db, kube, "pfx", 2)
    item = db.get_by_status(ImportStatus.AWAITING_RELEASE)[0]
    item.snapshot_name = "snap-1"
    item.release_name = "rel-stale"

    # Cached name has status "False" when existing is None -> clears release_name and creates new
    with (
        patch.object(kube, "get_release_status", return_value="False"),
        patch.object(kube, "create_release", return_value="rel-new"),
        patch.object(db, "count_in_flight", return_value=0),
    ):
        assert prim.reconcile(item, plan="plan-1") is True
        assert item.release_name == "rel-new"


def test_discovered_terminal_failure_retries_same_completed_import(tmp_path):
    """The newest failed attempt is retired, not converted into a retryable import failure."""
    db, _ = make_db(tmp_path / "newest-failed.db")
    kube = ProductionShapedKube(
        [ReleaseLookup(ReleaseLookupState.FOUND, "new-failed")], created="replacement", status="False"
    )
    try:
        ReleaseMonitor(db, kube, max_parallel=1, prefix="import-").update_statuses()
        item = db.get_by_status(ImportStatus.AWAITING_RELEASE)[0]
        assert item.release_name == "replacement"
        assert db.get_by_status(ImportStatus.FAILED) == []
        assert kube.pipeline_calls == 0
    finally:
        db.__exit__(None, None, None)


def test_promotion_ambiguous_create_survives_restart_without_duplicate(tmp_path):
    from import_orchestrator.engine.release import ReleasePrimitive

    path = tmp_path / "promotion.db"
    with ImportDatabase(path) as db:
        kube = ProductionShapedKube([ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY)], created=None)
        assert ReleasePrimitive(db, kube, "pfx", 1).promote_snapshot("snap", "plan") == (None, False)
        assert len(kube.create_calls) == 1
    with ImportDatabase(path) as db:
        kube = ProductionShapedKube([ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY)], created="must-not-create")
        assert ReleasePrimitive(db, kube, "pfx", 1).promote_snapshot("snap", "plan") == (None, False)
        assert kube.create_calls == []
