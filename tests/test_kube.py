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

from unittest.mock import MagicMock, patch

import pytest
import requests

from import_orchestrator.clients import KubeClient
from import_orchestrator.clients.kube_api import KubeAuth
from import_orchestrator.engine.errors import TriggerError


def _make_kube_client(monkeypatch, token=None, kubearchive_api=""):
    """Create a KubeClient with mocked auth and API layer."""
    if token:
        monkeypatch.setenv("KONFLUX_TOKEN", token)
    else:
        monkeypatch.delenv("KONFLUX_TOKEN", raising=False)

    mock_api = MagicMock()
    mock_ka_api = MagicMock() if kubearchive_api else None
    with patch("import_orchestrator.clients.kube.resolve_auth") as mock_resolve:
        mock_resolve.return_value = KubeAuth(
            server="https://api.example.com:6443", token=token or "test-token", ca_cert=None
        )
        with patch("import_orchestrator.clients.kube.KubeAPI", return_value=mock_api):
            client = KubeClient(
                namespace="test-ns",
                cluster_api="https://api.example.com:6443",
                kubearchive_api=kubearchive_api,
            )
    client._mock_api = mock_api
    if mock_ka_api is not None:
        client._ka_api = mock_ka_api
        client._mock_ka_api = mock_ka_api
    return client


@pytest.fixture
def kube(monkeypatch):
    """Create a KubeClient with no KONFLUX_TOKEN set."""
    return _make_kube_client(monkeypatch)


@pytest.fixture
def kube_with_ka(monkeypatch):
    """Create a KubeClient with KubeArchive API configured."""
    return _make_kube_client(monkeypatch, kubearchive_api="https://kubearchive.example.com")


class TestKubeClientInit:
    def test_ka_api_is_none_when_not_configured(self, kube: KubeClient):
        assert kube._ka_api is None

    def test_ka_api_is_set_when_configured(self, kube_with_ka: KubeClient):
        assert kube_with_ka._ka_api is not None


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

    def test_falls_back_to_kubearchive(self, kube_with_ka: KubeClient):
        kube_with_ka._mock_api.get.side_effect = requests.HTTPError("404")
        kube_with_ka._mock_ka_api.get.return_value = {
            "status": {"conditions": [{"type": "Succeeded", "status": "True"}]}
        }

        result = kube_with_ka.get_pipelinerun_status("archived-pr")
        assert result is not None
        assert result.name == "archived-pr"
        assert result.is_successful is True

    def test_returns_none_when_both_fail(self, kube_with_ka: KubeClient):
        kube_with_ka._mock_api.get.side_effect = requests.HTTPError("404")
        kube_with_ka._mock_ka_api.get.side_effect = requests.HTTPError("404")

        result = kube_with_ka.get_pipelinerun_status("missing-pr")
        assert result is None

    def test_skips_kubearchive_when_not_configured(self, kube: KubeClient):
        kube._mock_api.get.side_effect = requests.HTTPError("404")

        result = kube.get_pipelinerun_status("missing-pr")
        assert result is None


