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

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from import_orchestrator import pipelinerun_creator
from import_orchestrator.clients import KubeClient
from import_orchestrator.clients.kube_api import KubeAuth
from import_orchestrator.engine.errors import PipelineRunReconciliationError, TriggerError


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


class TestApiErrorLogging:
    @pytest.mark.parametrize("status_code", [403, 500])
    @pytest.mark.parametrize(
        "method,args,api_method,context,expected",
        [
            ("get_running_pipelineruns", (), "list", "list PipelineRuns", []),
            ("get_pipelinerun_status", ("pr-1",), "get", "get PipelineRun pr-1", None),
            ("_fetch_resource", ("/taskruns/tr-1",), "get", "get resource /taskruns/tr-1", None),
            ("_pod_log_tail", ("pod-1", "step-build"), "get_text", "pod pod-1, container step-build", None),
            ("find_snapshot_by_pipelinerun", ("pr-1",), "list", "find Snapshot for PipelineRun pr-1", None),
            ("find_release_plan_for_snapshot", ("snap-1",), "get", "get Snapshot snap-1", None),
            ("find_release_plan_for_snapshot", ("snap-1",), "list", "list ReleasePlans for Snapshot snap-1", None),
            ("create_release", ("snap-1", "plan-1", "release-"), "create", "ReleasePlan plan-1", None),
            ("find_release_for_snapshot", ("snap-1",), "list", "find Release for Snapshot snap-1", None),
            ("get_release_status", ("release-1",), "get", "get Release release-1 status", None),
        ],
    )
    def test_logs_api_message_with_operation_and_namespace(
        self, kube, capsys, status_code, method, args, api_method, context, expected
    ):
        response = requests.Response()
        response.status_code = status_code
        detail = "API rejected the operation\nAdditional server diagnostic"
        response._content = json.dumps({"kind": "Status", "message": detail}).encode()
        kube._mock_api.get.return_value = {"metadata": {"labels": {"appstudio.openshift.io/application": "app-1"}}}
        getattr(kube._mock_api, api_method).side_effect = requests.HTTPError(
            f"{status_code} API error", response=response
        )

        assert getattr(kube, method)(*args) == expected

        stderr = capsys.readouterr().err
        assert detail in stderr
        assert context in stderr
        assert "namespace test-ns" in stderr

    @pytest.mark.parametrize(
        "method,args,api_method",
        [
            ("get_pipelinerun_status", ("pr-1",), "get"),
            ("_fetch_resource", ("/taskruns/tr-1",), "get"),
            ("_pod_log_tail", ("pod-1", "step-build"), "get_text"),
            ("find_release_plan_for_snapshot", ("snap-1",), "get"),
            ("get_release_status", ("release-1",), "get"),
        ],
    )
    def test_expected_missing_resources_remain_quiet(self, kube, capsys, method, args, api_method):
        response = requests.Response()
        response.status_code = 404
        getattr(kube._mock_api, api_method).side_effect = requests.HTTPError("404 Not Found", response=response)

        assert getattr(kube, method)(*args) is None

        assert capsys.readouterr().err == ""

    @pytest.mark.parametrize("method", ["get_pipelinerun_status", "_fetch_resource"])
    def test_archive_failure_is_identified_after_quiet_cluster_404(self, kube_with_ka, capsys, method):
        missing = requests.Response()
        missing.status_code = 404
        kube_with_ka._mock_api.get.side_effect = requests.HTTPError("404 Not Found", response=missing)
        failure = requests.Response()
        failure.status_code = 503
        failure._content = b'{"message": "Archive temporarily unavailable"}'
        kube_with_ka._mock_ka_api.get.side_effect = requests.HTTPError("503 Unavailable", response=failure)

        assert getattr(kube_with_ka, method)("pr-1") is None

        stderr = capsys.readouterr().err
        assert "KubeArchive" in stderr
        assert "Archive temporarily unavailable" in stderr
        assert "404" not in stderr

    def test_release_creation_logs_non_json_response_body(self, kube, capsys):
        response = requests.Response()
        response.status_code = 502
        response._content = b"Upstream proxy unavailable"
        kube._mock_api.create.side_effect = requests.HTTPError("502 Bad Gateway", response=response)

        assert kube.create_release("snap-1", "plan-1", "release-") is None

        assert "Upstream proxy unavailable" in capsys.readouterr().err

    def test_release_status_logs_connection_error(self, kube, capsys):
        kube._mock_api.get.side_effect = requests.ConnectionError("Connection refused")

        assert kube.get_release_status("release-1") is None

        stderr = capsys.readouterr().err
        assert "release-1" in stderr
        assert "Connection refused" in stderr


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

    def test_returns_newest_across_any_plan(self, kube: KubeClient):
        """Matches a Release against any plan and, when several match, picks the newest."""
        kube._mock_api.list.return_value = {
            "items": [
                {
                    "metadata": {"name": "rel-new", "creationTimestamp": "2026-02-01T00:00:00Z"},
                    "spec": {"snapshot": "snap-1", "releasePlan": "remediated-build-prod"},
                    "status": {"conditions": [{"type": "Released", "status": "True", "reason": "Succeeded"}]},
                },
                {
                    "metadata": {"name": "rel-old", "creationTimestamp": "2026-01-01T00:00:00Z"},
                    "spec": {"snapshot": "snap-1", "releasePlan": "remediated-build"},
                    "status": {"conditions": [{"type": "Released", "status": "True", "reason": "Succeeded"}]},
                },
            ]
        }

        assert kube.find_release_for_snapshot("snap-1") == "rel-new"


