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

from __future__ import annotations

import pytest

from import_orchestrator.ecosystems.python.pipelinerun import (
    TriggerError,
    build_pipelinerun_manifest,
    parse_ref,
)


class TestParseRef:
    def test_splits_package_and_version(self):
        assert parse_ref("ntplib==0.4.0") == ("ntplib", "0.4.0")

    def test_rejects_missing_separator(self):
        with pytest.raises(TriggerError):
            parse_ref("ntplib-0.4.0")

    def test_rejects_empty_package(self):
        with pytest.raises(TriggerError):
            parse_ref("==0.4.0")

    def test_rejects_empty_version(self):
        with pytest.raises(TriggerError):
            parse_ref("ntplib==")


def _manifest(**overrides):
    kwargs = dict(
        package="ntplib",
        version="0.4.0",
        pipeline_spec={"tasks": []},
        namespace="lightwell-python-tenant",
        application="remediated-build",
        component="remediated-build",
        prefix="python-remediated-build-",
        repo_base="https://gitlab.cee.redhat.com/lightwell/lightwell-builds",
        image_repo_base="quay.io/redhat-user-workloads/lightwell-python-tenant",
    )
    kwargs.update(overrides)
    return build_pipelinerun_manifest(**kwargs)


class TestBuildManifest:
    def test_kind_and_generate_name(self):
        manifest = _manifest()
        assert manifest["kind"] == "PipelineRun"
        assert manifest["metadata"]["generateName"] == "python-remediated-build-"

    def test_namespace(self):
        assert _manifest()["metadata"]["namespace"] == "lightwell-python-tenant"

    def test_service_account_omitted_by_default(self):
        # No service account -> no taskRunTemplate, so the cluster default applies.
        assert "taskRunTemplate" not in _manifest()["spec"]

    def test_service_account_included_when_provided(self):
        manifest = _manifest(service_account="build-pipeline-python-remediated-build")
        assert manifest["spec"]["taskRunTemplate"]["serviceAccountName"] == "build-pipeline-python-remediated-build"

    def test_application_and_component_labels(self):
        labels = _manifest()["metadata"]["labels"]
        assert labels["appstudio.openshift.io/application"] == "remediated-build"
        assert labels["appstudio.openshift.io/component"] == "remediated-build"
        assert labels["pipelines.appstudio.openshift.io/type"] == "build"

    def test_params_are_derived(self):
        params = {p["name"]: p["value"] for p in _manifest()["spec"]["params"]}
        assert params["PACKAGE"] == "ntplib"
        assert params["VERSION"] == "0.4.0"
        assert params["LIGHTWELL_BUILDS_TAG"] == "ntplib/0.4.0"
        assert params["LIGHTWELL_BUILDS_REPO_URL"] == (
            "https://gitlab.cee.redhat.com/lightwell/lightwell-builds/pypi.org-ntplib"
        )
        assert params["IMAGE"] == "quay.io/redhat-user-workloads/lightwell-python-tenant/ntplib:0.4.0"
        assert params["ociStorage"] == "quay.io/redhat-user-workloads/lightwell-python-tenant/ntplib:0.4.0.src"

    def test_pipeline_spec_embedded(self):
        assert _manifest()["spec"]["pipelineSpec"] == {"tasks": []}
