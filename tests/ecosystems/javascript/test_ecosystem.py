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

import argparse

import pytest

from import_orchestrator.ecosystems.base import Ecosystem
from import_orchestrator.ecosystems.javascript import config
from import_orchestrator.ecosystems.javascript.ecosystem import JavaScriptEcosystem
from import_orchestrator.engine.errors import TriggerError


def test_javascript_ecosystem_satisfies_protocol():
    """JavaScriptEcosystem implements the Ecosystem protocol."""
    assert isinstance(JavaScriptEcosystem(), Ecosystem)


def test_javascript_attributes():
    """JavaScriptEcosystem exposes expected name/namespace/prefix."""
    eco = JavaScriptEcosystem()
    assert eco.name == "javascript"
    assert eco.namespace == "lightwell-javascript-tenant"
    assert eco.pipelinerun_prefix == "javascript-validated-build-"


def test_build_pipelinerun_from_ref(monkeypatch, tmp_path):
    """build_pipelinerun produces a VALIDATED manifest for package==version."""
    pipeline_file = tmp_path / "tekton" / "pipelines" / "javascript-validated-build" / "javascript-validated-build.yaml"
    pipeline_file.parent.mkdir(parents=True)
    pipeline_file.write_text("spec:\n  tasks: []\n")
    monkeypatch.setenv("TEKTON_PIPELINE_DIR", str(tmp_path / "tekton"))

    manifest = JavaScriptEcosystem().build_pipelinerun("bluebird==3.7.2", argparse.Namespace())

    assert manifest["metadata"]["namespace"] == "lightwell-javascript-tenant"
    assert manifest["metadata"]["generateName"] == "javascript-validated-build-"
    labels = manifest["metadata"]["labels"]
    assert labels["appstudio.openshift.io/application"] == "javascript-validated"
    assert labels["appstudio.openshift.io/component"] == "javascript-validated-build"
    assert manifest["spec"]["taskRunTemplate"]["serviceAccountName"] == ("build-pipeline-javascript-validated-build")
    params = {p["name"]: p["value"] for p in manifest["spec"]["params"]}
    assert params["packages"] == ["packages/bluebird/3.7.2"]
    assert params["output-image"].endswith(":bluebird-3.7.2")
    assert params["tl-npm-registry-url"] == config.TL_NPM_REGISTRY_URL_PROD
    assert params["lightwell-build-intent"] == "release"
    assert labels["lightwell.redhat.com/package"] == "bluebird"


def test_build_pipelinerun_validated_stage_target(monkeypatch, tmp_path):
    """VALIDATED_STAGE uses the stage app/component/SA and stage Pulp registry."""
    pipeline_file = tmp_path / "tekton" / "pipelines" / "javascript-validated-build" / "javascript-validated-build.yaml"
    pipeline_file.parent.mkdir(parents=True)
    pipeline_file.write_text("spec:\n  tasks: []\n")
    monkeypatch.setenv("TEKTON_PIPELINE_DIR", str(tmp_path / "tekton"))

    manifest = JavaScriptEcosystem().build_pipelinerun(
        "bluebird==3.7.2",
        argparse.Namespace(target="VALIDATED_STAGE"),
    )

    labels = manifest["metadata"]["labels"]
    assert labels["appstudio.openshift.io/application"] == "javascript-validated-stage"
    assert labels["appstudio.openshift.io/component"] == "javascript-validated-stage-build"
    assert manifest["spec"]["taskRunTemplate"]["serviceAccountName"] == (
        "build-pipeline-javascript-validated-stage-build"
    )
    params = {p["name"]: p["value"] for p in manifest["spec"]["params"]}
    assert params["tl-npm-registry-url"] == config.TL_NPM_REGISTRY_URL_STAGE
    assert params["output-image"].endswith(
        "/javascript-validated-stage/javascript-validated-stage-build:bluebird-3.7.2"
    )


def test_build_pipelinerun_tl_registry_cli_override(monkeypatch, tmp_path):
    """Explicit --tl-npm-registry-url wins over the target default."""
    pipeline_file = tmp_path / "tekton" / "pipelines" / "javascript-validated-build" / "javascript-validated-build.yaml"
    pipeline_file.parent.mkdir(parents=True)
    pipeline_file.write_text("spec:\n  tasks: []\n")
    monkeypatch.setenv("TEKTON_PIPELINE_DIR", str(tmp_path / "tekton"))

    manifest = JavaScriptEcosystem().build_pipelinerun(
        "bluebird==3.7.2",
        argparse.Namespace(
            target="VALIDATED_STAGE",
            tl_npm_registry_url="https://example.test/javascript",
        ),
    )
    params = {p["name"]: p["value"] for p in manifest["spec"]["params"]}
    assert params["tl-npm-registry-url"] == "https://example.test/javascript"


def test_build_pipelinerun_publish_version_suffix(monkeypatch, tmp_path):
    """--publish-version-suffix reaches the pipeline as a per-ref version override."""
    pipeline_file = tmp_path / "tekton" / "pipelines" / "javascript-validated-build" / "javascript-validated-build.yaml"
    pipeline_file.parent.mkdir(parents=True)
    pipeline_file.write_text("spec:\n  tasks: []\n")
    monkeypatch.setenv("TEKTON_PIPELINE_DIR", str(tmp_path / "tekton"))

    manifest = JavaScriptEcosystem().build_pipelinerun(
        "bluebird==3.7.2",
        argparse.Namespace(target="SCRATCH", publish_version_suffix="lwci.0"),
    )
    params = {p["name"]: p["value"] for p in manifest["spec"]["params"]}
    assert params["lightwell-publish-version-override"] == "3.7.2-lwci.0"
    assert params["lightwell-build-intent"] == "scratch"