class TestFindReleaseForSnapshotAndPlan:
    def test_ignores_release_against_a_different_plan(self, kube: KubeClient):
        """A stage release for this snapshot must not be mistaken for a prod release."""
        kube._mock_api.list.return_value = {
            "items": [
                {
                    "metadata": {"name": "rel-stage"},
                    "spec": {"snapshot": "snap-1", "releasePlan": "remediated-build"},
                    "status": {"conditions": [{"type": "Released", "status": "True", "reason": "Succeeded"}]},
                }
            ]
        }

        assert kube.find_release_for_snapshot_and_plan("snap-1", "remediated-build-prod") is None

    def test_finds_release_against_the_named_plan(self, kube: KubeClient):
        kube._mock_api.list.return_value = {
            "items": [
                {
                    "metadata": {"name": "rel-stage"},
                    "spec": {"snapshot": "snap-1", "releasePlan": "remediated-build"},
                    "status": {},
                },
                {
                    "metadata": {"name": "rel-prod"},
                    "spec": {"snapshot": "snap-1", "releasePlan": "remediated-build-prod"},
                    "status": {"conditions": [{"type": "Released", "status": "True", "reason": "Succeeded"}]},
                },
            ]
        }

        assert kube.find_release_for_snapshot_and_plan("snap-1", "remediated-build-prod") == "rel-prod"

    def test_ignores_other_snapshots(self, kube: KubeClient):
        kube._mock_api.list.return_value = {
            "items": [
                {
                    "metadata": {"name": "rel-other"},
                    "spec": {"snapshot": "snap-2", "releasePlan": "remediated-build-prod"},
                    "status": {},
                }
            ]
        }

        assert kube.find_release_for_snapshot_and_plan("snap-1", "remediated-build-prod") is None

    def test_skips_terminally_failed_release(self, kube: KubeClient):
        """A failed prod release must not be adopted — the operator needs a fresh attempt."""
        kube._mock_api.list.return_value = {
            "items": [
                {
                    "metadata": {"name": "rel-bad"},
                    "spec": {"snapshot": "snap-1", "releasePlan": "remediated-build-prod"},
                    "status": {"conditions": [{"type": "Released", "status": "False", "reason": "Failed"}]},
                }
            ]
        }

        assert kube.find_release_for_snapshot_and_plan("snap-1", "remediated-build-prod") is None

    def test_does_not_skip_progressing_false(self, kube: KubeClient):
        kube._mock_api.list.return_value = {
            "items": [
                {
                    "metadata": {"name": "rel-live"},
                    "spec": {"snapshot": "snap-1", "releasePlan": "remediated-build-prod"},
                    "status": {"conditions": [{"type": "Released", "status": "False", "reason": "Progressing"}]},
                }
            ]
        }

        assert kube.find_release_for_snapshot_and_plan("snap-1", "remediated-build-prod") == "rel-live"

    def test_returns_none_on_http_error(self, kube: KubeClient):
        kube._mock_api.list.side_effect = requests.HTTPError("500")

        assert kube.find_release_for_snapshot_and_plan("snap-1", "remediated-build-prod") is None

    def test_returns_newest_when_multiple_match(self, kube: KubeClient):
        """Adoption must be deterministic: pick the newest by creationTimestamp, not API order."""
        kube._mock_api.list.return_value = {
            "items": [
                {
                    "metadata": {"name": "rel-old", "creationTimestamp": "2026-01-01T00:00:00Z"},
                    "spec": {"snapshot": "snap-1", "releasePlan": "remediated-build-prod"},
                    "status": {"conditions": [{"type": "Released", "status": "True", "reason": "Succeeded"}]},
                },
                {
                    "metadata": {"name": "rel-new", "creationTimestamp": "2026-02-01T00:00:00Z"},
                    "spec": {"snapshot": "snap-1", "releasePlan": "remediated-build-prod"},
                    "status": {"conditions": [{"type": "Released", "status": "True", "reason": "Succeeded"}]},
                },
            ]
        }

        assert kube.find_release_for_snapshot_and_plan("snap-1", "remediated-build-prod") == "rel-new"


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

    def test_excludes_manual_only_plan(self, kube: KubeClient):
        """A plan labelled auto-release=false is operator-driven and never auto-selected."""
        kube._mock_api.get.return_value = {"metadata": {"labels": {"appstudio.openshift.io/application": "my-app"}}}
        kube._mock_api.list.return_value = {
            "items": [
                {
                    "metadata": {
                        "name": "plan-prod",
                        "labels": {"release.appstudio.openshift.io/auto-release": "false"},
                    },
                    "spec": {"application": "my-app"},
                },
                {
                    "metadata": {
                        "name": "plan-stage",
                        "labels": {"release.appstudio.openshift.io/auto-release": "true"},
                    },
                    "spec": {"application": "my-app"},
                },
            ]
        }

        assert kube.find_release_plan_for_snapshot("snap-1") == "plan-stage"

    def test_keeps_plan_without_auto_release_label(self, kube: KubeClient):
        """Only an explicit 'false' excludes a plan — a missing label is not a signal."""
        kube._mock_api.get.return_value = {"metadata": {"labels": {"appstudio.openshift.io/application": "my-app"}}}
        kube._mock_api.list.return_value = {
            "items": [
                {"metadata": {"name": "plan-unlabelled"}, "spec": {"application": "my-app"}},
            ]
        }

        assert kube.find_release_plan_for_snapshot("snap-1") == "plan-unlabelled"

    def test_handles_null_labels_mapping(self, kube: KubeClient):
        """A manifest with `labels: null` must not raise AttributeError — treat as no labels."""
        kube._mock_api.get.return_value = {"metadata": {"labels": {"appstudio.openshift.io/application": "my-app"}}}
        kube._mock_api.list.return_value = {
            "items": [
                {"metadata": {"name": "plan-null-labels", "labels": None}, "spec": {"application": "my-app"}},
            ]
        }

        assert kube.find_release_plan_for_snapshot("snap-1") == "plan-null-labels"

    def test_returns_none_when_ambiguous(self, kube: KubeClient, capsys: pytest.CaptureFixture[str]):
        """Two auto-releasing plans for one application is unresolvable — refuse rather than guess."""
        kube._mock_api.get.return_value = {"metadata": {"labels": {"appstudio.openshift.io/application": "my-app"}}}
        kube._mock_api.list.return_value = {
            "items": [
                {"metadata": {"name": "plan-a"}, "spec": {"application": "my-app"}},
                {"metadata": {"name": "plan-b"}, "spec": {"application": "my-app"}},
            ]
        }

        assert kube.find_release_plan_for_snapshot("snap-1") is None

        stderr = capsys.readouterr().err
        assert "plan-a" in stderr
        assert "plan-b" in stderr
        assert "my-app" in stderr


