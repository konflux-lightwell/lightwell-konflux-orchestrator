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

from import_orchestrator.ecosystems.java.ecosystem import JavaEcosystem
from import_orchestrator.ecosystems.java.pipelinerun import (
    TriggerError,
    digest_pin_image,
    extract_tag_from_image,
)


def _args(artifact_type="STAGE", tag=None):
    return argparse.Namespace(artifact_type=artifact_type, tag=tag)


def test_digest_pin_image_rejects_untagged():
    with pytest.raises(TriggerError):
        digest_pin_image("quay.io/repo:tag")


def test_extract_tag_from_image_prefers_tag():
    assert extract_tag_from_image("quay.io/repo:lw-ABC@sha256:deadbeef") == "lw-ABC"


def test_extract_tag_from_image_falls_back_to_digest():
    assert extract_tag_from_image("quay.io/repo@sha256:deadbeef") == "deadbeef"


def test_build_pipelinerun_sets_source_and_dest(monkeypatch, tmp_path):
    pipeline_file = tmp_path / "tekton" / "pipelines" / "pnc-import" / "pnc-import.yaml"
    pipeline_file.parent.mkdir(parents=True)
    pipeline_file.write_text("spec:\n  tasks: []\n")
    monkeypatch.setenv("TEKTON_PIPELINE_DIR", str(tmp_path / "tekton"))

    eco = JavaEcosystem()
    manifest = eco.build_pipelinerun("quay.io/repo:lw-ABC@sha256:deadbeef", _args("STAGE"))

    assert manifest["kind"] == "PipelineRun"
    assert manifest["metadata"]["generateName"] == "pnc-import-"
    params = {p["name"]: p["value"] for p in manifest["spec"]["params"]}
    assert params["SOURCE_IMAGE"] == "quay.io/repo:lw-ABC@sha256:deadbeef"
    assert params["IMAGE"].endswith(":lw-ABC")


def test_build_pipelinerun_missing_digest_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("TEKTON_PIPELINE_DIR", str(tmp_path))
    with pytest.raises(TriggerError):
        JavaEcosystem().build_pipelinerun("quay.io/repo:tag", _args("STAGE"))


def test_java_ecosystem_satisfies_protocol():
    from import_orchestrator.ecosystems.base import Ecosystem

    assert isinstance(JavaEcosystem(), Ecosystem)


def test_java_namespace():
    assert JavaEcosystem().namespace == "lightwell-poc-tenant"


def test_build_pipelinerun_uses_ecosystem_namespace(monkeypatch, tmp_path):
    pipeline_file = tmp_path / "tekton" / "pipelines" / "pnc-import" / "pnc-import.yaml"
    pipeline_file.parent.mkdir(parents=True)
    pipeline_file.write_text("spec:\n  tasks: []\n")
    monkeypatch.setenv("TEKTON_PIPELINE_DIR", str(tmp_path / "tekton"))

    manifest = JavaEcosystem().build_pipelinerun("quay.io/repo:lw-ABC@sha256:deadbeef", _args("STAGE"))

    assert manifest["metadata"]["namespace"] == "lightwell-poc-tenant"
