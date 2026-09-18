import argparse
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from import_orchestrator.clients import KubeClient
from import_orchestrator.database import ImportDatabase
from import_orchestrator.ecosystems.java.ecosystem import JavaEcosystem
from import_orchestrator.engine.release_only import ReleaseOnly
from import_orchestrator.models import (
    ImportStatus,
    PipelineRunStatus,
    ReleaseLookup,
    ReleaseLookupState,
    SnapshotLookup,
    SnapshotLookupState,
)


@pytest.fixture
def db(tmp_path: Path):
    with ImportDatabase(tmp_path / "test.db") as database:
        yield database


def test_completed_running_import_resolves_snapshot_and_creates_release(db: ImportDatabase):
    """Release-only must reconcile a completed import without rerunning it."""
    kube = MagicMock(spec=KubeClient)
    ref, _ = db.add_item("quay.io/repo:tag@sha256:" + "a" * 64)
    assert ref.id is not None
    db.update_status(ref.id, ImportStatus.RUNNING, pipelinerun_name="import-1")

    kube.get_pipelinerun_status.return_value = PipelineRunStatus(name="import-1", status="True")
    kube.find_snapshot_by_pipelinerun.return_value = "snapshot-1"
    kube.get_snapshot_component_digests.return_value = {"sha256:" + "a" * 64}
    kube.lookup_release_for_snapshot.return_value = ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY)
    kube.create_release.return_value = "release-1"
    kube.get_release_status.return_value = "Unknown"

    result = ReleaseOnly(db, kube, "import-", 1, "plan-1").run()

    assert result == 1
    item = db.get_by_status(ImportStatus.AWAITING_RELEASE)[0]
    assert item.snapshot_name == "snapshot-1"
    assert item.release_name == "release-1"
    kube.create_release.assert_called_once_with("snapshot-1", "plan-1", "import-")
    kube.get_pipelinerun_status.assert_called_once_with("import-1")

    db.update_status(item.id, ImportStatus.AWAITING_RELEASE, release_name="")
    kube.lookup_release_for_snapshot.return_value = ReleaseLookup(ReleaseLookupState.FOUND, "release-1")
    kube.find_release_for_snapshot_and_plan.return_value = "release-1"
    kube.get_release_status.return_value = "True"
    assert ReleaseOnly(db, kube, "import-", 1, "plan-1").run() == 0
    assert len(db.get_by_status(ImportStatus.SUCCESS)) == 1
    kube.create_release.assert_called_once()


def test_pending_matching_snapshot_creates_release_without_pipeline_run(db: ImportDatabase):
    item, _ = db.add_item("quay.io/repo:tag@sha256:" + "a" * 64)
    kube = MagicMock(spec=KubeClient)
    kube.find_snapshot_by_component_digest.return_value = SnapshotLookup(SnapshotLookupState.FOUND, "snapshot-1")
    kube.get_snapshot_component_digests.return_value = {"sha256:" + "a" * 64}
    kube.lookup_release_for_snapshot.return_value = ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY)
    kube.create_release.return_value = "release-1"
    kube.get_release_status.return_value = "Unknown"

    assert ReleaseOnly(db, kube, "import-", 1, "plan-1", "app").run() == 1
    kube.find_snapshot_by_component_digest.assert_called_once_with("sha256:" + "a" * 64, "app")
    kube.create_pipelinerun.assert_not_called()
    assert db.get_by_status(ImportStatus.AWAITING_RELEASE)[0].release_name == "release-1"


@pytest.mark.parametrize(
    "state",
    [SnapshotLookupState.CONFIRMED_EMPTY, SnapshotLookupState.AMBIGUOUS, SnapshotLookupState.UNKNOWN],
)
def test_java_source_miss_never_falls_back_to_shared_component(db: ImportDatabase, state: SnapshotLookupState):
    item, _ = db.add_item("quay.io/repo:tag@sha256:" + "a" * 64)
    kube = MagicMock(spec=KubeClient)
    kube.find_snapshot_for_import.return_value = SnapshotLookup(state)
    kube.find_snapshot_by_component_digest.return_value = SnapshotLookup(
        SnapshotLookupState.FOUND, "shared-component-snapshot"
    )

    assert ReleaseOnly(db, kube, "import-", 1, "plan-1", "app", import_snapshot_resolver=True).run() == 1
    assert db.get_by_status(ImportStatus.PENDING)[0].id == item.id
    kube.find_snapshot_by_component_digest.assert_not_called()
    kube.create_release.assert_not_called()