def test_build_pipelinerun_scoped_package(monkeypatch, tmp_path):
    """Scoped refs keep packages/@scope/name path; tag/label use a slug."""
    pipeline_file = tmp_path / "tekton" / "pipelines" / "javascript-validated-build" / "javascript-validated-build.yaml"
    pipeline_file.parent.mkdir(parents=True)
    pipeline_file.write_text("spec:\n  tasks: []\n")
    monkeypatch.setenv("TEKTON_PIPELINE_DIR", str(tmp_path / "tekton"))

    manifest = JavaScriptEcosystem().build_pipelinerun("@babel/core==7.3.2", argparse.Namespace())

    labels = manifest["metadata"]["labels"]
    assert labels["lightwell.redhat.com/package"] == "babel-core"
    params = {p["name"]: p["value"] for p in manifest["spec"]["params"]}
    assert params["packages"] == ["packages/@babel/core/7.3.2"]
    assert params["output-image"].endswith(":babel-core-7.3.2")


def test_build_pipelinerun_rejects_malformed_ref(tmp_path, monkeypatch):
    """build_pipelinerun rejects refs that are not package==version."""
    monkeypatch.setenv("TEKTON_PIPELINE_DIR", str(tmp_path))
    with pytest.raises(TriggerError):
        JavaScriptEcosystem().build_pipelinerun("bluebird-3.7.2", argparse.Namespace())


def test_validated_does_not_skip_release():
    """VALIDATED waits on Release; no SCRATCH target until dedicated wiring exists."""
    assert JavaScriptEcosystem().target_skip_release("VALIDATED") is False
    assert JavaScriptEcosystem().target_skip_release("VALIDATED_STAGE") is False
    assert JavaScriptEcosystem().target_skip_release("REMEDIATED_STAGE") is False


def test_build_pipelinerun_remediated_stage_target(monkeypatch, tmp_path):
    """REMEDIATED_STAGE uses the thin remediations pipeline and omits assess params."""
    pipeline_file = (
        tmp_path / "tekton" / "pipelines" / "javascript-remediated-build" / "javascript-remediated-build.yaml"
    )
    pipeline_file.parent.mkdir(parents=True)
    pipeline_file.write_text("spec:\n  tasks: []\n")
    monkeypatch.setenv("TEKTON_PIPELINE_DIR", str(tmp_path / "tekton"))

    manifest = JavaScriptEcosystem().build_pipelinerun(
        "bluebird==3.7.2",
        argparse.Namespace(target="REMEDIATED_STAGE"),
    )

    labels = manifest["metadata"]["labels"]
    assert labels["appstudio.openshift.io/application"] == "javascript-remediated-stage"
    assert labels["appstudio.openshift.io/component"] == "javascript-remediated-stage-build"
    assert labels["pipelines.openshift.io/runtime"] == "build-npm"
    assert manifest["metadata"]["generateName"] == "javascript-remediated-build-"
    assert manifest["spec"]["taskRunTemplate"]["serviceAccountName"] == (
        "build-pipeline-javascript-remediated-stage-build"
    )
    params = {p["name"]: p["value"] for p in manifest["spec"]["params"]}
    assert "task-assess-npm-compliance-bundle" not in params
    assert "tl-npm-registry-url" not in params
    assert "pulp-read-secret-name" not in params
    assert params["lightwell-build-intent"] == "release"
    assert params["output-image"].endswith(
        "/javascript-remediated-stage/javascript-remediated-stage-build:bluebird-3.7.2"
    )


def test_pipelinerun_prefix_for_remediated_stage():
    """REMEDIATED_STAGE uses the remediations generateName prefix, not validated."""
    eco = JavaScriptEcosystem()
    assert eco.pipelinerun_prefix == "javascript-validated-build-"
    assert eco.pipelinerun_prefix_for(argparse.Namespace(target="REMEDIATED_STAGE")) == "javascript-remediated-build-"
    assert eco.pipelinerun_prefix_for(argparse.Namespace(target="VALIDATED")) == "javascript-validated-build-"


def test_request_identity_remediated_stage_target():
    """request_identity fingerprints REMEDIATED_STAGE app/component when selected."""
    identity = JavaScriptEcosystem().request_identity(
        "bluebird==3.7.2",
        argparse.Namespace(target="REMEDIATED_STAGE"),
    )
    assert identity["application"] == "javascript-remediated-stage"
    assert identity["component"] == "javascript-remediated-stage-build"
    assert identity["pipeline"] == "javascript-remediated-build"
    assert identity["target"] == "REMEDIATED_STAGE"


def test_request_identity_defaults_to_validated_target():
    """request_identity fingerprints VALIDATED when no target is set."""
    identity = JavaScriptEcosystem().request_identity("bluebird==3.7.2", argparse.Namespace())
    assert identity == {
        "namespace": "lightwell-javascript-tenant",
        "application": "javascript-validated",
        "component": "javascript-validated-build",
        "pipeline": "javascript-validated-build",
        "release_plan": "",
        "target": "VALIDATED",
        "artifact": "bluebird==3.7.2",
        "source_repo": config.DEFAULT_SOURCE_GIT_URL,
    }


def test_request_identity_validated_stage_target():
    """request_identity fingerprints VALIDATED_STAGE app/component when selected."""
    identity = JavaScriptEcosystem().request_identity(
        "bluebird==3.7.2",
        argparse.Namespace(target="VALIDATED_STAGE"),
    )
    assert identity["application"] == "javascript-validated-stage"
    assert identity["component"] == "javascript-validated-stage-build"
    assert identity["target"] == "VALIDATED_STAGE"
