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
from unittest.mock import MagicMock, patch

import pytest
import requests

from import_orchestrator.clients import KubeClient
from import_orchestrator.clients.kube_api import KubeAuth


def _make_kube_client(monkeypatch, token=None):
    """Create a KubeClient with mocked auth and API layer."""
    if token:
        monkeypatch.setenv("KONFLUX_TOKEN", token)
    else:
        monkeypatch.delenv("KONFLUX_TOKEN", raising=False)

    mock_api = MagicMock()
    with patch("import_orchestrator.clients.kube.resolve_auth") as mock_resolve:
        mock_resolve.return_value = KubeAuth(
            server="https://api.example.com:6443", token=token or "test-token", ca_cert=None
        )
        with patch("import_orchestrator.clients.kube.KubeAPI", return_value=mock_api):
            client = KubeClient(namespace="test-ns", cluster_api="https://api.example.com:6443")
    client._mock_api = mock_api
    return client


@pytest.fixture
def kube(monkeypatch):
    """Create a KubeClient with no KONFLUX_TOKEN set."""
    return _make_kube_client(monkeypatch)


@pytest.fixture
def kube_with_token(monkeypatch):
    """Create a KubeClient with KONFLUX_TOKEN set."""
    return _make_kube_client(monkeypatch, token="test-token-123")


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
    def test_returns_only_running(self, kube: KubeClient):
        kube._mock_api.list.return_value = {
            "items": [
                {"metadata": {"name": "pr-1"}, "status": {"conditions": [{"status": "Unknown"}]}},
                {"metadata": {"name": "pr-2"}, "status": {"conditions": [{"status": "True"}]}},
                {"metadata": {"name": "pr-3"}, "status": {"conditions": [{"status": "False"}]}},
                {"metadata": {"name": "pr-4"}, "status": {"conditions": [{"status": "Unknown"}]}},
            ]
        }

        result = kube.get_running_pipelineruns()
        assert len(result) == 2
        assert result[0].name == "pr-1"
        assert result[1].name == "pr-4"

    def test_returns_empty_on_error(self, kube: KubeClient):
        kube._mock_api.list.side_effect = requests.ConnectionError("connection refused")

        result = kube.get_running_pipelineruns()
        assert result == []

    def test_handles_empty_items(self, kube: KubeClient):
        kube._mock_api.list.return_value = {"items": []}

        result = kube.get_running_pipelineruns()
        assert result == []

    def test_skips_items_without_conditions(self, kube: KubeClient):
        kube._mock_api.list.return_value = {
            "items": [
                {"metadata": {"name": "pr-1"}, "status": {"conditions": [{"status": "Unknown"}]}},
                {"metadata": {"name": "pr-2"}, "status": {}},
                {"metadata": {"name": "pr-3"}, "status": {"conditions": [{"status": "Invalid"}]}},
            ]
        }

        result = kube.get_running_pipelineruns()
        assert len(result) == 1
        assert result[0].name == "pr-1"


class TestGetPipelineRunStatus:
    def test_returns_status_from_api(self, kube: KubeClient):
        kube._mock_api.get.return_value = {"status": {"conditions": [{"status": "True"}]}}

        result = kube.get_pipelinerun_status("my-pr")
        assert result is not None
        assert result.name == "my-pr"
        assert result.is_successful is True

    def test_returns_running_status(self, kube: KubeClient):
        kube._mock_api.get.return_value = {"status": {"conditions": [{"status": "Unknown"}]}}

        result = kube.get_pipelinerun_status("my-pr")
        assert result is not None
        assert result.is_running is True

    def test_returns_none_when_no_conditions(self, kube: KubeClient):
        kube._mock_api.get.return_value = {"status": {}}

        result = kube.get_pipelinerun_status("my-pr")
        assert result is None

    @patch("import_orchestrator.clients.kube.subprocess.run")
    def test_falls_back_to_kubearchive(self, mock_run, kube: KubeClient):
        kube._mock_api.get.side_effect = requests.HTTPError("404")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"status":{"conditions":[{"type":"Succeeded","status":"True"}]}}',
            stderr="",
        )

        result = kube.get_pipelinerun_status("archived-pr")
        assert result is not None
        assert result.name == "archived-pr"
        assert result.is_successful is True

    @patch("import_orchestrator.clients.kube.subprocess.run")
    def test_returns_none_when_both_fail(self, mock_run, kube: KubeClient):
        kube._mock_api.get.side_effect = requests.HTTPError("404")
        mock_run.side_effect = subprocess.CalledProcessError(1, "kubectl")

        result = kube.get_pipelinerun_status("missing-pr")
        assert result is None


class TestCountRunningImports:
    def test_counts_only_pnc_import_prefix(self, kube: KubeClient):
        kube._mock_api.list.return_value = {
            "items": [
                {"metadata": {"name": "pnc-import-abc"}, "status": {"conditions": [{"status": "Unknown"}]}},
                {"metadata": {"name": "pnc-import-def"}, "status": {"conditions": [{"status": "Unknown"}]}},
                {"metadata": {"name": "other-pr"}, "status": {"conditions": [{"status": "Unknown"}]}},
            ]
        }

        assert kube.count_running_imports() == 2


class TestCreatePipelinerun:
    """Test the create_pipelinerun method."""

    def test_returns_generated_name(self, kube: KubeClient):
        kube._mock_api.create.return_value = {"metadata": {"name": "pnc-import-abcde"}}

        result = kube.create_pipelinerun({"apiVersion": "tekton.dev/v1", "kind": "PipelineRun"})
        assert result == "pnc-import-abcde"

    def test_passes_manifest_as_body(self, kube: KubeClient):
        kube._mock_api.create.return_value = {"metadata": {"name": "pnc-import-xyz"}}

        manifest = {"apiVersion": "tekton.dev/v1", "kind": "PipelineRun"}
        kube.create_pipelinerun(manifest)

        kube._mock_api.create.assert_called_once_with(
            "/apis/tekton.dev/v1/namespaces/test-ns/pipelineruns",
            manifest,
        )

    def test_returns_none_on_http_error(self, kube: KubeClient):
        kube._mock_api.create.side_effect = requests.HTTPError("403 Forbidden")

        result = kube.create_pipelinerun({"kind": "PipelineRun"})
        assert result is None

    def test_returns_none_when_name_missing(self, kube: KubeClient):
        kube._mock_api.create.return_value = {"metadata": {}}

        result = kube.create_pipelinerun({"kind": "PipelineRun"})
        assert result is None

    def test_uses_correct_namespace_in_path(self, kube: KubeClient):
        kube._mock_api.create.return_value = {"metadata": {"name": "pnc-import-ns"}}

        kube.create_pipelinerun({"kind": "PipelineRun"})

        api_path = kube._mock_api.create.call_args[0][0]
        assert "/namespaces/test-ns/" in api_path
