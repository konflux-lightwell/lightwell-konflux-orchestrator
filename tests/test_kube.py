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
from unittest.mock import patch

import pytest

from import_orchestrator.clients import KubeClient


@pytest.fixture
def kube(monkeypatch):
    """Create a KubeClient with no KONFLUX_TOKEN set."""
    monkeypatch.delenv("KONFLUX_TOKEN", raising=False)
    return KubeClient(namespace="test-ns", cluster_api="https://api.example.com:6443")


@pytest.fixture
def kube_with_token(monkeypatch):
    """Create a KubeClient with KONFLUX_TOKEN set."""
    monkeypatch.setenv("KONFLUX_TOKEN", "test-token-123")
    return KubeClient(namespace="test-ns", cluster_api="https://api.example.com:6443")


class TestKubeClientInit:
    def test_base_args_without_token(self, kube: KubeClient):
        assert kube._kubectl_base_args == ["-n", "test-ns"]

    def test_base_args_with_token(self, kube_with_token: KubeClient):
        args = kube_with_token._kubectl_base_args
        assert "-n" in args
        assert "test-ns" in args
        assert "--token" in args
        assert "test-token-123" in args
        assert "--server" in args
        assert "https://api.example.com:6443" in args


class TestGetRunningPipelineRuns:
    @patch("import_orchestrator.clients.kube.subprocess.run")
    def test_returns_only_running(self, mock_run, kube: KubeClient):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="pr-1\tUnknown\npr-2\tTrue\npr-3\tFalse\npr-4\tUnknown\n",
            stderr="",
        )

        result = kube.get_running_pipelineruns()
        assert len(result) == 2
        assert result[0].name == "pr-1"
        assert result[1].name == "pr-4"

    @patch("import_orchestrator.clients.kube.subprocess.run")
    def test_returns_empty_on_error(self, mock_run, kube: KubeClient):
        mock_run.side_effect = subprocess.CalledProcessError(1, "kubectl", stderr="connection refused")

        result = kube.get_running_pipelineruns()
        assert result == []

    @patch("import_orchestrator.clients.kube.subprocess.run")
    def test_handles_empty_output(self, mock_run, kube: KubeClient):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        result = kube.get_running_pipelineruns()
        assert result == []

    @patch("import_orchestrator.clients.kube.subprocess.run")
    def test_skips_malformed_lines(self, mock_run, kube: KubeClient):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="pr-1\tUnknown\nbadline\npr-2\tInvalid\n",
            stderr="",
        )

        result = kube.get_running_pipelineruns()
        assert len(result) == 1
        assert result[0].name == "pr-1"


class TestGetPipelineRunStatus:
    @patch("import_orchestrator.clients.kube.subprocess.run")
    def test_returns_status(self, mock_run, kube: KubeClient):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="True", stderr="")

        result = kube.get_pipelinerun_status("my-pr")
        assert result is not None
        assert result.name == "my-pr"
        assert result.is_successful is True

    @patch("import_orchestrator.clients.kube.subprocess.run")
    def test_returns_none_on_error(self, mock_run, kube: KubeClient):
        mock_run.side_effect = subprocess.CalledProcessError(1, "kubectl")

        result = kube.get_pipelinerun_status("missing-pr")
        assert result is None

    @patch("import_orchestrator.clients.kube.subprocess.run")
    def test_returns_none_for_unknown_status_string(self, mock_run, kube: KubeClient):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        result = kube.get_pipelinerun_status("my-pr")
        assert result is None


class TestCountRunningImports:
    @patch("import_orchestrator.clients.kube.subprocess.run")
    def test_counts_only_pnc_import_prefix(self, mock_run, kube: KubeClient):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="pnc-import-abc\tUnknown\npnc-import-def\tUnknown\nother-pr\tUnknown\n",
            stderr="",
        )

        assert kube.count_running_imports() == 2


class TestCreatePipelinerun:
    """Test the create_pipelinerun method."""

    @patch("import_orchestrator.clients.kube.subprocess.run")
    def test_returns_generated_name(self, mock_run, kube: KubeClient):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="pipelinerun.tekton.dev/pnc-import-abcde created\n",
            stderr="",
        )

        result = kube.create_pipelinerun("apiVersion: tekton.dev/v1\nkind: PipelineRun\n")
        assert result == "pnc-import-abcde"

    @patch("import_orchestrator.clients.kube.subprocess.run")
    def test_passes_manifest_as_stdin(self, mock_run, kube: KubeClient):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="pipelinerun.tekton.dev/pnc-import-xyz created\n",
            stderr="",
        )

        manifest = "apiVersion: tekton.dev/v1\nkind: PipelineRun\n"
        kube.create_pipelinerun(manifest)

        call_kwargs = mock_run.call_args
        assert call_kwargs[1]["input"] == manifest

    @patch("import_orchestrator.clients.kube.subprocess.run")
    def test_uses_kubectl_create_with_stdin(self, mock_run, kube: KubeClient):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="pipelinerun.tekton.dev/pnc-import-xyz created\n",
            stderr="",
        )

        kube.create_pipelinerun("manifest")

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "kubectl"
        assert "create" in cmd
        assert "-f" in cmd
        assert "-" in cmd

    @patch("import_orchestrator.clients.kube.subprocess.run")
    def test_returns_none_on_subprocess_error(self, mock_run, kube: KubeClient):
        error = subprocess.CalledProcessError(1, "kubectl")
        error.stderr = "forbidden"
        mock_run.side_effect = error

        result = kube.create_pipelinerun("manifest")
        assert result is None

    @patch("import_orchestrator.clients.kube.subprocess.run")
    def test_returns_none_when_name_not_parseable(self, mock_run, kube: KubeClient):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="some unexpected output\n",
            stderr="",
        )

        result = kube.create_pipelinerun("manifest")
        assert result is None

    @patch("import_orchestrator.clients.kube.subprocess.run")
    def test_parses_name_from_stderr(self, mock_run, kube: KubeClient):
        """kubectl sometimes writes the 'created' line to stderr."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="pipelinerun.tekton.dev/pnc-import-stderr created\n",
        )

        result = kube.create_pipelinerun("manifest")
        assert result == "pnc-import-stderr"

    @patch("import_orchestrator.clients.kube.subprocess.run")
    def test_includes_namespace_args(self, mock_run, kube: KubeClient):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="pipelinerun.tekton.dev/pnc-import-ns created\n",
            stderr="",
        )

        kube.create_pipelinerun("manifest")
        cmd = mock_run.call_args[0][0]
        assert "-n" in cmd
        assert "test-ns" in cmd

    @patch("import_orchestrator.clients.kube.subprocess.run")
    def test_includes_token_args_when_set(self, mock_run, kube_with_token: KubeClient):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="pipelinerun.tekton.dev/pnc-import-tok created\n",
            stderr="",
        )

        kube_with_token.create_pipelinerun("manifest")
        cmd = mock_run.call_args[0][0]
        assert "--token" in cmd
        assert "test-token-123" in cmd
