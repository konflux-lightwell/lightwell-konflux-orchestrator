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

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from import_orchestrator.commands.run import run_single
from import_orchestrator.database import ImportDatabase
from import_orchestrator.engine import ImportOrchestrator
from import_orchestrator.models import ImportStatus

_REF = "ntplib==0.4.0"


def _args(**overrides):
    eco = MagicMock()
    eco.namespace = "lightwell-tenant"
    eco.pipelinerun_prefix = "prefix"
    eco.build_pipelinerun.return_value = {"kind": "PipelineRun"}
    base = dict(ecosystem=eco, max_retries=3, poll_interval=30, db_explicit=False, db=None, output_json=None)
    base.update(overrides)
    return argparse.Namespace(**base)


def _item_count(db_path: Path) -> int:
    with ImportDatabase(db_path) as db:
        return sum(db.get_statistics().values())


def _make_success(**fields):
    """Return a fake run_until_complete that marks the seeded ref SUCCESS."""

    def fake_run(self):
        item = self.db.get_by_ref(_REF)
        self.db.update_status(item.id, ImportStatus.SUCCESS, **fields)
        return 0

    return fake_run


def _make_failure(error_message):
    """Return a fake run_until_complete that marks the seeded ref FAILED."""

    def fake_run(self):
        item = self.db.get_by_ref(_REF)
        self.db.update_status(item.id, ImportStatus.FAILED, error_message=error_message)
        return 1

    return fake_run


class TestEphemeralDatabase:
    def test_seeds_single_ref_then_cleans_up(self, tmp_path, monkeypatch):
        eph_dir = tmp_path / "eph"
        monkeypatch.setattr("tempfile.mkdtemp", lambda *a, **k: str(eph_dir))

        captured: dict = {}

        def fake_run(self):
            # Capture DB state mid-run, before the ephemeral dir is removed.
            captured["items"] = sum(self.db.get_statistics().values())
            return 0

        with (
            patch("import_orchestrator.commands.run.KubeClient"),
            patch.object(ImportOrchestrator, "run_until_complete", fake_run),
        ):
            rc = run_single(_args(), "ntplib==0.4.0")

        assert rc == 0
        assert captured["items"] == 1  # exactly the one seeded ref
        assert not eph_dir.exists()  # ephemeral DB removed afterward

    def test_exit_code_propagates(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tempfile.mkdtemp", lambda *a, **k: str(tmp_path / "eph"))
        with (
            patch("import_orchestrator.commands.run.KubeClient"),
            patch.object(ImportOrchestrator, "run_until_complete", lambda self: 1),
        ):
            rc = run_single(_args(), "ntplib==0.4.0")
        assert rc == 1


class TestPersistentDatabase:
    def test_explicit_db_seeded_and_retained(self, tmp_path):
        db_path = tmp_path / "ct-123.db"

        with (
            patch("import_orchestrator.commands.run.KubeClient"),
            patch.object(ImportOrchestrator, "run_until_complete", lambda self: 0),
        ):
            rc = run_single(_args(db_explicit=True, db=db_path), "ntplib==0.4.0")

        assert rc == 0
        assert db_path.exists()  # persistent DB is not deleted
        assert _item_count(db_path) == 1  # the single ref was seeded

    def test_resume_dedups_existing_ref(self, tmp_path):
        db_path = tmp_path / "ct-123.db"

        for _ in range(2):
            with (
                patch("import_orchestrator.commands.run.KubeClient"),
                patch.object(ImportOrchestrator, "run_until_complete", lambda self: 0),
            ):
                run_single(_args(db_explicit=True, db=db_path), "ntplib==0.4.0")

        assert _item_count(db_path) == 1  # second run does not duplicate the ref


class TestResultPayload:
    def test_success_payload_to_stdout(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("tempfile.mkdtemp", lambda *a, **k: str(tmp_path / "eph"))
        fake = _make_success(
            pipelinerun_name="python-remediated-build-abc",
            snapshot_name="snapshot-xyz",
            release_name="release-123",
        )
        with (
            patch("import_orchestrator.commands.run.KubeClient"),
            patch.object(ImportOrchestrator, "run_until_complete", fake),
        ):
            rc = run_single(_args(), _REF)

        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload == {
            "ref": _REF,
            "status": "success",
            "pipelinerun_name": "python-remediated-build-abc",
            "snapshot_name": "snapshot-xyz",
            "release_name": "release-123",
            "error_message": None,
            "retry_count": 0,
        }

    def test_failure_payload_to_stdout(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("tempfile.mkdtemp", lambda *a, **k: str(tmp_path / "eph"))
        with (
            patch("import_orchestrator.commands.run.KubeClient"),
            patch.object(ImportOrchestrator, "run_until_complete", _make_failure("pipeline exploded")),
        ):
            rc = run_single(_args(), _REF)

        assert rc == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "failed"
        assert payload["error_message"] == "pipeline exploded"
        assert payload["ref"] == _REF

    def test_output_json_writes_matching_file(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("tempfile.mkdtemp", lambda *a, **k: str(tmp_path / "eph"))
        out_path = tmp_path / "result.json"
        fake = _make_success(pipelinerun_name="pr-1", snapshot_name="snap-1", release_name="rel-1")
        with (
            patch("import_orchestrator.commands.run.KubeClient"),
            patch.object(ImportOrchestrator, "run_until_complete", fake),
        ):
            run_single(_args(output_json=str(out_path)), _REF)

        stdout_payload = json.loads(capsys.readouterr().out)
        assert out_path.exists()
        assert json.loads(out_path.read_text()) == stdout_payload

    def test_payload_emitted_before_ephemeral_cleanup(self, tmp_path, monkeypatch, capsys):
        eph_dir = tmp_path / "eph"
        monkeypatch.setattr("tempfile.mkdtemp", lambda *a, **k: str(eph_dir))
        with (
            patch("import_orchestrator.commands.run.KubeClient"),
            patch.object(ImportOrchestrator, "run_until_complete", _make_success(pipelinerun_name="pr-9")),
        ):
            run_single(_args(), _REF)

        # The payload reflects state that only existed in the now-removed DB.
        payload = json.loads(capsys.readouterr().out)
        assert payload["pipelinerun_name"] == "pr-9"
        assert not eph_dir.exists()