class TestGetPipelineRunFailureDetail:
    """Test the get_pipelinerun_failure_detail method."""

    def _pr(self, name, *, reason="Failed", message="Tasks Completed: 1 (Failed: 1)", children=()):
        return {
            "status": {
                "conditions": [{"type": "Succeeded", "status": "False", "reason": reason, "message": message}],
                "childReferences": [{"kind": "TaskRun", "name": c} for c in children],
            }
        }

    def _failed_tr(self, *, reason="Failed", message="build step failed", pod_name="pod-1", steps=None):
        return {
            "status": {
                "conditions": [{"type": "Succeeded", "status": "False", "reason": reason, "message": message}],
                "podName": pod_name,
                "steps": steps if steps is not None else [],
            }
        }

    def test_returns_none_when_pipelinerun_missing(self, kube: KubeClient):
        kube._mock_api.get.side_effect = requests.HTTPError("404")

        assert kube.get_pipelinerun_failure_detail("pnc-import-abc") is None

    def test_returns_none_when_no_diagnostics(self, kube: KubeClient):
        kube._mock_api.get.return_value = {"status": {}}

        assert kube.get_pipelinerun_failure_detail("pnc-import-abc") is None

    def test_captures_condition_and_failed_taskrun_with_logs(self, kube: KubeClient):
        pr = self._pr(
            "pnc-import-abc",
            reason="Failed",
            message="Tasks Completed: 1 (Failed: 1)",
            children=["tr-build"],
        )
        tr = self._failed_tr(
            reason="Failed",
            message="TaskRun failed",
            pod_name="pod-abc",
            steps=[
                {"name": "prepare", "container": "step-prepare", "terminated": {"reason": "Completed", "exitCode": 0}},
                {"name": "build", "container": "step-build", "terminated": {"reason": "Error", "exitCode": 1}},
            ],
        )

        def get_side_effect(path):
            if path.endswith("/pipelineruns/pnc-import-abc"):
                return pr
            if path.endswith("/taskruns/tr-build"):
                return tr
            raise requests.HTTPError("404")

        kube._mock_api.get.side_effect = get_side_effect
        kube._mock_api.get_text.return_value = "compiling wheel...\nfatal error: Python.h: No such file\n"

        detail = kube.get_pipelinerun_failure_detail("pnc-import-abc")

        assert detail is not None
        assert "PipelineRun pnc-import-abc: Failed - Tasks Completed: 1 (Failed: 1)" in detail
        assert "TaskRun tr-build: Failed - TaskRun failed" in detail
        assert "step 'build' terminated: reason=Error exitCode=1" in detail
        # The completed step is not reported as a failure.
        assert "step 'prepare'" not in detail
        # The failing step's container logs are tailed in.
        assert "fatal error: Python.h" in detail
        kube._mock_api.get_text.assert_called_once_with(
            "/api/v1/namespaces/test-ns/pods/pod-abc/log",
            container="step-build",
            tailLines=20,
        )

    def test_degrades_when_logs_unavailable(self, kube: KubeClient):
        pr = self._pr("pnc-import-abc", children=["tr-build"])
        tr = self._failed_tr(
            pod_name="pod-abc",
            steps=[{"name": "build", "container": "step-build", "terminated": {"reason": "Error", "exitCode": 1}}],
        )

        def get_side_effect(path):
            if path.endswith("/pipelineruns/pnc-import-abc"):
                return pr
            if path.endswith("/taskruns/tr-build"):
                return tr
            raise requests.HTTPError("404")

        kube._mock_api.get.side_effect = get_side_effect
        kube._mock_api.get_text.side_effect = requests.HTTPError("pod gone")

        detail = kube.get_pipelinerun_failure_detail("pnc-import-abc")

        assert detail is not None
        assert "step 'build' terminated: reason=Error exitCode=1" in detail
        assert "logs (" not in detail

    def test_skips_successful_taskruns(self, kube: KubeClient):
        pr = self._pr("pnc-import-abc", children=["tr-ok"])
        tr_ok = {"status": {"conditions": [{"type": "Succeeded", "status": "True", "reason": "Succeeded"}]}}

        def get_side_effect(path):
            if path.endswith("/pipelineruns/pnc-import-abc"):
                return pr
            if path.endswith("/taskruns/tr-ok"):
                return tr_ok
            raise requests.HTTPError("404")

        kube._mock_api.get.side_effect = get_side_effect

        detail = kube.get_pipelinerun_failure_detail("pnc-import-abc")

        assert detail is not None
        assert "TaskRun tr-ok" not in detail
        assert "PipelineRun pnc-import-abc" in detail

    def test_falls_back_to_kubearchive(self, kube_with_ka: KubeClient):
        pr = self._pr("archived-pr", children=[])
        kube_with_ka._mock_api.get.side_effect = requests.HTTPError("404")
        kube_with_ka._mock_ka_api.get.return_value = pr

        detail = kube_with_ka.get_pipelinerun_failure_detail("archived-pr")

        assert detail is not None
        assert "PipelineRun archived-pr: Failed" in detail

    def test_truncates_to_max_chars(self, kube: KubeClient):
        pr = self._pr("pnc-import-abc", message="x" * 10000, children=[])
        kube._mock_api.get.return_value = pr

        detail = kube.get_pipelinerun_failure_detail("pnc-import-abc")

        assert detail is not None
        assert len(detail) <= KubeClient._MAX_DETAIL_CHARS


class TestCountRunningImports:
    def test_counts_only_pnc_import_prefix(self, kube: KubeClient):
        kube._mock_api.list.return_value = {
            "items": [
                {"metadata": {"name": "pnc-import-abc"}, "status": {"conditions": [{"status": "Unknown"}]}},
                {"metadata": {"name": "pnc-import-def"}, "status": {"conditions": [{"status": "Unknown"}]}},
                {"metadata": {"name": "other-pr"}, "status": {"conditions": [{"status": "Unknown"}]}},
            ]
        }

        assert kube.count_running_imports("pnc-import-") == 2


