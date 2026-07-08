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
from unittest.mock import patch

import pytest

from import_orchestrator.cli import make_parser
from import_orchestrator.commands.trigger import register, run
from import_orchestrator.engine.pipelinerun import TriggerError

# ---------------------------------------------------------------------------
# Argument parsing via make_parser (the trigger subcommand)
# ---------------------------------------------------------------------------


class TestTriggerArgParsing:
    """Test argument parsing for the 'trigger' subcommand."""

    def test_source_image_required(self):
        parser = make_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["trigger"])

    def test_source_image_parsed(self):
        parser = make_parser()
        args = parser.parse_args(["trigger", "quay.io/repo:tag@sha256:abc"])
        assert args.source_image == "quay.io/repo:tag@sha256:abc"

    def test_optional_tag(self):
        parser = make_parser()
        args = parser.parse_args(["trigger", "quay.io/repo:tag@sha256:abc", "custom-tag"])
        assert args.tag == "custom-tag"

    def test_tag_defaults_to_none(self):
        parser = make_parser()
        args = parser.parse_args(["trigger", "quay.io/repo:tag@sha256:abc"])
        assert args.tag is None

    def test_artifact_type_rebuild(self, monkeypatch):
        monkeypatch.delenv("LIGHTWELL_ARTIFACT_TYPE", raising=False)
        parser = make_parser()
        args = parser.parse_args(["trigger", "--artifact-type", "REBUILD", "quay.io/repo:tag@sha256:abc"])
        assert args.artifact_type == "REBUILD"

    def test_artifact_type_remediated(self, monkeypatch):
        monkeypatch.delenv("LIGHTWELL_ARTIFACT_TYPE", raising=False)
        parser = make_parser()
        args = parser.parse_args(["trigger", "--artifact-type", "REMEDIATED", "quay.io/repo:tag@sha256:abc"])
        assert args.artifact_type == "REMEDIATED"

    def test_artifact_type_defaults_to_rebuild(self, monkeypatch):
        monkeypatch.delenv("LIGHTWELL_ARTIFACT_TYPE", raising=False)
        parser = make_parser()
        args = parser.parse_args(["trigger", "quay.io/repo:tag@sha256:abc"])
        assert args.artifact_type == "REBUILD"

    def test_artifact_type_from_env_var(self, monkeypatch):
        monkeypatch.setenv("LIGHTWELL_ARTIFACT_TYPE", "REMEDIATED")
        parser = make_parser()
        args = parser.parse_args(["trigger", "quay.io/repo:tag@sha256:abc"])
        assert args.artifact_type == "REMEDIATED"

    def test_artifact_type_flag_overrides_env_var(self, monkeypatch):
        monkeypatch.setenv("LIGHTWELL_ARTIFACT_TYPE", "REMEDIATED")
        parser = make_parser()
        args = parser.parse_args(["trigger", "--artifact-type", "REBUILD", "quay.io/repo:tag@sha256:abc"])
        assert args.artifact_type == "REBUILD"

    def test_invalid_artifact_type_rejected(self):
        parser = make_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["trigger", "--artifact-type", "INVALID", "quay.io/repo:tag@sha256:abc"])

    def test_has_func_attribute(self):
        parser = make_parser()
        args = parser.parse_args(["trigger", "quay.io/repo:tag@sha256:abc"])
        assert hasattr(args, "func")
        assert callable(args.func)

    def test_command_is_trigger(self):
        parser = make_parser()
        args = parser.parse_args(["trigger", "quay.io/repo:tag@sha256:abc"])
        assert args.command == "trigger"

    def test_top_level_db_flag_still_works(self):
        parser = make_parser()
        args = parser.parse_args(["--db", "/tmp/custom.db", "trigger", "quay.io/repo:tag@sha256:abc"])
        assert str(args.db) == "/tmp/custom.db"


# ---------------------------------------------------------------------------
# register() standalone test
# ---------------------------------------------------------------------------


