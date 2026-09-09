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
from import_orchestrator.ecosystems.java.commands.run import run

_IMAGE = "quay.io/example/image:tag@sha256:" + "a" * 64


class TestRunArgParsing:
    def test_source_image_required(self):
        parser = make_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["java", "run"])

    def test_source_image_parsed(self):
        parser = make_parser()
        args = parser.parse_args(["java", "run", _IMAGE])
        assert args.source_image == _IMAGE

    def test_command_name(self):
        parser = make_parser()
        args = parser.parse_args(["java", "run", _IMAGE])
        assert args.command == "run"

    def test_ecosystem_attached(self):
        parser = make_parser()
        args = parser.parse_args(["java", "run", _IMAGE])
        assert args.ecosystem.name == "java"

    def test_tag_optional_defaults_to_none(self):
        parser = make_parser()
        args = parser.parse_args(["java", "run", _IMAGE])
        assert args.tag is None

    def test_tag_parsed(self):
        parser = make_parser()
        args = parser.parse_args(["java", "run", _IMAGE, "custom-tag"])
        assert args.tag == "custom-tag"

    def test_artifact_type_default(self, monkeypatch):
        monkeypatch.delenv("LIGHTWELL_ARTIFACT_TYPE", raising=False)
        parser = make_parser()
        args = parser.parse_args(["java", "run", _IMAGE])
        assert args.artifact_type == "STAGE"

    def test_poll_interval_and_max_retries_defaults(self):
        parser = make_parser()
        args = parser.parse_args(["java", "run", _IMAGE])
        assert args.poll_interval == DEFAULT_POLL_INTERVAL
        assert args.max_retries == DEFAULT_MAX_RETRIES

    def test_no_max_parallel_flag(self):
        parser = make_parser()
        args = parser.parse_args(["java", "run", _IMAGE])
        assert not hasattr(args, "max_parallel")

    def test_output_json_defaults_to_none(self):
        parser = make_parser()
        args = parser.parse_args(["java", "run", _IMAGE])
        assert args.output_json is None

    def test_output_json_parsed(self):
        parser = make_parser()
        args = parser.parse_args(["java", "run", "--output-json", "/tmp/result.json", _IMAGE])
        assert args.output_json == "/tmp/result.json"


class TestRunCommand:
    @patch("import_orchestrator.ecosystems.java.commands.run.run_single")
    def test_delegates_with_source_image(self, mock_run):
        mock_run.return_value = 0
        args = argparse.Namespace(source_image=_IMAGE, ecosystem=MagicMock())
        assert run(args) == 0
        mock_run.assert_called_once_with(args, _IMAGE)