COLLECTION_PATH = "/apis/tekton.dev/v1/namespaces/test-ns/pipelineruns"
IMPORT_NAME = "pnc-import-abcde"
IMPORT_IDENTITY = '{"import":"one"}'


def _named_manifest(name=IMPORT_NAME, identity=IMPORT_IDENTITY):
    """Return a PipelineRun manifest with a deterministic name and import identity."""
    return {
        "apiVersion": "tekton.dev/v1",
        "kind": "PipelineRun",
        "metadata": {
            "name": name,
            "annotations": {"lightwell.redhat.com/import-identity": identity},
        },
    }


def _generate_name_manifest():
    """Return a PipelineRun manifest whose name the cluster chooses."""
    return {"apiVersion": "tekton.dev/v1", "kind": "PipelineRun", "metadata": {"generateName": "pnc-import-"}}


def _http_error(status_code, message=None):
    """Return an HTTPError with a real response, optionally carrying a Status message."""
    response = requests.Response()
    response.status_code = status_code
    if message is not None:
        response._content = json.dumps({"kind": "Status", "message": message}).encode()
    return requests.HTTPError(f"{status_code} error", response=response)


def _failure(kind):
    """Return a retryable or non-retryable POST failure named by kind."""
    transport_errors = {
        "connect": requests.ConnectTimeout,
        "read": requests.ReadTimeout,
        "connection": requests.ConnectionError,
    }
    if kind in transport_errors:
        return transport_errors[kind](kind)
    return _http_error(int(kind))