class TestFindSnapshotByPipelinerun:
    def test_returns_snapshot_name(self, kube: KubeClient):
        kube._mock_api.list.return_value = {"items": [{"metadata": {"name": "snap-abc"}}]}

        result = kube.find_snapshot_by_pipelinerun("pr-1")
        assert result == "snap-abc"
        kube._mock_api.list.assert_called_once_with(
            "/apis/appstudio.redhat.com/v1alpha1/namespaces/test-ns/snapshots",
            labelSelector="appstudio.openshift.io/build-pipelinerun=pr-1",
        )

    def test_returns_none_when_no_items(self, kube: KubeClient):
        kube._mock_api.list.return_value = {"items": []}

        assert kube.find_snapshot_by_pipelinerun("pr-1") is None

    def test_returns_none_on_http_error(self, kube: KubeClient):
        kube._mock_api.list.side_effect = requests.HTTPError("404")

        assert kube.find_snapshot_by_pipelinerun("pr-1") is None


class TestFindReleaseForSnapshot:
    def test_finds_active_release(self, kube: KubeClient):
        kube._mock_api.list.return_value = {
            "items": [
                {
                    "metadata": {"name": "release-1"},
                    "spec": {"snapshot": "snap-1"},
                    "status": {"conditions": [{"type": "Released", "status": "Unknown", "reason": "Progressing"}]},
                }
            ]
        }

        assert kube.find_release_for_snapshot("snap-1") == "release-1"

    def test_skips_terminally_failed_release(self, kube: KubeClient):
        kube._mock_api.list.return_value = {
            "items": [
                {
                    "metadata": {"name": "release-bad"},
                    "spec": {"snapshot": "snap-1"},
                    "status": {"conditions": [{"type": "Released", "status": "False", "reason": "Failed"}]},
                }
            ]
        }

        assert kube.find_release_for_snapshot("snap-1") is None

    def test_does_not_skip_progressing_false(self, kube: KubeClient):
        kube._mock_api.list.return_value = {
            "items": [
                {
                    "metadata": {"name": "release-prog"},
                    "spec": {"snapshot": "snap-1"},
                    "status": {"conditions": [{"type": "Released", "status": "False", "reason": "Progressing"}]},
                }
            ]
        }

        assert kube.find_release_for_snapshot("snap-1") == "release-prog"

    def test_returns_none_when_no_match(self, kube: KubeClient):
        kube._mock_api.list.return_value = {
            "items": [
                {"metadata": {"name": "release-other"}, "spec": {"snapshot": "other-snap"}, "status": {}},
            ]
        }

        assert kube.find_release_for_snapshot("snap-1") is None

    def test_returns_none_on_http_error(self, kube: KubeClient):
        kube._mock_api.list.side_effect = requests.HTTPError("500")

        assert kube.find_release_for_snapshot("snap-1") is None


class TestGetReleaseStatus:
    def test_returns_true_on_success(self, kube: KubeClient):
        kube._mock_api.get.return_value = {
            "status": {"conditions": [{"type": "Released", "status": "True", "reason": "Succeeded"}]}
        }

        assert kube.get_release_status("rel-1") == "True"

    def test_returns_false_on_terminal_failure(self, kube: KubeClient):
        kube._mock_api.get.return_value = {
            "status": {"conditions": [{"type": "Released", "status": "False", "reason": "Failed"}]}
        }

        assert kube.get_release_status("rel-1") == "False"

    def test_returns_unknown_when_progressing(self, kube: KubeClient):
        kube._mock_api.get.return_value = {
            "status": {"conditions": [{"type": "Released", "status": "False", "reason": "Progressing"}]}
        }

        assert kube.get_release_status("rel-1") == "Unknown"

    def test_returns_unknown_when_no_released_condition(self, kube: KubeClient):
        kube._mock_api.get.return_value = {"status": {"conditions": [{"type": "Other", "status": "True"}]}}

        assert kube.get_release_status("rel-1") == "Unknown"

    def test_returns_none_on_http_error(self, kube: KubeClient):
        kube._mock_api.get.side_effect = requests.HTTPError("404")

        assert kube.get_release_status("rel-1") is None


