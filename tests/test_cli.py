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

import subprocess
from pathlib import Path
from unittest.mock import patch

from import_orchestrator.cli import main, make_parser
from import_orchestrator.constants import (
    DEFAULT_DB_PATH,
    DEFAULT_MAX_PARALLEL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_POLL_INTERVAL,
)


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
    def test_default_values(self):
        parser = make_parser()
        args = parser.parse_args(["fetch"])
        assert args.db == Path(DEFAULT_DB_PATH)
        assert args.command == "fetch"
        # fetch_script has a default path derived from project root
        assert args.fetch_script.name == "fetch_pnc_oci_references.sh"

    def test_custom_fetch_script(self):
        parser = make_parser()
        args = parser.parse_args(["fetch", "--fetch-script", "/tmp/my_fetch.sh"])
        assert args.fetch_script == Path("/tmp/my_fetch.sh")

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
    def test_missing_trigger_script_returns_2(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(
            "sys.argv",
            [
                "prog",
                "--db",
                str(tmp_path / "test.db"),
                "orchestrate",
                "--trigger-script",
                "/nonexistent/trigger.sh",
            ],
        )
        exit_code = main()
        assert exit_code == 2

    def test_reset_deletes_existing_db(self, monkeypatch, tmp_path: Path):
        db_path = tmp_path / "test.db"
        db_path.write_text("dummy data")
        trigger_script = tmp_path / "trigger.sh"
        trigger_script.touch()

        monkeypatch.setattr(
            "sys.argv",
            [
                "prog",
                "--db",
                str(db_path),
                "--reset",
                "orchestrate",
                "--trigger-script",
                str(trigger_script),
            ],
        )

        with patch.object(
            __import__("import_orchestrator.orchestrator", fromlist=["ImportOrchestrator"]).ImportOrchestrator,
            "run_until_complete",
            return_value=0,
        ):
            main()

        # After reset, db should be recreated by ImportDatabase
        assert db_path.exists()

    def test_empty_database_prints_warning(self, monkeypatch, tmp_path: Path, capsys):
        trigger_script = tmp_path / "trigger.sh"
        trigger_script.touch()

        monkeypatch.setattr(
            "sys.argv",
            [
                "prog",
                "--db",
                str(tmp_path / "test.db"),
                "orchestrate",
                "--trigger-script",
                str(trigger_script),
            ],
        )

        with patch.object(
            __import__("import_orchestrator.orchestrator", fromlist=["ImportOrchestrator"]).ImportOrchestrator,
            "run_until_complete",
            return_value=0,
        ):
            exit_code = main()

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "WARNING: No OCI references in database. Run 'import-orchestrator fetch' first." in captured.err


class TestMainFetch:
    def test_missing_fetch_script_returns_2(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(
            "sys.argv",
            [
                "prog",
                "--db",
                str(tmp_path / "test.db"),
                "fetch",
                "--fetch-script",
                "/nonexistent/fetch.sh",
            ],
        )
        exit_code = main()
        assert exit_code == 2

    @patch("import_orchestrator.orchestrator.subprocess.run")
    def test_fetch_stores_refs_and_returns_0(self, mock_run, monkeypatch, tmp_path: Path):
        fetch_script = tmp_path / "fetch.sh"
        fetch_script.touch()

        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="quay.io/repo:tag1@sha256:aaa\n",
            stderr="",
        )

        monkeypatch.setattr(
            "sys.argv",
            [
                "prog",
                "--db",
                str(tmp_path / "test.db"),
                "fetch",
                "--fetch-script",
                str(fetch_script),
            ],
        )

        exit_code = main()
        assert exit_code == 0

    @patch("import_orchestrator.orchestrator.subprocess.run")
    def test_fetch_returns_empty_exits_0(self, mock_run, monkeypatch, tmp_path: Path):
        fetch_script = tmp_path / "fetch.sh"
        fetch_script.touch()

        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        monkeypatch.setattr(
            "sys.argv",
            [
                "prog",
                "--db",
                str(tmp_path / "test.db"),
                "fetch",
                "--fetch-script",
                str(fetch_script),
            ],
        )

        exit_code = main()
        assert exit_code == 0
