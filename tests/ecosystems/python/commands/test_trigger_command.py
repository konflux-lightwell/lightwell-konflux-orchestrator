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
from import_orchestrator.ecosystems.python.commands.trigger import run


class TestTriggerArgParsing:
    def test_ref_required(self):
        parser = make_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["python", "trigger"])

    def test_ref_parsed(self):
        parser = make_parser()
        args = parser.parse_args(["python", "trigger", "ntplib==0.4.0"])
        assert args.ref == "ntplib==0.4.0"

    def test_command_is_trigger(self):
        parser = make_parser()
        args = parser.parse_args(["python", "trigger", "ntplib==0.4.0"])
        assert args.command == "trigger"

    def test_ecosystem_attached(self):
        parser = make_parser()
        args = parser.parse_args(["python", "trigger", "ntplib==0.4.0"])
        assert args.ecosystem.name == "python"

    def test_target_default(self, monkeypatch):
        monkeypatch.delenv("LIGHTWELL_PYTHON_TARGET", raising=False)
        parser = make_parser()
        args = parser.parse_args(["python", "trigger", "ntplib==0.4.0"])
        assert args.target == "REMEDIATED"

    def test_invalid_target_rejected(self):
        parser = make_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["python", "trigger", "--target", "NOPE", "ntplib==0.4.0"])

    def test_builds_tag_defaults_to_none(self):
        parser = make_parser()
        args = parser.parse_args(["python", "trigger", "ntplib==0.4.0"])
        assert args.builds_tag is None

    def test_builds_tag_parsed(self):
        parser = make_parser()
        args = parser.parse_args(
            ["python", "trigger", "--builds-tag", "CVE-2025-1234/0.4.0/pipeline-9", "ntplib==0.4.0"]
        )
        assert args.builds_tag == "CVE-2025-1234/0.4.0/pipeline-9"


def _args(**overrides):
    base = dict(ref="ntplib==0.4.0", ecosystem=MagicMock())
    base.update(overrides)
    return argparse.Namespace(**base)


class TestTriggerRun:
    @patch("import_orchestrator.commands.trigger.KubeClient")
    def test_passes_ref_and_args_to_build(self, mock_kube_cls):
        mock_kube = mock_kube_cls.return_value
        mock_kube.create_pipelinerun.return_value = "python-remediated-build-abc"

        eco = MagicMock()
        eco.build_pipelinerun.return_value = {"kind": "PipelineRun"}

        args = _args(ecosystem=eco)
        exit_code = run(args)

        assert exit_code == 0
        eco.build_pipelinerun.assert_called_once_with("ntplib==0.4.0", args)

    @patch("import_orchestrator.commands.trigger.KubeClient")
    def test_uses_ecosystem_namespace(self, mock_kube_cls):
        eco = MagicMock()
        eco.namespace = "lightwell-python-tenant"
        eco.build_pipelinerun.return_value = {"kind": "PipelineRun"}
        mock_kube_cls.return_value.create_pipelinerun.return_value = "python-remediated-build-abc"

        run(_args(ecosystem=eco))

        namespace_arg = mock_kube_cls.call_args[0][0]
        assert namespace_arg == "lightwell-python-tenant"
