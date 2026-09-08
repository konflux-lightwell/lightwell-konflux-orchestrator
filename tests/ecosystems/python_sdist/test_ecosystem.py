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
from import_orchestrator.ecosystems.python_sdist.ecosystem import PythonSdistEcosystem
from import_orchestrator.engine.errors import TriggerError


def test_python_sdist_ecosystem_satisfies_protocol():
    assert isinstance(PythonSdistEcosystem(), Ecosystem)


def test_python_sdist_attributes():
    eco = PythonSdistEcosystem()
    assert eco.name == "python-sdist"
    assert eco.namespace == "lightwell-python-tenant"
    assert eco.pipelinerun_prefix == "python-sdist-ingest-"


def test_build_pipelinerun_from_ref(monkeypatch, tmp_path):
    pipeline_file = tmp_path / "tekton" / "pipelines" / "python-sdist-ingest" / "python-sdist-ingest.yaml"
    pipeline_file.parent.mkdir(parents=True)
    pipeline_file.write_text("spec:\n  tasks: []\n")
    monkeypatch.setenv("TEKTON_PIPELINE_DIR", str(tmp_path / "tekton"))

    manifest = PythonSdistEcosystem().build_pipelinerun("foolib==0.4.0", argparse.Namespace())

    assert manifest["metadata"]["namespace"] == "lightwell-python-tenant"
    assert manifest["metadata"]["generateName"] == "python-sdist-ingest-"
    labels = manifest["metadata"]["labels"]
    assert labels["appstudio.openshift.io/application"] == "python-sdist-mirror"
    assert labels["appstudio.openshift.io/component"] == "python-sdist-mirror"
    params = {p["name"]: p["value"] for p in manifest["spec"]["params"]}
    assert params["PACKAGE"] == "foolib"
    assert params["VERSION"] == "0.4.0"
    assert params["SOURCE_REGISTRIES"] == "rhtl,pypi.org"


def test_build_pipelinerun_rejects_malformed_ref(tmp_path, monkeypatch):
    monkeypatch.setenv("TEKTON_PIPELINE_DIR", str(tmp_path))
    with pytest.raises(TriggerError):
        PythonSdistEcosystem().build_pipelinerun("foolib-0.4.0", argparse.Namespace())
