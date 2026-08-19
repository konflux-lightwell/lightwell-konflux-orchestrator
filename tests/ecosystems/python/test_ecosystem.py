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
from import_orchestrator.ecosystems.python.ecosystem import PythonEcosystem
from import_orchestrator.engine.errors import TriggerError


def test_python_ecosystem_satisfies_protocol():
    assert isinstance(PythonEcosystem(), Ecosystem)


def test_python_attributes():
    eco = PythonEcosystem()
    assert eco.name == "python"
    assert eco.namespace == "lightwell-python-tenant"
    assert eco.pipelinerun_prefix == "python-remediated-build-"


def test_build_pipelinerun_from_ref(monkeypatch, tmp_path):
    pipeline_file = tmp_path / "tekton" / "pipelines" / "python-remediated-build" / "python-remediated-build.yaml"
    pipeline_file.parent.mkdir(parents=True)
    pipeline_file.write_text("spec:\n  tasks: []\n")
    monkeypatch.setenv("TEKTON_PIPELINE_DIR", str(tmp_path / "tekton"))

    manifest = PythonEcosystem().build_pipelinerun("ntplib==0.4.0", argparse.Namespace())

    assert manifest["metadata"]["namespace"] == "lightwell-python-tenant"
    params = {p["name"]: p["value"] for p in manifest["spec"]["params"]}
    assert params["PACKAGE"] == "ntplib"
    assert params["VERSION"] == "0.4.0"


def test_build_pipelinerun_rejects_malformed_ref(tmp_path, monkeypatch):
    monkeypatch.setenv("TEKTON_PIPELINE_DIR", str(tmp_path))
    with pytest.raises(TriggerError):
        PythonEcosystem().build_pipelinerun("ntplib-0.4.0", argparse.Namespace())
