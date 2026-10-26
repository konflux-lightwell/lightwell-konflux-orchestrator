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
import json
from pathlib import Path
from typing import NamedTuple

import pytest

from import_orchestrator.ecosystems.java.ecosystem import JavaEcosystem
from import_orchestrator.ecosystems.javascript.ecosystem import JavaScriptEcosystem
from import_orchestrator.ecosystems.python.ecosystem import PythonEcosystem
from import_orchestrator.ecosystems.python_sdist.ecosystem import PythonSdistEcosystem
from import_orchestrator.pipelinerun import IMPORT_IDENTITY_ANNOTATION

IGNORE_SUPERSESSION = "test.appstudio.openshift.io/ignore-supersession"
DEFAULT_PIPELINE_CONTENT = "spec:\n  tasks: []\n"


class Case(NamedTuple):
    """One ecosystem with a valid ref, args, and the pipeline file it loads."""

    ecosystem: type
    pipeline_relpath: str
    ref: str
    args: argparse.Namespace


@pytest.fixture(
    params=[
        pytest.param(
            Case(
                JavaEcosystem,
                "pipelines/pnc-import/pnc-import.yaml",
                "quay.io/repo:lw-ABC@sha256:deadbeef",
                argparse.Namespace(artifact_type="STAGE", tag=None),
            ),
            id="java",
        ),
        pytest.param(
            Case(PythonEcosystem, "pipelines/python-build/python-build.yaml", "foolib==0.4.0", argparse.Namespace()),
            id="python",
        ),
        pytest.param(
            Case(
                PythonSdistEcosystem,
                "pipelines/python-sdist-ingest/python-sdist-ingest.yaml",
                "foolib==0.4.0",
                argparse.Namespace(),
            ),
            id="python-sdist",
        ),
        pytest.param(
            Case(
                JavaScriptEcosystem,
                "pipelines/javascript-validated-build/javascript-validated-build.yaml",
                "bluebird==3.7.2",
                argparse.Namespace(),
            ),
            id="javascript",
        ),
    ]
)
def case(request) -> Case:
    """Yield each ecosystem case in turn."""
    return request.param


@pytest.fixture
def tekton_dir(tmp_path: Path, monkeypatch) -> Path:
    """Point the pipeline and build-config settings at a temp tree, not the developer environment."""
    root = tmp_path / "tekton"
    monkeypatch.setenv("TEKTON_PIPELINE_DIR", str(root))
    monkeypatch.setenv("BUILD_CONFIGS_REPO_URL", "https://example.test/build-configs.git")
    monkeypatch.delenv("BUILD_CONFIGS_REVISION", raising=False)
    return root


@pytest.fixture
def pipeline_file(case: Case, tekton_dir: Path) -> Path:
    """Write the pipeline definition the case loads and return its path."""
    path = tekton_dir / case.pipeline_relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_PIPELINE_CONTENT)
    return path


def _build(case: Case, attempt: int | None = None) -> dict:
    """Build a manifest through the ecosystem's public entry point."""
    return case.ecosystem().build_pipelinerun(case.ref, case.args, attempt=attempt)


def test_attempt_none_keeps_generate_name(case: Case, pipeline_file: Path):
    """Without a DB Attempt the manifest keeps generateName and has no identity annotation."""
    manifest = _build(case)

    metadata = manifest["metadata"]
    assert metadata["generateName"] == case.ecosystem.pipelinerun_prefix
    assert "name" not in metadata
    assert IMPORT_IDENTITY_ANNOTATION not in metadata["annotations"]


def test_attempt_gives_deterministic_name(case: Case, pipeline_file: Path):
    """The same attempt and inputs always produce the same PipelineRun name."""
    first = _build(case, attempt=3)
    again = _build(case, attempt=3)

    name = first["metadata"]["name"]
    assert name == again["metadata"]["name"]
    assert name.startswith(case.ecosystem.pipelinerun_prefix.rstrip("-"))
    assert "generateName" not in first["metadata"]


def test_attempt_adds_identity_annotation(case: Case, pipeline_file: Path):
    """An attempt adds an identity annotation that names the ecosystem and attempt, and keeps defaults."""
    manifest = _build(case, attempt=3)

    annotations = manifest["metadata"]["annotations"]
    identity = json.loads(annotations[IMPORT_IDENTITY_ANNOTATION])
    assert identity["ecosystem"] == case.ecosystem.name
    assert identity["attempt"] == 3
    assert annotations[IGNORE_SUPERSESSION] == "true"


def test_different_attempt_gives_different_name(case: Case, pipeline_file: Path):
    """A retry, which is a new attempt, gets its own PipelineRun name and identity."""
    first = _build(case, attempt=1)
    retry = _build(case, attempt=2)

    assert first["metadata"]["name"] != retry["metadata"]["name"]
    assert (
        first["metadata"]["annotations"][IMPORT_IDENTITY_ANNOTATION]
        != retry["metadata"]["annotations"][IMPORT_IDENTITY_ANNOTATION]
    )


def test_different_execution_spec_gives_different_name(case: Case, pipeline_file: Path):
    """Changing the pipeline definition changes the execution spec fingerprint and so the name."""
    first = _build(case, attempt=1)

    pipeline_file.write_text("spec:\n  tasks:\n    - name: changed\n")
    changed = _build(case, attempt=1)

    assert first["metadata"]["name"] != changed["metadata"]["name"]
    assert (
        first["metadata"]["annotations"][IMPORT_IDENTITY_ANNOTATION]
        != changed["metadata"]["annotations"][IMPORT_IDENTITY_ANNOTATION]
    )


def test_attempt_does_not_leak_into_default_annotations(case: Case, pipeline_file: Path):
    """Building with an attempt must not change the shared default annotations for later builds."""
    _build(case, attempt=1)

    manifest = _build(case)

    assert manifest["metadata"]["annotations"] == {IGNORE_SUPERSESSION: "true"}
