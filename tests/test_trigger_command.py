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
from import_orchestrator.commands.trigger import register, run
from import_orchestrator.ecosystems.java.ecosystem import JavaEcosystem
from import_orchestrator.engine.errors import TriggerError

# ---------------------------------------------------------------------------
# Argument parsing via make_parser (the java trigger subcommand)
# ---------------------------------------------------------------------------


class TestTriggerArgParsing:
    """Test argument parsing for the 'trigger' subcommand."""

    def test_source_image_required(self):
        parser = make_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["java", "trigger"])

    def test_source_image_parsed(self):
        parser = make_parser()
        args = parser.parse_args(["java", "trigger", "quay.io/repo:tag@sha256:abc"])
        assert args.source_image == "quay.io/repo:tag@sha256:abc"

    def test_optional_tag(self):
        parser = make_parser()
        args = parser.parse_args(["java", "trigger", "quay.io/repo:tag@sha256:abc", "custom-tag"])
        assert args.tag == "custom-tag"

    def test_tag_defaults_to_none(self):
        parser = make_parser()
        args = parser.parse_args(["java", "trigger", "quay.io/repo:tag@sha256:abc"])
        assert args.tag is None

    def test_artifact_type_rebuild(self, monkeypatch):
        monkeypatch.delenv("LIGHTWELL_ARTIFACT_TYPE", raising=False)
        parser = make_parser()
        args = parser.parse_args(["java", "trigger", "--artifact-type", "REBUILD", "quay.io/repo:tag@sha256:abc"])
        assert args.artifact_type == "REBUILD"

    def test_artifact_type_default(self, monkeypatch):
        monkeypatch.delenv("LIGHTWELL_ARTIFACT_TYPE", raising=False)
        parser = make_parser()
        args = parser.parse_args(["java", "trigger", "quay.io/repo:tag@sha256:abc"])
        assert args.artifact_type == "STAGE"

    def test_artifact_type_from_env_var(self, monkeypatch):
        monkeypatch.setenv("LIGHTWELL_ARTIFACT_TYPE", "REMEDIATED")
        parser = make_parser()
        args = parser.parse_args(["java", "trigger", "quay.io/repo:tag@sha256:abc"])
        assert args.artifact_type == "REMEDIATED"

    def test_invalid_artifact_type_rejected(self):
        parser = make_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["java", "trigger", "--artifact-type", "INVALID", "quay.io/repo:tag@sha256:abc"])

    def test_has_func_attribute(self):
        parser = make_parser()
        args = parser.parse_args(["java", "trigger", "quay.io/repo:tag@sha256:abc"])
        assert hasattr(args, "func")
        assert callable(args.func)

    def test_command_is_trigger(self):
        parser = make_parser()
        args = parser.parse_args(["java", "trigger", "quay.io/repo:tag@sha256:abc"])
        assert args.command == "trigger"

    def test_ecosystem_attached(self):
        parser = make_parser()
        args = parser.parse_args(["java", "trigger", "quay.io/repo:tag@sha256:abc"])
        assert args.ecosystem.name == "java"

    def test_top_level_db_flag_still_works(self):
        parser = make_parser()
        args = parser.parse_args(["--db", "/tmp/custom.db", "java", "trigger", "quay.io/repo:tag@sha256:abc"])
        assert str(args.db) == "/tmp/custom.db"


# ---------------------------------------------------------------------------
# register() standalone test
# ---------------------------------------------------------------------------


