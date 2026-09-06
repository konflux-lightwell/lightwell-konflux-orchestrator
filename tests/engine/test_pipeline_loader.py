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

from pathlib import Path

import pytest

from import_orchestrator.engine.errors import TriggerError
from import_orchestrator.engine.pipeline_loader import (
    apply_pipeline_overrides,
    load_pipeline,
    load_pipeline_overrides,
)


def test_load_pipeline_success(tmp_path: Path):
    pipeline_file = tmp_path / "pipeline.yaml"
    pipeline_file.write_text("spec:\n  tasks:\n    - name: test\n")
    spec = load_pipeline(pipeline_file)
    assert spec == {"tasks": [{"name": "test"}]}


def test_load_pipeline_missing_file(tmp_path: Path):
    with pytest.raises(TriggerError, match="pipeline definition not found"):
        load_pipeline(tmp_path / "nonexistent.yaml")


def test_load_pipeline_invalid_yaml(tmp_path: Path):
    pipeline_file = tmp_path / "pipeline.yaml"
    pipeline_file.write_text(":\n invalid")
    with pytest.raises(TriggerError, match="failed to load pipeline"):
        load_pipeline(pipeline_file)


def test_load_pipeline_missing_spec(tmp_path: Path):
    pipeline_file = tmp_path / "pipeline.yaml"
    pipeline_file.write_text("kind: Pipeline\n")
    with pytest.raises(TriggerError, match="failed to load pipeline"):
        load_pipeline(pipeline_file)


def test_load_pipeline_overrides_when_file_absent(tmp_path: Path):
    pipeline_file = tmp_path / "pipeline.yaml"
    pipeline_file.write_text("spec: {}\n")
    assert load_pipeline_overrides(pipeline_file) == {}


def test_load_pipeline_overrides_when_file_present(tmp_path: Path):
    pipeline_file = tmp_path / "pipeline.yaml"
    overrides_file = tmp_path / "overrides.yaml"
    overrides_file.write_text("taskRunSpecs:\n  - pipelineTaskName: sast-shell-check\n")
    overrides = load_pipeline_overrides(pipeline_file)
    assert overrides == {"taskRunSpecs": [{"pipelineTaskName": "sast-shell-check"}]}


def test_load_pipeline_overrides_invalid_yaml(tmp_path: Path):
    pipeline_file = tmp_path / "pipeline.yaml"
    overrides_file = tmp_path / "overrides.yaml"
    overrides_file.write_text(":\n invalid")
    with pytest.raises(TriggerError, match="failed to load overrides"):
        load_pipeline_overrides(pipeline_file)


def test_apply_pipeline_overrides_merges_into_manifest(tmp_path: Path):
    pipeline_file = tmp_path / "pipeline.yaml"
    overrides_file = tmp_path / "overrides.yaml"
    overrides_file.write_text(
        "taskRunSpecs:\n"
        "  - pipelineTaskName: sast-shell-check\n"
        "    stepSpecs:\n"
        "      - name: sast-shell-check\n"
        "        computeResources:\n"
        "          limits:\n"
        "            memory: 5Gi\n"
        "taskRunTemplate:\n"
        "  podTemplate:\n"
        "    nodeSelector:\n"
        "      workload: test\n"
    )

    manifest = {
        "apiVersion": "tekton.dev/v1",
        "kind": "PipelineRun",
        "spec": {
            "taskRunTemplate": {"serviceAccountName": "custom-sa"},
            "params": [],
        },
    }

    result = apply_pipeline_overrides(manifest, pipeline_file)

    # taskRunSpecs attached
    assert result["spec"]["taskRunSpecs"] == [
        {
            "pipelineTaskName": "sast-shell-check",
            "stepSpecs": [
                {
                    "name": "sast-shell-check",
                    "computeResources": {"limits": {"memory": "5Gi"}},
                }
            ],
        }
    ]
    # taskRunTemplate shallow-merged: serviceAccountName preserved and podTemplate added
    assert result["spec"]["taskRunTemplate"]["serviceAccountName"] == "custom-sa"
    assert result["spec"]["taskRunTemplate"]["podTemplate"] == {"nodeSelector": {"workload": "test"}}


def test_apply_pipeline_overrides_noops_when_no_overrides(tmp_path: Path):
    pipeline_file = tmp_path / "pipeline.yaml"
    manifest = {
        "apiVersion": "tekton.dev/v1",
        "kind": "PipelineRun",
        "spec": {"taskRunTemplate": {"serviceAccountName": "custom-sa"}},
    }
    result = apply_pipeline_overrides(manifest, pipeline_file)
    assert result == manifest
