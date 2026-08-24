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
from unittest.mock import patch

from import_orchestrator.cli import main, make_parser
from import_orchestrator.constants import DEFAULT_MAX_PARALLEL


class TestOrchestrateArgParsing:
    def test_defaults(self):
        parser = make_parser()
        args = parser.parse_args(["python", "orchestrate"])
        assert args.command == "orchestrate"
        assert args.max_parallel == DEFAULT_MAX_PARALLEL

    def test_max_parallel_override(self):
        parser = make_parser()
        args = parser.parse_args(["python", "orchestrate", "--max-parallel", "5"])
        assert args.max_parallel == 5

    def test_no_artifact_type_flag(self):
        parser = make_parser()
        args = parser.parse_args(["python", "orchestrate"])
        assert not hasattr(args, "artifact_type")

    def test_target_default(self, monkeypatch):
        monkeypatch.delenv("LIGHTWELL_PYTHON_TARGET", raising=False)
        parser = make_parser()
        args = parser.parse_args(["python", "orchestrate"])
        assert args.target == "REMEDIATED"


class TestOrchestrateEmptyDbWarning:
    def test_empty_database_prints_python_warning(self, monkeypatch, tmp_path: Path, capsys):
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--db", str(tmp_path / "test.db"), "python", "orchestrate"],
        )

        orchestrator_mod = __import__("import_orchestrator.engine.orchestrator", fromlist=["ImportOrchestrator"])
        with (
            patch("import_orchestrator.commands.orchestrate.KubeClient"),
            patch.object(orchestrator_mod.ImportOrchestrator, "run_until_complete", return_value=0),
        ):
            exit_code = main()

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "No package references in database" in captured.err
        assert "python import-file" in captured.err
