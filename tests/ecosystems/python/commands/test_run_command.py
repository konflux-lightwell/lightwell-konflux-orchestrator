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
from unittest.mock import MagicMock, patch

import pytest

from import_orchestrator.cli import make_parser
from import_orchestrator.constants import DEFAULT_MAX_RETRIES, DEFAULT_POLL_INTERVAL
from import_orchestrator.ecosystems.python.commands.run import run


class TestRunArgParsing:
    def test_ref_required(self):
        parser = make_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["python", "run"])

    def test_ref_parsed(self):
        parser = make_parser()
        args = parser.parse_args(["python", "run", "ntplib==0.4.0"])
        assert args.ref == "ntplib==0.4.0"

    def test_command_name(self):
        parser = make_parser()
        args = parser.parse_args(["python", "run", "ntplib==0.4.0"])
        assert args.command == "run"

    def test_ecosystem_attached(self):
        parser = make_parser()
        args = parser.parse_args(["python", "run", "ntplib==0.4.0"])
        assert args.ecosystem.name == "python"

    def test_target_default(self, monkeypatch):
        monkeypatch.delenv("LIGHTWELL_PYTHON_TARGET", raising=False)
        parser = make_parser()
        args = parser.parse_args(["python", "run", "ntplib==0.4.0"])
        assert args.target == "REMEDIATED"

    def test_builds_tag_defaults_to_none(self):
        parser = make_parser()
        args = parser.parse_args(["python", "run", "ntplib==0.4.0"])
        assert args.builds_tag is None

    def test_builds_ref_alias_parsed(self):
        parser = make_parser()
        args = parser.parse_args(["python", "run", "--builds-ref", "cumulative/0.4.0/pipeline-9", "ntplib==0.4.0"])
        assert args.builds_tag == "cumulative/0.4.0/pipeline-9"

    def test_poll_interval_and_max_retries_defaults(self):
        parser = make_parser()
        args = parser.parse_args(["python", "run", "ntplib==0.4.0"])
        assert args.poll_interval == DEFAULT_POLL_INTERVAL
        assert args.max_retries == DEFAULT_MAX_RETRIES

    def test_poll_interval_and_max_retries_override(self):
        parser = make_parser()
        args = parser.parse_args(["python", "run", "--poll-interval", "5", "--max-retries", "1", "ntplib==0.4.0"])
        assert args.poll_interval == 5
        assert args.max_retries == 1

    def test_no_max_parallel_flag(self):
        parser = make_parser()
        args = parser.parse_args(["python", "run", "ntplib==0.4.0"])
        assert not hasattr(args, "max_parallel")

    def test_output_json_defaults_to_none(self):
        parser = make_parser()
        args = parser.parse_args(["python", "run", "ntplib==0.4.0"])
        assert args.output_json is None

    def test_output_json_parsed(self):
        parser = make_parser()
        args = parser.parse_args(["python", "run", "--output-json", "/tmp/result.json", "ntplib==0.4.0"])
        assert args.output_json == "/tmp/result.json"


class TestRunCommand:
    @patch("import_orchestrator.ecosystems.python.commands.run.run_single")
    def test_delegates_with_ref(self, mock_run):
        mock_run.return_value = 0
        args = argparse.Namespace(ref="ntplib==0.4.0", ecosystem=MagicMock())
        assert run(args) == 0
        mock_run.assert_called_once_with(args, "ntplib==0.4.0")