@pytest.fixture
def no_wait(monkeypatch):
    """Make retry sleeps instant and jitter zero. Returns the list of requested sleeps."""
    sleeps: list[float] = []
    monkeypatch.setattr(pipelinerun_creator, "_sleep", sleeps.append)
    monkeypatch.setattr(pipelinerun_creator, "_jitter", lambda _low, _high: 0.0)
    return sleeps


class TestCreatePipelinerun:
    """Test the create_pipelinerun method."""

    def test_returns_generated_name(self, kube: KubeClient):
        """A generateName manifest is created with one POST and returns the server's name."""
        kube._mock_api.create.return_value = {"metadata": {"name": "pnc-import-abcde"}}

        assert kube.create_pipelinerun(_generate_name_manifest()) == "pnc-import-abcde"

    def test_passes_manifest_as_body(self, kube: KubeClient):
        kube._mock_api.create.return_value = {"metadata": {"name": "pnc-import-xyz"}}
        manifest = _generate_name_manifest()

        kube.create_pipelinerun(manifest)

        kube._mock_api.create.assert_called_once_with(COLLECTION_PATH, manifest)

    def test_raises_trigger_error_on_http_error(self, kube: KubeClient):
        kube._mock_api.create.side_effect = requests.HTTPError("403 Forbidden")

        with pytest.raises(TriggerError):
            kube.create_pipelinerun(_generate_name_manifest())

    @pytest.mark.parametrize("manifest", [_generate_name_manifest(), _named_manifest()], ids=["generate-name", "named"])
    def test_http_error_surfaces_api_message(self, kube: KubeClient, manifest):
        """The cluster's rejection reason reaches the TriggerError message for both manifest kinds."""
        kube._mock_api.get.side_effect = _http_error(404)
        kube._mock_api.create.side_effect = _http_error(400, "non-existent variable in value")

        with pytest.raises(TriggerError, match="non-existent variable in value"):
            kube.create_pipelinerun(manifest)

    def test_returns_none_when_name_missing(self, kube: KubeClient):
        kube._mock_api.create.return_value = {"metadata": {}}

        assert kube.create_pipelinerun(_generate_name_manifest()) is None

    def test_uses_correct_namespace_in_path(self, kube: KubeClient):
        kube._mock_api.create.return_value = {"metadata": {"name": "pnc-import-ns"}}

        kube.create_pipelinerun(_generate_name_manifest())

        api_path = kube._mock_api.create.call_args[0][0]
        assert "/namespaces/test-ns/" in api_path

    @pytest.mark.parametrize("failure_kind", ["connect", "503"])
    def test_generate_name_manifest_is_never_retried(self, kube: KubeClient, no_wait, failure_kind):
        """Retrying a generateName POST could create a second PipelineRun, so it gets one attempt."""
        kube._mock_api.create.side_effect = _failure(failure_kind)

        with pytest.raises(TriggerError, match="failed to create PipelineRun"):
            kube.create_pipelinerun(_generate_name_manifest())

        kube._mock_api.create.assert_called_once()
        kube._mock_api.get.assert_not_called()
        assert no_wait == []

    @pytest.mark.parametrize("existing", [True, False])
    def test_reuses_or_creates_after_exact_name_lookup(self, kube: KubeClient, existing):
        manifest = _named_manifest(name="pnc-import-xyz")
        if existing:
            kube._mock_api.get.return_value = {"metadata": manifest["metadata"]}
        else:
            kube._mock_api.get.side_effect = _http_error(404)
        kube._mock_api.create.return_value = {"metadata": {"name": "pnc-import-xyz"}}

        assert kube.create_pipelinerun(manifest) == "pnc-import-xyz"
        kube._mock_api.get.assert_called_once_with(f"{COLLECTION_PATH}/pnc-import-xyz")
        assert kube._mock_api.create.call_count == (0 if existing else 1)

    def test_named_create_without_name_in_response_returns_manifest_name(self, kube: KubeClient):
        """The manifest fixes the name, so a response without metadata.name must not turn into a failure."""
        kube._mock_api.get.side_effect = _http_error(404)
        kube._mock_api.create.return_value = {}

        assert kube.create_pipelinerun(_named_manifest()) == IMPORT_NAME

    def test_raises_plain_trigger_error_on_mismatched_existing_identity(self, kube: KubeClient):
        """A name owned by another import is not an unclear create, so a new attempt may use a new name."""
        kube._mock_api.get.return_value = {
            "metadata": {"annotations": {"lightwell.redhat.com/import-identity": "another"}}
        }

        with pytest.raises(TriggerError, match="different import identity") as raised:
            kube.create_pipelinerun(_named_manifest())

        assert type(raised.value) is TriggerError
        kube._mock_api.create.assert_not_called()

    def test_raises_on_non_404_get_failure(self, kube: KubeClient):
        kube._mock_api.get.side_effect = _http_error(403, "forbidden")

        with pytest.raises(PipelineRunReconciliationError, match="ownership lookup failed") as raised:
            kube.create_pipelinerun(_named_manifest())

        assert raised.value.name == IMPORT_NAME
        assert "forbidden" in str(raised.value)
        kube._mock_api.create.assert_not_called()

    @pytest.mark.parametrize(
        ("failure_kind", "reconciles"),
        [
            ("connect", True),
            ("read", True),
            ("connection", True),
            ("409", True),
            ("429", False),
            ("500", False),
            ("502", False),
            ("503", False),
            ("504", False),
        ],
    )
    def test_reconciles_or_retries_retryable_create_failure(self, kube: KubeClient, no_wait, failure_kind, reconciles):
        """A retryable POST failure either reuses the object that now exists or retries the same name."""
        manifest = _named_manifest()
        failure = _failure(failure_kind)
        if reconciles:
            kube._mock_api.get.side_effect = [_http_error(404), {"metadata": manifest["metadata"]}]
            kube._mock_api.create.side_effect = failure
        else:
            kube._mock_api.get.side_effect = _http_error(404)
            kube._mock_api.create.side_effect = [failure, {"metadata": {"name": IMPORT_NAME}}]

        assert kube.create_pipelinerun(manifest) == IMPORT_NAME
        assert kube._mock_api.create.call_count == (1 if reconciles else 2)
        assert len(no_wait) == (0 if reconciles else 1)

    @pytest.mark.parametrize("failure_kind", ["connect", "read", "503"])
    def test_stops_after_three_posts_with_unclear_create(self, kube: KubeClient, no_wait, failure_kind, caplog):
        """Budget exhaustion after a retryable failure is unclear, because the last POST may still land."""
        failure = _failure(failure_kind)
        kube._mock_api.get.side_effect = _http_error(404)
        kube._mock_api.create.side_effect = failure

        with caplog.at_level("WARNING", logger="import_orchestrator.pipelinerun_creator"):
            with pytest.raises(PipelineRunReconciliationError, match="retry budget exhausted") as raised:
                kube.create_pipelinerun(_named_manifest())

        assert type(raised.value) is PipelineRunReconciliationError
        assert raised.value.name == IMPORT_NAME
        assert raised.value.__cause__ is failure
        assert kube._mock_api.create.call_count == 3
        assert caplog.records[-1].getMessage().endswith("attempts=3")

    def test_raises_reconciliation_error_when_ownership_lookup_fails(self, kube: KubeClient):
        """A failed lookup after an ambiguous POST leaves the create result unclear."""
        manifest = _named_manifest()
        reconciliation_result = _http_error(500)
        kube._mock_api.get.side_effect = [_http_error(404), reconciliation_result]
        failure = _http_error(503)
        kube._mock_api.create.side_effect = failure

        with pytest.raises(PipelineRunReconciliationError) as raised:
            kube.create_pipelinerun(manifest)

        assert raised.value.name == IMPORT_NAME
        assert raised.value.__cause__ is reconciliation_result

    def test_retry_resolving_to_other_identity_raises_plain_trigger_error(self, kube: KubeClient):
        """Another import owns the name after our ambiguous POST, so the error is not an unclear create."""
        manifest = _named_manifest()
        other_owner = {"metadata": {"annotations": {"lightwell.redhat.com/import-identity": "another"}}}
        kube._mock_api.get.side_effect = [_http_error(404), other_owner]
        failure = _http_error(503)
        kube._mock_api.create.side_effect = failure

        with pytest.raises(TriggerError, match="different import identity") as raised:
            kube.create_pipelinerun(manifest)

        assert type(raised.value) is TriggerError
        assert raised.value.__cause__ is failure

    @pytest.mark.parametrize(
        "manifest",
        [
            {"kind": "PipelineRun"},
            {"metadata": {"name": IMPORT_NAME}},
            {"metadata": {"annotations": {"lightwell.redhat.com/import-identity": IMPORT_IDENTITY}}},
        ],
        ids=["no-metadata", "name-without-identity", "identity-without-name"],
    )
    def test_raises_when_manifest_identity_is_missing(self, kube: KubeClient, manifest):
        with pytest.raises(TriggerError, match="metadata|identity"):
            kube.create_pipelinerun(manifest)

        kube._mock_api.create.assert_not_called()

    @pytest.mark.parametrize("status_code", [401, 400, 422])
    def test_does_not_retry_nonretryable_http_failure(self, kube: KubeClient, no_wait, status_code):
        kube._mock_api.get.side_effect = _http_error(404)
        kube._mock_api.create.side_effect = _http_error(status_code, "rejected by cluster")

        with pytest.raises(TriggerError, match="rejected by cluster"):
            kube.create_pipelinerun(_named_manifest())

        kube._mock_api.create.assert_called_once()
        assert no_wait == []

    def test_logs_retry_count_delay_and_deterministic_name(self, kube: KubeClient, no_wait, monkeypatch, caplog):
        kube._mock_api.get.side_effect = _http_error(404)
        kube._mock_api.create.side_effect = [
            _http_error(500),
            _http_error(500),
            {"metadata": {"name": IMPORT_NAME}},
        ]
        monkeypatch.setattr(pipelinerun_creator, "_jitter", lambda _low, _high: 0.25)

        with caplog.at_level("WARNING", logger="import_orchestrator.pipelinerun_creator"):
            kube.create_pipelinerun(_named_manifest())

        messages = [record.getMessage() for record in caplog.records]
        assert messages == [
            "Retrying PipelineRun creation name=pnc-import-abcde retry=1/2 delay=0.250s",
            "Retrying PipelineRun creation name=pnc-import-abcde retry=2/2 delay=0.250s",
        ]


