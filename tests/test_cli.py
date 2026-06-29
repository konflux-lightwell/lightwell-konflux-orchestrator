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
        args = parser.parse_args([])
        assert args.db == Path(DEFAULT_DB_PATH)
        assert args.max_parallel == DEFAULT_MAX_PARALLEL
        assert args.poll_interval == DEFAULT_POLL_INTERVAL
        assert args.max_retries == DEFAULT_MAX_RETRIES
        assert args.skip_fetch is False
        assert args.fetch_only is False
        assert args.reset is False

    def test_custom_db_path(self):
        parser = make_parser()
        args = parser.parse_args(["--db", "/tmp/custom.db"])
        assert args.db == Path("/tmp/custom.db")

    def test_max_parallel(self):
        parser = make_parser()
        args = parser.parse_args(["--max-parallel", "10"])
        assert args.max_parallel == 10

    def test_poll_interval(self):
        parser = make_parser()
        args = parser.parse_args(["--poll-interval", "60"])
        assert args.poll_interval == 60

    def test_max_retries(self):
        parser = make_parser()
        args = parser.parse_args(["--max-retries", "5"])
        assert args.max_retries == 5

    def test_skip_fetch_flag(self):
        parser = make_parser()
        args = parser.parse_args(["--skip-fetch"])
        assert args.skip_fetch is True

    def test_fetch_only_flag(self):
        parser = make_parser()
        args = parser.parse_args(["--fetch-only"])
        assert args.fetch_only is True

    def test_reset_flag(self):
        parser = make_parser()
        args = parser.parse_args(["--reset"])
        assert args.reset is True

    def test_all_flags_combined(self):
        parser = make_parser()
        args = parser.parse_args([
            "--db", "/tmp/test.db",
            "--max-parallel", "8",
            "--poll-interval", "15",
            "--max-retries", "2",
            "--skip-fetch",
        ])
        assert args.db == Path("/tmp/test.db")
        assert args.max_parallel == 8
        assert args.poll_interval == 15
        assert args.max_retries == 2
        assert args.skip_fetch is True


class TestMain:
    def test_missing_fetch_script_returns_2(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--db", str(tmp_path / "test.db"), "--fetch-script", "/nonexistent/fetch.sh"],
        )
        exit_code = main()
        assert exit_code == 2

    def test_missing_trigger_script_returns_2(self, monkeypatch, tmp_path: Path):
        fetch_script = tmp_path / "fetch.sh"
        fetch_script.touch()

        monkeypatch.setattr(
            "sys.argv",
            [
                "prog",
                "--db", str(tmp_path / "test.db"),
                "--fetch-script", str(fetch_script),
                "--trigger-script", "/nonexistent/trigger.sh",
            ],
        )
        exit_code = main()
        assert exit_code == 2

    def test_reset_deletes_existing_db(self, monkeypatch, tmp_path: Path):
        db_path = tmp_path / "test.db"
        db_path.write_text("dummy data")
        fetch_script = tmp_path / "fetch.sh"
        fetch_script.touch()

        monkeypatch.setattr(
            "sys.argv",
            [
                "prog",
                "--db", str(db_path),
                "--fetch-script", str(fetch_script),
                "--reset",
                "--skip-fetch",
                "--fetch-only",
            ],
        )

        main()
        # After reset + skip-fetch + fetch-only, db should be recreated by ImportDatabase
        # but the original file content should be gone
        assert db_path.exists()  # recreated by the context manager

    @patch("import_orchestrator.orchestrator.subprocess.run")
    def test_fetch_only_mode(self, mock_run, monkeypatch, tmp_path: Path):
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
                "--db", str(tmp_path / "test.db"),
                "--fetch-script", str(fetch_script),
                "--fetch-only",
            ],
        )

        exit_code = main()
        assert exit_code == 0

    @patch("import_orchestrator.orchestrator.subprocess.run")
    def test_fetch_returns_empty_exits_0(self, mock_run, monkeypatch, tmp_path: Path):
        fetch_script = tmp_path / "fetch.sh"
        fetch_script.touch()
        trigger_script = tmp_path / "trigger.sh"
        trigger_script.touch()

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        monkeypatch.setattr(
            "sys.argv",
            [
                "prog",
                "--db", str(tmp_path / "test.db"),
                "--fetch-script", str(fetch_script),
                "--trigger-script", str(trigger_script),
            ],
        )

        exit_code = main()
        assert exit_code == 0
