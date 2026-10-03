"""Java command wiring tests for source-scoped Snapshot reuse."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from import_orchestrator.cli import make_parser
from import_orchestrator.commands.orchestrate import run_orchestrate
from import_orchestrator.commands.run import run_single
from import_orchestrator.engine import ImportOrchestrator
from import_orchestrator.models import SnapshotLookup, SnapshotLookupState

_REF = "quay.io/source:tag@sha256:" + "a" * 64


def test_java_parser_wires_source_scoped_snapshot_resolution_for_orchestrate(tmp_path):
    args = make_parser().parse_args(["--db", str(tmp_path / "state.db"), "java", "orchestrate"])
    captured = {}

    def fake_run(self):
        captured["resolver"] = self._trigger.import_snapshot_resolver
        return 0

    with (
        patch("import_orchestrator.commands.orchestrate.KubeClient"),
        patch.object(ImportOrchestrator, "run_until_complete", fake_run),
    ):
        assert run_orchestrate(args, "empty") == 0

    assert captured["resolver"] is True


def test_java_run_wires_source_scoped_resolution_and_confirmed_miss_creates_pipeline(tmp_path):
    args = make_parser().parse_args(["--db", str(tmp_path / "state.db"), "java", "run", _REF])
    captured = {}

    def fake_run(self):
        trigger = self._trigger
        captured["resolver"] = trigger.import_snapshot_resolver
        trigger.kube.find_snapshot_for_import.return_value = SnapshotLookup(SnapshotLookupState.CONFIRMED_EMPTY)
        trigger.kube.create_pipelinerun.return_value = "fresh-import"
        assert trigger.trigger_next_batch() == 1
        trigger.kube.find_snapshot_by_component_digest.assert_not_called()
        return 0

    kube = MagicMock()
    with (
        patch("import_orchestrator.commands.run.KubeClient", return_value=kube),
        patch.object(ImportOrchestrator, "run_until_complete", fake_run),
    ):
        assert run_single(args, _REF) == 0

    assert captured["resolver"] is True
    kube.create_pipelinerun.assert_called_once()