LIVE_FOUND = {"metadata": {"name": IMPORT_NAME}}


def _answer(mock, outcome):
    """Make a GET mock raise ``outcome`` when it is an exception, or return it otherwise."""
    if isinstance(outcome, Exception):
        mock.side_effect = outcome
    else:
        mock.return_value = outcome


class TestPipelinerunAbsent:
    """Absence is confirmed only when every configured source answers 404."""

    @pytest.mark.parametrize(
        ("live", "expected"),
        [
            (_http_error(404), True),
            (_http_error(500), False),
            (_http_error(403, "forbidden"), False),
            (requests.ConnectionError("refused"), False),
            (LIVE_FOUND, False),
        ],
        ids=["live-404", "live-500", "live-403", "connection-error", "found"],
    )
    def test_without_archive_relies_on_live_lookup(self, kube: KubeClient, live, expected):
        _answer(kube._mock_api.get, live)

        assert kube.pipelinerun_absent(IMPORT_NAME) is expected

    @pytest.mark.parametrize(
        ("live", "archive", "expected"),
        [
            (_http_error(404), _http_error(404), True),
            (_http_error(404), _http_error(500), False),
            (_http_error(404), requests.ConnectionError("refused"), False),
            (_http_error(404), LIVE_FOUND, False),
            (_http_error(500), _http_error(404), False),
        ],
        ids=["archive-404", "archive-500", "archive-connection-error", "archive-found", "live-500"],
    )
    def test_with_archive_requires_both_sources_to_return_404(self, kube_with_ka: KubeClient, live, archive, expected):
        _answer(kube_with_ka._mock_api.get, live)
        _answer(kube_with_ka._mock_ka_api.get, archive)

        assert kube_with_ka.pipelinerun_absent(IMPORT_NAME) is expected


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
