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
from import_orchestrator.constants import (
    DEFAULT_DB_PATH,
    DEFAULT_MAX_PARALLEL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_POLL_INTERVAL,
)
from import_orchestrator.database import ImportDatabase
from import_orchestrator.models import ImportStatus


class TestMakeParser:
    def test_default_values(self):
        parser = make_parser()
        args = parser.parse_args(["orchestrate"])
        assert args.db == Path(DEFAULT_DB_PATH)
        assert args.max_parallel == DEFAULT_MAX_PARALLEL
        assert args.poll_interval == DEFAULT_POLL_INTERVAL
        assert args.max_retries == DEFAULT_MAX_RETRIES
        assert args.reset is False

    def test_custom_db_path(self):
        parser = make_parser()
        args = parser.parse_args(["--db", "/tmp/custom.db", "orchestrate"])
        assert args.db == Path("/tmp/custom.db")

    def test_max_parallel(self):
        parser = make_parser()
        args = parser.parse_args(["orchestrate", "--max-parallel", "10"])
        assert args.max_parallel == 10

    def test_poll_interval(self):
        parser = make_parser()
        args = parser.parse_args(["orchestrate", "--poll-interval", "60"])
        assert args.poll_interval == 60

    def test_max_retries(self):
        parser = make_parser()
        args = parser.parse_args(["orchestrate", "--max-retries", "5"])
        assert args.max_retries == 5

    def test_reset_flag(self):
        parser = make_parser()
        args = parser.parse_args(["--reset", "orchestrate"])
        assert args.reset is True

    def test_all_flags_combined(self):
        parser = make_parser()
        args = parser.parse_args(
            [
                "--db",
                "/tmp/test.db",
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


class TestMakeParserFetch:
    def test_default_values(self, monkeypatch):
        monkeypatch.delenv("LIGHTWELL_ARTIFACT_TYPE", raising=False)
        parser = make_parser()
        args = parser.parse_args(["fetch"])
        assert args.db == Path(DEFAULT_DB_PATH)
        assert args.command == "fetch"
        assert args.artifact_type == "REBUILD"

    def test_custom_artifact_type(self):
        parser = make_parser()
        args = parser.parse_args(["fetch", "--artifact-type", "REMEDIATED"])
        assert args.artifact_type == "REMEDIATED"

    def test_custom_db_path(self):
        parser = make_parser()
        args = parser.parse_args(["--db", "/tmp/custom.db", "fetch"])
        assert args.db == Path("/tmp/custom.db")

    def test_reset_flag_with_fetch(self):
        parser = make_parser()
        args = parser.parse_args(["--reset", "fetch"])
        assert args.reset is True


class TestMain:
    def test_no_subcommand_returns_2(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog"])
        exit_code = main()
        assert exit_code == 2


class TestMainOrchestrate:
    def test_reset_deletes_existing_db(self, monkeypatch, tmp_path: Path):
        db_path = tmp_path / "test.db"
        db_path.write_text("dummy data")

        monkeypatch.setattr(
            "sys.argv",
            [
                "prog",
                "--db",
                str(db_path),
                "--reset",
                "orchestrate",
            ],
        )

        with patch.object(
            __import__("import_orchestrator.engine.orchestrator", fromlist=["ImportOrchestrator"]).ImportOrchestrator,
            "run_until_complete",
            return_value=0,
        ):
            main()

        # After reset, db should be recreated by ImportDatabase
        assert db_path.exists()

    def test_empty_database_prints_warning(self, monkeypatch, tmp_path: Path, capsys):
        monkeypatch.setattr(
            "sys.argv",
            [
                "prog",
                "--db",
                str(tmp_path / "test.db"),
                "orchestrate",
            ],
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


class TestMainFetch:
    def test_missing_quay_token_returns_2(self, monkeypatch, tmp_path: Path):
        monkeypatch.delenv("QUAY_TOKEN", raising=False)
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--db", str(tmp_path / "test.db"), "fetch"],
        )
        exit_code = main()
        assert exit_code == 2

    @patch("import_orchestrator.commands.fetch.QuayClient")
    def test_fetch_stores_refs_and_returns_0(self, mock_client_cls, monkeypatch, tmp_path: Path):
        mock_client = mock_client_cls.return_value
        mock_client.fetch_oci_references.return_value = ["quay.io/repo:tag1@sha256:aaa"]

        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--db", str(tmp_path / "test.db"), "fetch"],
        )

        exit_code = main()
        assert exit_code == 0

    @patch("import_orchestrator.commands.fetch.QuayClient")
    def test_fetch_returns_empty_exits_0(self, mock_client_cls, monkeypatch, tmp_path: Path):
        mock_client = mock_client_cls.return_value
        mock_client.fetch_oci_references.return_value = []

        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--db", str(tmp_path / "test.db"), "fetch"],
        )

        exit_code = main()
        assert exit_code == 0


class TestMakeParserImportFile:
    def test_parses_file_argument(self):
        parser = make_parser()
        args = parser.parse_args(["import-file", "/tmp/refs.txt"])
        assert args.file == Path("/tmp/refs.txt")
        assert args.command == "import-file"

    def test_inherits_top_level_db_flag(self):
        parser = make_parser()
        args = parser.parse_args(["--db", "/tmp/custom.db", "import-file", "refs.txt"])
        assert args.db == Path("/tmp/custom.db")

    def test_has_func_attribute(self):
        parser = make_parser()
        args = parser.parse_args(["import-file", "refs.txt"])
        assert hasattr(args, "func")
        assert callable(args.func)


class TestMainImportFile:
    def test_missing_file_returns_2(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(
            "sys.argv",
            [
                "prog",
                "--db",
                str(tmp_path / "test.db"),
                "import-file",
                "/nonexistent/refs.txt",
            ],
        )
        exit_code = main()
        assert exit_code == 2

    def test_empty_file_returns_0(self, monkeypatch, tmp_path: Path):
        refs_file = tmp_path / "empty.txt"
        refs_file.write_text("")

        monkeypatch.setattr(
            "sys.argv",
            [
                "prog",
                "--db",
                str(tmp_path / "test.db"),
                "import-file",
                str(refs_file),
            ],
        )

        exit_code = main()
        assert exit_code == 0

    def test_imports_refs_from_file(self, monkeypatch, tmp_path: Path):
        refs_file = tmp_path / "refs.txt"
        refs_file.write_text("quay.io/repo:tag1@sha256:aaa\nquay.io/repo:tag2@sha256:bbb\n")

        db_path = tmp_path / "test.db"
        monkeypatch.setattr(
            "sys.argv",
            [
                "prog",
                "--db",
                str(db_path),
                "import-file",
                str(refs_file),
            ],
        )

        exit_code = main()
        assert exit_code == 0

        with ImportDatabase(db_path) as db:
            pending = db.get_by_status(ImportStatus.PENDING)
            assert len(pending) == 2
            refs = {r.oci_ref for r in pending}
            assert refs == {"quay.io/repo:tag1@sha256:aaa", "quay.io/repo:tag2@sha256:bbb"}

    def test_skips_comments_and_blank_lines(self, monkeypatch, tmp_path: Path):
        refs_file = tmp_path / "refs.txt"
        refs_file.write_text(
            "# This is a comment\n"
            "\n"
            "quay.io/repo:tag1@sha256:aaa\n"
            "  \n"
            "# Another comment\n"
            "quay.io/repo:tag2@sha256:bbb\n"
            "\n"
        )

        db_path = tmp_path / "test.db"
        monkeypatch.setattr(
            "sys.argv",
            [
                "prog",
                "--db",
                str(db_path),
                "import-file",
                str(refs_file),
            ],
        )

        exit_code = main()
        assert exit_code == 0

        with ImportDatabase(db_path) as db:
            pending = db.get_by_status(ImportStatus.PENDING)
            assert len(pending) == 2

    def test_handles_duplicates(self, monkeypatch, tmp_path: Path, capsys):
        refs_file = tmp_path / "refs.txt"
        refs_file.write_text(
            "quay.io/repo:tag1@sha256:aaa\nquay.io/repo:tag1@sha256:aaa\nquay.io/repo:tag2@sha256:bbb\n"
        )

        db_path = tmp_path / "test.db"
        monkeypatch.setattr(
            "sys.argv",
            [
                "prog",
                "--db",
                str(db_path),
                "import-file",
                str(refs_file),
            ],
        )

        exit_code = main()
        assert exit_code == 0

        with ImportDatabase(db_path) as db:
            pending = db.get_by_status(ImportStatus.PENDING)
            assert len(pending) == 2

        captured = capsys.readouterr()
        assert "2 new" in captured.err
        assert "1 already in database" in captured.err

    def test_works_with_reset_flag(self, monkeypatch, tmp_path: Path):
        db_path = tmp_path / "test.db"

        # Pre-populate the database
        with ImportDatabase(db_path) as db:
            db.add_oci_reference("quay.io/repo:old@sha256:old")

        refs_file = tmp_path / "refs.txt"
        refs_file.write_text("quay.io/repo:new@sha256:new\n")

        monkeypatch.setattr(
            "sys.argv",
            [
                "prog",
                "--db",
                str(db_path),
                "--reset",
                "import-file",
                str(refs_file),
            ],
        )

        exit_code = main()
        assert exit_code == 0

        with ImportDatabase(db_path) as db:
            pending = db.get_by_status(ImportStatus.PENDING)
            assert len(pending) == 1
            assert pending[0].oci_ref == "quay.io/repo:new@sha256:new"