@pytest.mark.parametrize(
    ("artifact_type", "expected_snapshot"),
    [("STAGE", None), ("REBUILD", "pnc-import-snapshot")],
)
def test_java_release_uses_artifact_application_for_source_lookup(
    db: ImportDatabase, artifact_type: str, expected_snapshot: str | None
):
    item, _ = db.add_item("quay.io/repo:tag@sha256:" + "a" * 64)
    kube = MagicMock(spec=KubeClient)

    def find_snapshot(source, application):
        assert source == item.ref
        return (
            SnapshotLookup(SnapshotLookupState.FOUND, expected_snapshot)
            if application == "pnc-import"
            else SnapshotLookup(SnapshotLookupState.CONFIRMED_EMPTY)
        )

    kube.find_snapshot_for_import.side_effect = find_snapshot
    kube.get_snapshot_component_digests.return_value = {"sha256:" + "a" * 64}
    kube.lookup_release_for_snapshot.return_value = ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY)
    kube.create_release.return_value = "release-1"
    kube.get_release_status.return_value = "Unknown"

    args = argparse.Namespace(artifact_type=artifact_type)
    application = JavaEcosystem().snapshot_application(args)
    result = ReleaseOnly(db, kube, "import-", 1, "plan-1", application, import_snapshot_resolver=True).run()

    assert kube.find_snapshot_for_import.call_args.args[1] == (
        "pnc-import" if artifact_type == "REBUILD" else "pnc-import-stage"
    )
    if expected_snapshot:
        assert result == 1
        assert db.get_by_status(ImportStatus.AWAITING_RELEASE)[0].snapshot_name == expected_snapshot
    else:
        assert result == 1
        assert db.get_by_status(ImportStatus.PENDING)[0].id == item.id
        kube.create_release.assert_not_called()


def test_java_source_match_uses_latest_import_snapshot(db: ImportDatabase):
    item, _ = db.add_item("quay.io/repo:tag@sha256:" + "a" * 64)
    kube = MagicMock(spec=KubeClient)
    kube.find_snapshot_for_import.return_value = SnapshotLookup(SnapshotLookupState.FOUND, "source-snapshot")
    kube.get_snapshot_component_digests.return_value = {"sha256:" + "a" * 64}
    kube.lookup_release_for_snapshot.return_value = ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY)
    kube.create_release.return_value = "release-1"
    kube.get_release_status.return_value = "Unknown"

    assert ReleaseOnly(db, kube, "import-", 1, "plan-1", "app", import_snapshot_resolver=True).run() == 1
    assert db.get_by_status(ImportStatus.AWAITING_RELEASE)[0].snapshot_name == "source-snapshot"
    kube.find_snapshot_by_component_digest.assert_not_called()
    kube.create_release.assert_called_once_with("source-snapshot", "plan-1", "import-")


def test_pending_snapshot_miss_or_ambiguity_remains_pending(db: ImportDatabase):
    for state in (SnapshotLookupState.CONFIRMED_EMPTY, SnapshotLookupState.UNKNOWN, SnapshotLookupState.AMBIGUOUS):
        item, _ = db.add_item(f"quay.io/repo:{state.value}@sha256:" + "b" * 64)
        kube = MagicMock(spec=KubeClient)
        kube.find_snapshot_by_component_digest.return_value = SnapshotLookup(state)
        assert ReleaseOnly(db, kube, "import-", 1, "plan-1", "app").run() == 1
        assert db.get_by_status(ImportStatus.PENDING)[-1].id == item.id
        kube.create_pipelinerun.assert_not_called()


