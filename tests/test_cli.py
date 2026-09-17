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

import pytest

from import_orchestrator.cli import main, make_parser
from import_orchestrator.clients.kube_api import KubeAuth
from import_orchestrator.constants import (
    DEFAULT_MAX_PARALLEL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_POLL_INTERVAL,
)
from import_orchestrator.database import ImportDatabase
from import_orchestrator.ecosystems.java import config as java_config
from import_orchestrator.models import ImportStatus


class TestParserEcosystem:
    def test_java_orchestrate_flags(self):
        parser = make_parser()
        args = parser.parse_args(["java", "orchestrate", "--max-parallel", "10"])
        assert args.max_parallel == 10

    def test_top_level_db_override_before_ecosystem(self):
        parser = make_parser()
        args = parser.parse_args(["--db", "/tmp/x.db", "java", "import-file", "refs.txt"])
        assert args.db == Path("/tmp/x.db")

    def test_reset_flag_before_ecosystem(self):
        parser = make_parser()
        args = parser.parse_args(["--reset", "java", "orchestrate"])
        assert args.reset is True

    def test_db_defaults_to_none(self):
        parser = make_parser()
        args = parser.parse_args(["java", "orchestrate"])
        assert args.db is None

    def test_unknown_ecosystem_rejected(self):
        parser = make_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["ruby", "fetch"])

    def test_ecosystem_object_is_shared_instance(self):
        parser = make_parser()
        args = parser.parse_args(["java", "import-file", "mock-file.txt"])
        assert args.ecosystem.default_db_path == java_config.JAVA_DEFAULT_DB_PATH


class TestParserOrchestrate:
    def test_default_values(self, monkeypatch):
        monkeypatch.delenv("LIGHTWELL_ARTIFACT_TYPE", raising=False)
        parser = make_parser()
        args = parser.parse_args(["java", "orchestrate"])
        assert args.max_parallel == DEFAULT_MAX_PARALLEL
        assert args.poll_interval == DEFAULT_POLL_INTERVAL
        assert args.max_retries == DEFAULT_MAX_RETRIES
        assert args.artifact_type == "STAGE"
        assert args.reset is False

    def test_all_flags_combined(self):
        parser = make_parser()
        args = parser.parse_args(
            [
                "--db",
                "/tmp/test.db",
                "java",
                "orchestrate",
                "--max-parallel",
                "8",
                "--poll-interval",
                "15",
                "--max-retries",
                "2",
            ]
        )
        assert args.db == Path("/tmp/test.db")
        assert args.max_parallel == 8
        assert args.poll_interval == 15
        assert args.max_retries == 2

    def test_artifact_type_flag(self, monkeypatch):
        monkeypatch.delenv("LIGHTWELL_ARTIFACT_TYPE", raising=False)
        parser = make_parser()
        args = parser.parse_args(["java", "orchestrate", "--artifact-type", "REBUILD"])
        assert args.artifact_type == "REBUILD"

    def test_artifact_type_from_env_var(self, monkeypatch):
        monkeypatch.setenv("LIGHTWELL_ARTIFACT_TYPE", "REMEDIATED")
        parser = make_parser()
        args = parser.parse_args(["java", "orchestrate"])
        assert args.artifact_type == "REMEDIATED"

    def test_invalid_artifact_type_rejected(self):
        parser = make_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["java", "orchestrate", "--artifact-type", "INVALID"])


class TestParserRelease:
    def test_help_and_defaults(self):
        parser = make_parser()
        args = parser.parse_args(["java", "release"])
        assert args.command == "release"
        assert args.max_parallel == DEFAULT_MAX_PARALLEL
        assert args.release_plan is None
        assert args.artifact_type == "STAGE"
        assert args.dry_run is False
        assert args.poll_interval is None

    def test_artifact_type_flag_and_env(self, monkeypatch):
        monkeypatch.setenv("LIGHTWELL_ARTIFACT_TYPE", "REBUILD")
        parser = make_parser()
        args = parser.parse_args(["java", "release"])
        assert args.artifact_type == "REBUILD"
        assert parser.parse_args(["java", "release", "--artifact-type", "STAGE"]).artifact_type == "STAGE"

    def test_explicit_plan_and_runtime_options(self):
        parser = make_parser()
        args = parser.parse_args(
            [
                "java",
                "release",
                "--release-plan",
                "custom-plan",
                "--dry-run",
                "--poll-interval",
                "12.5",
                "--max-parallel",
                "3",
            ]
        )
        assert args.release_plan == "custom-plan"
        assert args.dry_run is True
        assert args.poll_interval == 12.5
        assert args.max_parallel == 3

    def test_result_propagates_to_cli(self, monkeypatch, tmp_path: Path):
        class FakeDatabase:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return None

        release = __import__("import_orchestrator.ecosystems.java.commands.release", fromlist=["run"])
        monkeypatch.setattr(release, "ImportDatabase", lambda path: FakeDatabase())
        monkeypatch.setattr(release, "KubeClient", lambda *args: object())
        monkeypatch.setattr(release.ReleaseOnly, "run", lambda *args, **kwargs: 1)
        monkeypatch.setattr("sys.argv", ["prog", "--db", str(tmp_path / "state.db"), "java", "release"])
        assert main() == 1