class TestTriggerRegister:
    """Test the register function that wires up the subparser."""

    def test_registers_trigger_subcommand(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        register(subparsers)

        args = parser.parse_args(["trigger", "quay.io/repo:tag@sha256:abc"])
        assert args.command == "trigger"
        assert args.source_image == "quay.io/repo:tag@sha256:abc"


# ---------------------------------------------------------------------------
# run() function
# ---------------------------------------------------------------------------


class TestTriggerRun:
    """Test the run() function that executes the trigger subcommand."""

    @patch("import_orchestrator.commands.trigger.PipelineRunBuilder")
    @patch("import_orchestrator.commands.trigger.KubeClient")
    def test_successful_trigger_returns_0(self, mock_kube_cls, mock_builder_cls, capsys):
        mock_builder = mock_builder_cls.return_value
        mock_builder.trigger.return_value = "pnc-import-12345"

        args = argparse.Namespace(
            source_image="quay.io/repo:tag@sha256:abc",
            tag=None,
            artifact_type="REBUILD",
        )
        exit_code = run(args)

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "pnc-import-12345" in captured.out

    @patch("import_orchestrator.commands.trigger.PipelineRunBuilder")
    @patch("import_orchestrator.commands.trigger.KubeClient")
    def test_none_result_returns_1(self, mock_kube_cls, mock_builder_cls, capsys):
        mock_builder = mock_builder_cls.return_value
        mock_builder.trigger.return_value = None

        args = argparse.Namespace(
            source_image="quay.io/repo:tag@sha256:abc",
            tag=None,
            artifact_type="REBUILD",
        )
        exit_code = run(args)

        assert exit_code == 1
        captured = capsys.readouterr()
        assert "could not be parsed" in captured.err

    @patch("import_orchestrator.commands.trigger.PipelineRunBuilder")
    @patch("import_orchestrator.commands.trigger.KubeClient")
    def test_trigger_error_returns_1(self, mock_kube_cls, mock_builder_cls, capsys):
        mock_builder = mock_builder_cls.return_value
        mock_builder.trigger.side_effect = TriggerError("skopeo failed")

        args = argparse.Namespace(
            source_image="quay.io/bad:ref",
            tag=None,
            artifact_type="REBUILD",
        )
        exit_code = run(args)

        assert exit_code == 1
        captured = capsys.readouterr()
        assert "skopeo failed" in captured.err

    @patch("import_orchestrator.commands.trigger.PipelineRunBuilder")
    @patch("import_orchestrator.commands.trigger.KubeClient")
    def test_passes_tag_to_trigger(self, mock_kube_cls, mock_builder_cls):
        mock_builder = mock_builder_cls.return_value
        mock_builder.trigger.return_value = "pnc-import-xyz"

        args = argparse.Namespace(
            source_image="quay.io/repo:tag@sha256:abc",
            tag="my-custom-tag",
            artifact_type="REBUILD",
        )
        run(args)

        mock_builder.trigger.assert_called_once_with("quay.io/repo:tag@sha256:abc", "my-custom-tag")

    @patch("import_orchestrator.commands.trigger.PipelineRunBuilder")
    @patch("import_orchestrator.commands.trigger.KubeClient")
    def test_passes_artifact_type_to_builder(self, mock_kube_cls, mock_builder_cls):
        mock_builder = mock_builder_cls.return_value
        mock_builder.trigger.return_value = "pnc-import-xyz"

        args = argparse.Namespace(
            source_image="quay.io/repo:tag@sha256:abc",
            tag=None,
            artifact_type="REMEDIATED",
        )
        run(args)

        mock_builder_cls.assert_called_once()
        call_kwargs = mock_builder_cls.call_args
        assert call_kwargs[1]["artifact_type"] == "REMEDIATED"

    @patch("import_orchestrator.commands.trigger.PipelineRunBuilder")
    @patch("import_orchestrator.commands.trigger.KubeClient")
    def test_output_format_on_success(self, mock_kube_cls, mock_builder_cls, capsys):
        mock_builder = mock_builder_cls.return_value
        mock_builder.trigger.return_value = "pnc-import-99999"

        args = argparse.Namespace(
            source_image="quay.io/repo:tag@sha256:abc",
            tag=None,
            artifact_type="REBUILD",
        )
        run(args)

        captured = capsys.readouterr()
        assert captured.out.strip() == "pipelinerun.tekton.dev/pnc-import-99999 created"


# ---------------------------------------------------------------------------
# Full CLI integration via main()
# ---------------------------------------------------------------------------


class TestTriggerCLIIntegration:
    """Test the trigger subcommand through the full CLI entry point."""

    @patch("import_orchestrator.commands.trigger.PipelineRunBuilder")
    @patch("import_orchestrator.commands.trigger.KubeClient")
    def test_cli_trigger_success(self, mock_kube_cls, mock_builder_cls, monkeypatch, capsys):
        mock_builder = mock_builder_cls.return_value
        mock_builder.trigger.return_value = "pnc-import-cli-test"

        monkeypatch.setattr(
            "sys.argv",
            ["prog", "trigger", "quay.io/repo:v1@sha256:abc"],
        )

        from import_orchestrator.cli import main

        exit_code = main()
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "pnc-import-cli-test" in captured.out

    @patch("import_orchestrator.commands.trigger.PipelineRunBuilder")
    @patch("import_orchestrator.commands.trigger.KubeClient")
    def test_cli_trigger_with_tag_and_artifact_type(self, mock_kube_cls, mock_builder_cls, monkeypatch):
        mock_builder = mock_builder_cls.return_value
        mock_builder.trigger.return_value = "pnc-import-xyz"

        monkeypatch.delenv("LIGHTWELL_ARTIFACT_TYPE", raising=False)
        monkeypatch.setattr(
            "sys.argv",
            [
                "prog",
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

        mock_builder.trigger.assert_called_once_with("quay.io/repo:v1@sha256:abc", "override-tag")
        call_kwargs = mock_builder_cls.call_args
        assert call_kwargs[1]["artifact_type"] == "REMEDIATED"

    @patch("import_orchestrator.commands.trigger.PipelineRunBuilder")
    @patch("import_orchestrator.commands.trigger.KubeClient")
    def test_cli_trigger_error_exits_1(self, mock_kube_cls, mock_builder_cls, monkeypatch, capsys):
        mock_builder = mock_builder_cls.return_value
        mock_builder.trigger.side_effect = TriggerError("connection refused")

        monkeypatch.setattr(
            "sys.argv",
            ["prog", "trigger", "quay.io/repo:bad@sha256:xxx"],
        )

        from import_orchestrator.cli import main

        exit_code = main()
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "connection refused" in captured.err
