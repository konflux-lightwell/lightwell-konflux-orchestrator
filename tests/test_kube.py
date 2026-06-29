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

from import_orchestrator.kube import KubeClient


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
    @patch("import_orchestrator.kube.subprocess.run")
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

    @patch("import_orchestrator.kube.subprocess.run")
    def test_returns_empty_on_error(self, mock_run, kube: KubeClient):
        mock_run.side_effect = subprocess.CalledProcessError(1, "kubectl", stderr="connection refused")

        result = kube.get_running_pipelineruns()
        assert result == []

    @patch("import_orchestrator.kube.subprocess.run")
    def test_handles_empty_output(self, mock_run, kube: KubeClient):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        result = kube.get_running_pipelineruns()
        assert result == []

    @patch("import_orchestrator.kube.subprocess.run")
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
    @patch("import_orchestrator.kube.subprocess.run")
    def test_returns_status(self, mock_run, kube: KubeClient):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="True", stderr=""
        )

        result = kube.get_pipelinerun_status("my-pr")
        assert result is not None
        assert result.name == "my-pr"
        assert result.is_successful is True

    @patch("import_orchestrator.kube.subprocess.run")
    def test_returns_none_on_error(self, mock_run, kube: KubeClient):
        mock_run.side_effect = subprocess.CalledProcessError(1, "kubectl")

        result = kube.get_pipelinerun_status("missing-pr")
        assert result is None

    @patch("import_orchestrator.kube.subprocess.run")
    def test_returns_none_for_unknown_status_string(self, mock_run, kube: KubeClient):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        result = kube.get_pipelinerun_status("my-pr")
        assert result is None


class TestCountRunningImports:
    @patch("import_orchestrator.kube.subprocess.run")
    def test_counts_only_pnc_import_prefix(self, mock_run, kube: KubeClient):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="pnc-import-abc\tUnknown\npnc-import-def\tUnknown\nother-pr\tUnknown\n",
            stderr="",
        )

        assert kube.count_running_imports() == 2