def test_pending_does_not_consume_capacity(db: ImportDatabase):
    pending, _ = db.add_item("quay.io/repo:pending@sha256:" + "a" * 64)
    active, _ = db.add_item("quay.io/repo:active@sha256:" + "b" * 64)
    db.update_status(active.id, ImportStatus.RUNNING, pipelinerun_name="run")
    kube = MagicMock(spec=KubeClient)
    kube.get_pipelinerun_status.return_value = PipelineRunStatus("run", "Unknown")
    kube.find_snapshot_by_component_digest.return_value = SnapshotLookup(SnapshotLookupState.CONFIRMED_EMPTY)
    assert ReleaseOnly(db, kube, "import-", 1, "plan-1", "app").run() == 1
    assert db.get_by_status(ImportStatus.PENDING)[0].id == pending.id


def test_tracked_success_reconciles_before_snapshot_digest_validation(db: ImportDatabase):
    kube = MagicMock(spec=KubeClient)
    ref, _ = db.add_item("quay.io/repo:tag@sha256:" + "a" * 64)
    db.update_status(
        ref.id,
        ImportStatus.AWAITING_RELEASE,
        pipelinerun_name="import-1",
        snapshot_name="old",
        release_name="release-1",
    )
    kube.get_release_status.return_value = "True"
    assert ReleaseOnly(db, kube, "import-", 1, "plan-1").run() == 0
    kube.get_snapshot_component_digests.assert_not_called()


def test_tracked_failure_respects_capacity_before_retry(db: ImportDatabase):
    ref, _ = db.add_item("quay.io/repo:tag@sha256:" + "a" * 64)
    other, _ = db.add_item("quay.io/repo:other@sha256:" + "b" * 64)
    db.update_status(ref.id, ImportStatus.FAILED, release_name="release-1")
    db.update_status(other.id, ImportStatus.AWAITING_RELEASE, release_name="release-2")
    kube = MagicMock(spec=KubeClient)
    kube.get_release_status.return_value = "False"
    assert ReleaseOnly(db, kube, "import-", 1, "plan-1").run() == 1
    assert db.get_by_status(ImportStatus.FAILED)[0].release_name == "release-1"


def test_release_only_defer_branches(db: ImportDatabase):
    digest = "sha256:" + "a" * 64
    ref, _ = db.add_item("quay.io/repo:tag@" + digest)
    db.update_status(ref.id, ImportStatus.AWAITING_RELEASE, pipelinerun_name="pr-1")

    kube = MagicMock(spec=KubeClient)
    # snapshot not found
    kube.find_snapshot_by_pipelinerun.return_value = None
    assert ReleaseOnly(db, kube, "pfx", 1, "plan-1").run() == 1

    # snapshot found but digest mismatch
    kube.find_snapshot_by_pipelinerun.return_value = "snap-1"
    kube.get_snapshot_component_digests.return_value = {"other-digest"}
    assert ReleaseOnly(db, kube, "pfx", 1, "plan-1").run() == 1

    # snapshot found but no plan and no release_name
    kube.get_snapshot_component_digests.return_value = {digest}
    assert ReleaseOnly(db, kube, "pfx", 1, release_plan=None).run() == 1


def test_release_only_dynamically_resolves_unique_plan_without_import(db: ImportDatabase):
    item, _ = db.add_item("quay.io/repo:tag@sha256:" + "a" * 64)
    kube = MagicMock(spec=KubeClient)
    kube.find_snapshot_for_import.return_value = SnapshotLookup(SnapshotLookupState.FOUND, "snapshot-1")
    kube.get_snapshot_component_digests.return_value = {"sha256:" + "a" * 64}
    kube.find_release_plan_for_snapshot.return_value = "unique-plan"
    kube.lookup_release_for_snapshot.return_value = ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY)
    kube.create_release.return_value = "release-1"
    kube.get_release_status.return_value = "Unknown"

    assert ReleaseOnly(db, kube, "import-", 1, None, "app", import_snapshot_resolver=True).run() == 1
    assert db.get_by_status(ImportStatus.AWAITING_RELEASE)[0].release_plan == "unique-plan"
    kube.create_release.assert_called_once_with("snapshot-1", "unique-plan", "import-")
    kube.create_pipelinerun.assert_not_called()