class TestFindReleasePlanForSnapshot:
    def test_finds_matching_plan(self, kube: KubeClient):
        kube._mock_api.get.return_value = {"metadata": {"labels": {"appstudio.openshift.io/application": "my-app"}}}
        kube._mock_api.list.return_value = {
            "items": [
                {"metadata": {"name": "plan-other"}, "spec": {"application": "other-app"}},
                {"metadata": {"name": "plan-mine"}, "spec": {"application": "my-app"}},
            ]
        }

        assert kube.find_release_plan_for_snapshot("snap-1") == "plan-mine"

    def test_returns_none_when_no_application_label(self, kube: KubeClient):
        kube._mock_api.get.return_value = {"metadata": {"labels": {}}}

        assert kube.find_release_plan_for_snapshot("snap-1") is None
        kube._mock_api.list.assert_not_called()

    def test_returns_none_when_no_matching_plan(self, kube: KubeClient):
        kube._mock_api.get.return_value = {"metadata": {"labels": {"appstudio.openshift.io/application": "my-app"}}}
        kube._mock_api.list.return_value = {
            "items": [
                {"metadata": {"name": "plan-other"}, "spec": {"application": "other-app"}},
            ]
        }

        assert kube.find_release_plan_for_snapshot("snap-1") is None

    def test_returns_none_on_http_error(self, kube: KubeClient):
        kube._mock_api.get.side_effect = requests.HTTPError("404")

        assert kube.find_release_plan_for_snapshot("snap-1") is None


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

    def test_raises_trigger_error_on_http_error(self, kube: KubeClient):
        kube._mock_api.create.side_effect = requests.HTTPError("403 Forbidden")

        with pytest.raises(TriggerError):
            kube.create_pipelinerun({"kind": "PipelineRun"})

    def test_http_error_surfaces_api_message(self, kube: KubeClient):
        response = MagicMock()
        response.json.return_value = {"kind": "Status", "message": "non-existent variable in value"}
        kube._mock_api.create.side_effect = requests.HTTPError("400 Bad Request", response=response)

        with pytest.raises(TriggerError, match="non-existent variable in value"):
            kube.create_pipelinerun({"kind": "PipelineRun"})

    def test_returns_none_when_name_missing(self, kube: KubeClient):
        kube._mock_api.create.return_value = {"metadata": {}}

        result = kube.create_pipelinerun({"kind": "PipelineRun"})
        assert result is None

    def test_uses_correct_namespace_in_path(self, kube: KubeClient):
        kube._mock_api.create.return_value = {"metadata": {"name": "pnc-import-ns"}}

        kube.create_pipelinerun({"kind": "PipelineRun"})

        api_path = kube._mock_api.create.call_args[0][0]
        assert "/namespaces/test-ns/" in api_path


class TestCreateRelease:
    def test_returns_generated_name(self, kube: KubeClient):
        kube._mock_api.create.return_value = {"metadata": {"name": "pnc-import-abc123"}}

        result = kube.create_release("my-snapshot", "my-release-plan", "pnc-import-")
        assert result == "pnc-import-abc123"

    def test_passes_correct_manifest(self, kube: KubeClient):
        kube._mock_api.create.return_value = {"metadata": {"name": "pnc-import-xyz"}}

        kube.create_release("snap-1", "plan-1", "pnc-import-")

        kube._mock_api.create.assert_called_once_with(
            "/apis/appstudio.redhat.com/v1alpha1/namespaces/test-ns/releases",
            {
                "apiVersion": "appstudio.redhat.com/v1alpha1",
                "kind": "Release",
                "metadata": {"generateName": "pnc-import-", "namespace": "test-ns"},
                "spec": {"releasePlan": "plan-1", "snapshot": "snap-1"},
            },
        )

    def test_returns_none_on_http_error(self, kube: KubeClient):
        kube._mock_api.create.side_effect = requests.HTTPError("403 Forbidden")

        result = kube.create_release("snap-1", "plan-1", "pnc-import-")
        assert result is None

    def test_returns_none_when_name_missing(self, kube: KubeClient):
        kube._mock_api.create.return_value = {"metadata": {}}

        result = kube.create_release("snap-1", "plan-1", "pnc-import-")
        assert result is None

    def test_uses_correct_namespace_in_path(self, kube: KubeClient):
        kube._mock_api.create.return_value = {"metadata": {"name": "pnc-import-ns"}}

        kube.create_release("snap-1", "plan-1", "pnc-import-")

        api_path = kube._mock_api.create.call_args[0][0]
        assert "/namespaces/test-ns/" in api_path
