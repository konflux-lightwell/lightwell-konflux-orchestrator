"""Regression tests for durable promotion ownership."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

from import_orchestrator.database import ImportDatabase
from import_orchestrator.engine.release import ReleasePrimitive
from import_orchestrator.models import ReleaseLookup, ReleaseLookupState


def test_promotion_creation_ownership_is_atomic_across_two_connections(tmp_path: Path):
    path = tmp_path / "shared.db"
    barrier = threading.Barrier(2)
    created: list[str] = []
    results: list[tuple[str | None, bool]] = []

    def worker(label: str):
        kube = MagicMock()
        kube.lookup_release_for_snapshot.return_value = ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY)

        def create(*_args):
            created.append(label)
            return f"release-{label}"

        kube.create_release.side_effect = create
        with ImportDatabase(path) as db:
            primitive = ReleasePrimitive(db, kube, "prefix-", 1)
            barrier.wait()
            results.append(primitive.promote_snapshot("snap", "plan"))

    threads = [threading.Thread(target=worker, args=(label,)) for label in ("one", "two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(created) == 1
    # The losing connection may observe the created durable name and adopt it;
    # the invariant is one remote creator, not one successful caller.
    assert all(name == f"release-{created[0]}" for name, _adopted in results)
    with ImportDatabase(path) as db:
        state = db.get_promotion_release("snap", "plan")
    assert state == (f"release-{created[0]}", False)