class TestParserImportFile:
    def test_parses_file_argument(self):
        parser = make_parser()
        args = parser.parse_args(["java", "import-file", "/tmp/refs.txt"])
        assert args.file == Path("/tmp/refs.txt")
        assert args.command == "import-file"

    def test_has_func_attribute(self):
        parser = make_parser()
        args = parser.parse_args(["java", "import-file", "refs.txt"])
        assert hasattr(args, "func")
        assert callable(args.func)


class TestMain:
    def test_no_args_returns_2(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog"])
        assert main() == 2

    def test_ecosystem_without_command_returns_2(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["prog", "java"])
        assert main() == 2


class TestMainOrchestrate:
    @pytest.fixture(autouse=True)
    def _mock_kube_auth(self):
        with patch("import_orchestrator.clients.kube.resolve_auth") as mock:
            mock.return_value = KubeAuth(server="https://api.example.com:6443", token="fake", ca_cert=None)
            yield

    def test_reset_deletes_existing_db(self, monkeypatch, tmp_path: Path):
        db_path = tmp_path / "test.db"
        db_path.write_text("dummy data")

        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--db", str(db_path), "--reset", "java", "orchestrate"],
        )

        with patch.object(
            __import__("import_orchestrator.engine.orchestrator", fromlist=["ImportOrchestrator"]).ImportOrchestrator,
            "run_until_complete",
            return_value=0,
        ):
            main()

        assert db_path.exists()

    def test_empty_database_prints_warning(self, monkeypatch, tmp_path: Path, capsys):
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--db", str(tmp_path / "test.db"), "java", "orchestrate"],
        )

        with patch.object(
            __import__("import_orchestrator.engine.orchestrator", fromlist=["ImportOrchestrator"]).ImportOrchestrator,
            "run_until_complete",
            return_value=0,
        ):
            exit_code = main()

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "WARNING: No OCI references in database. Run 'import-orchestrator fetch' first." in captured.err


class TestMainImportFile:
    def test_missing_file_returns_2(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--db", str(tmp_path / "test.db"), "java", "import-file", "/nonexistent/refs.txt"],
        )
        exit_code = main()
        assert exit_code == 2

    def test_imports_refs_from_file(self, monkeypatch, tmp_path: Path):
        refs_file = tmp_path / "refs.txt"
        refs_file.write_text("quay.io/repo:tag1@sha256:aaa\nquay.io/repo:tag2@sha256:bbb\n")

        db_path = tmp_path / "test.db"
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--db", str(db_path), "java", "import-file", str(refs_file)],
        )

        exit_code = main()
        assert exit_code == 0

        with ImportDatabase(db_path) as db:
            pending = db.get_by_status(ImportStatus.PENDING)
            assert len(pending) == 2
            refs = {r.ref for r in pending}
            assert refs == {"quay.io/repo:tag1@sha256:aaa", "quay.io/repo:tag2@sha256:bbb"}

    def test_works_with_reset_flag(self, monkeypatch, tmp_path: Path):
        db_path = tmp_path / "test.db"
        with ImportDatabase(db_path) as db:
            db.add_item("quay.io/repo:old@sha256:old")

        refs_file = tmp_path / "refs.txt"
        refs_file.write_text("quay.io/repo:new@sha256:new\n")

        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--db", str(db_path), "--reset", "java", "import-file", str(refs_file)],
        )

        exit_code = main()
        assert exit_code == 0

        with ImportDatabase(db_path) as db:
            pending = db.get_by_status(ImportStatus.PENDING)
            assert len(pending) == 1
            assert pending[0].ref == "quay.io/repo:new@sha256:new"