class TestTriggerRegister:
    """Test the register function that wires up the subparser."""

    def test_registers_trigger_subcommand(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        register(subparsers, JavaEcosystem())

        args = parser.parse_args(["trigger", "quay.io/repo:tag@sha256:abc"])
        assert args.command == "trigger"
        assert args.source_image == "quay.io/repo:tag@sha256:abc"


# ---------------------------------------------------------------------------
# run() function
# ---------------------------------------------------------------------------


def _args(**overrides):
    base = dict(
        source_image="quay.io/repo:tag@sha256:abc",
        tag=None,
        artifact_type="REBUILD",
        ecosystem=MagicMock(),
    )
    base.update(overrides)
    return argparse.Namespace(**base)


class TestTriggerRun:
    """Test the run() function that executes the trigger subcommand."""

    @patch("import_orchestrator.commands.trigger.KubeClient")
    def test_successful_trigger_returns_0(self, mock_kube_cls, capsys):
        mock_kube = mock_kube_cls.return_value
        mock_kube.create_pipelinerun.return_value = "pnc-import-12345"

        eco = MagicMock()
        eco.build_pipelinerun.return_value = {"kind": "PipelineRun"}

        exit_code = run(_args(ecosystem=eco))

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "pnc-import-12345" in captured.out

    @patch("import_orchestrator.commands.trigger.KubeClient")
    def test_none_result_returns_1(self, mock_kube_cls, capsys):
        mock_kube = mock_kube_cls.return_value
        mock_kube.create_pipelinerun.return_value = None

        eco = MagicMock()
        eco.build_pipelinerun.return_value = {"kind": "PipelineRun"}

        exit_code = run(_args(ecosystem=eco))

        assert exit_code == 1
        captured = capsys.readouterr()
        assert "could not be parsed" in captured.err

    @patch("import_orchestrator.commands.trigger.KubeClient")
    def test_trigger_error_returns_1(self, mock_kube_cls, capsys):
        eco = MagicMock()
        eco.build_pipelinerun.side_effect = TriggerError("skopeo failed")

        exit_code = run(_args(source_image="quay.io/bad:ref", ecosystem=eco))

        assert exit_code == 1
        captured = capsys.readouterr()
        assert "skopeo failed" in captured.err

    @patch("import_orchestrator.commands.trigger.KubeClient")
    def test_passes_source_image_and_args_to_build(self, mock_kube_cls):
        mock_kube = mock_kube_cls.return_value
        mock_kube.create_pipelinerun.return_value = "pnc-import-xyz"

        eco = MagicMock()
        eco.build_pipelinerun.return_value = {"kind": "PipelineRun"}

        args = _args(tag="my-custom-tag", ecosystem=eco)
        run(args)

        eco.build_pipelinerun.assert_called_once_with("quay.io/repo:tag@sha256:abc", args)
        mock_kube.create_pipelinerun.assert_called_once_with({"kind": "PipelineRun"})

    @patch("import_orchestrator.commands.trigger.KubeClient")
    def test_output_format_on_success(self, mock_kube_cls, capsys):
        mock_kube = mock_kube_cls.return_value
        mock_kube.create_pipelinerun.return_value = "pnc-import-99999"

        eco = MagicMock()
        eco.build_pipelinerun.return_value = {"kind": "PipelineRun"}

        run(_args(ecosystem=eco))

        captured = capsys.readouterr()
        assert captured.out.strip() == "pipelinerun.tekton.dev/pnc-import-99999 created"


# ---------------------------------------------------------------------------
# Full CLI integration via main()
# ---------------------------------------------------------------------------


class TestTriggerCLIIntegration:
    """Test the trigger subcommand through the full CLI entry point."""

    @patch.object(JavaEcosystem, "build_pipelinerun", return_value={"kind": "PipelineRun"})
    @patch("import_orchestrator.commands.trigger.KubeClient")
    def test_cli_trigger_success(self, mock_kube_cls, mock_build, monkeypatch, capsys):
        mock_kube = mock_kube_cls.return_value
        mock_kube.create_pipelinerun.return_value = "pnc-import-cli-test"

        monkeypatch.setattr("sys.argv", ["prog", "java", "trigger", "quay.io/repo:v1@sha256:abc"])

        from import_orchestrator.cli import main

        exit_code = main()
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "pnc-import-cli-test" in captured.out

    @patch.object(JavaEcosystem, "build_pipelinerun", return_value={"kind": "PipelineRun"})
    @patch("import_orchestrator.commands.trigger.KubeClient")
    def test_cli_trigger_passes_source_and_args(self, mock_kube_cls, mock_build, monkeypatch):
        mock_kube = mock_kube_cls.return_value
        mock_kube.create_pipelinerun.return_value = "pnc-import-xyz"

        monkeypatch.delenv("LIGHTWELL_ARTIFACT_TYPE", raising=False)
        monkeypatch.setattr(
            "sys.argv",
            [
                "prog",
                "java",
                "trigger",
                "--artifact-type",
                "REMEDIATED",
                "quay.io/repo:v1@sha256:abc",
                "override-tag",
            ],
        )

        from import_orchestrator.cli import main

        exit_code = main()
        assert exit_code == 0

        called_source, called_args = mock_build.call_args[0]
        assert called_source == "quay.io/repo:v1@sha256:abc"
        assert called_args.tag == "override-tag"
        assert called_args.artifact_type == "REMEDIATED"

    @patch.object(JavaEcosystem, "build_pipelinerun", side_effect=TriggerError("connection refused"))
    @patch("import_orchestrator.commands.trigger.KubeClient")
    def test_cli_trigger_error_exits_1(self, mock_kube_cls, mock_build, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["prog", "java", "trigger", "quay.io/repo:bad@sha256:xxx"])

        from import_orchestrator.cli import main

        exit_code = main()
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "connection refused" in captured.err
