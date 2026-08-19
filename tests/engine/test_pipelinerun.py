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
from unittest.mock import MagicMock, patch

import pytest
import yaml

from import_orchestrator.clients.kube import KubeClient
from import_orchestrator.constants import ARTIFACT_CONFIGS
from import_orchestrator.engine.pipelinerun import (
    PipelineRunBuilder,
    TriggerError,
    build_pipelinerun_manifest,
    digest_pin_image,
    extract_tag_from_image,
    get_pipeline_definition_path,
    load_pipeline,
)

# ---------------------------------------------------------------------------
# Sample pipeline YAML used by multiple test classes
# ---------------------------------------------------------------------------


def _bundle_resolver_ref(name, bundle):
    return {
        "resolver": "bundles",
        "params": [
            {"name": "name", "value": name},
            {"name": "bundle", "value": bundle},
            {"name": "kind", "value": "task"},
        ],
    }


SAMPLE_PIPELINE_YAML = {
    "apiVersion": "tekton.dev/v1",
    "kind": "Pipeline",
    "metadata": {"name": "pnc-import"},
    "spec": {
        "params": [
            {"name": "SOURCE_IMAGE", "type": "string"},
            {"name": "IMAGE", "type": "string"},
        ],
        "tasks": [
            {
                "name": "verify-and-mirror",
                "taskRef": _bundle_resolver_ref(
                    "oci-verify-import",
                    "quay.io/konflux-ci/tekton-catalog/task-oci-verify-import:0.1@sha256:PLACEHOLDER",
                ),
                "params": [
                    {"name": "SOURCE_IMAGE", "value": "$(params.SOURCE_IMAGE)"},
                    {"name": "IMAGE", "value": "$(params.IMAGE)"},
                ],
            },
            {
                "name": "clamav-scan",
                "taskRef": _bundle_resolver_ref(
                    "clamav-scan",
                    "quay.io/konflux-ci/tekton-catalog/task-clamav-scan:0.3@sha256:aaa",
                ),
                "runAfter": ["verify-and-mirror"],
                "params": [],
            },
            {
                "name": "sast-shell-check",
                "taskRef": _bundle_resolver_ref(
                    "sast-shell-check-oci-ta",
                    "quay.io/konflux-ci/tekton-catalog/task-sast-shell-check-oci-ta:0.1@sha256:bbb",
                ),
                "runAfter": ["verify-and-mirror"],
                "params": [],
            },
            {
                "name": "sast-unicode-check",
                "taskRef": _bundle_resolver_ref(
                    "sast-unicode-check-oci-ta",
                    "quay.io/konflux-ci/tekton-catalog/task-sast-unicode-check-oci-ta:0.4@sha256:ccc",
                ),
                "runAfter": ["verify-and-mirror"],
                "params": [],
            },
        ],
    },
}


# ---------------------------------------------------------------------------
# digest_pin_image
# ---------------------------------------------------------------------------


class TestDigestPinImage:
    """Test the digest_pin_image function."""

    def test_already_pinned_returns_unchanged(self):
        image = "quay.io/repo:tag@sha256:abc123"
        assert digest_pin_image(image) == image

    def test_raises_trigger_error_for_unpinned_tagged_image(self):
        with pytest.raises(TriggerError, match="must be digest-pinned"):
            digest_pin_image("quay.io/repo:tag")

    def test_raises_trigger_error_for_bare_reference(self):
        with pytest.raises(TriggerError, match="must be digest-pinned"):
            digest_pin_image("quay.io/repo")


# ---------------------------------------------------------------------------
# extract_tag_from_image
# ---------------------------------------------------------------------------


class TestExtractTagFromImage:
    """Test the extract_tag_from_image function."""

    def test_standard_tag_with_digest(self):
        image = "quay.io/repo:v1.2.3@sha256:abc123"
        assert extract_tag_from_image(image) == "v1.2.3"

    def test_numeric_tag(self):
        image = "quay.io/repo:12345@sha256:def456"
        assert extract_tag_from_image(image) == "12345"

    def test_tag_with_dots_and_hyphens(self):
        image = "quay.io/repo:build-2024.01.15-rc1@sha256:aaa"
        assert extract_tag_from_image(image) == "build-2024.01.15-rc1"

    def test_registry_with_port_captures_from_first_colon(self):
        """The regex matches from the first colon, so port-based registries
        include the port and path in the extracted 'tag'.  This is acceptable
        because PNC image references always use quay.io (no port)."""
        image = "registry.example.com:5000/repo:mytag@sha256:fff"
        # Actual behavior: the greedy match captures everything between first : and @
        assert extract_tag_from_image(image) == "5000/repo:mytag"

    def test_digest_only_returns_hex(self):
        image = "quay.io/repo@sha256:52400dbd9569ac3d25281b9f10edb680b947291ecedf6321681bcaec9749364f"
        assert extract_tag_from_image(image) == "52400dbd9569ac3d25281b9f10edb680b947291ecedf6321681bcaec9749364f"

    def test_raises_trigger_error_for_no_digest(self):
        image = "quay.io/repo:tag"
        with pytest.raises(TriggerError, match="could not extract tag"):
            extract_tag_from_image(image)

    def test_raises_trigger_error_for_bare_reference(self):
        image = "quay.io/repo"
        with pytest.raises(TriggerError, match="could not extract tag"):
            extract_tag_from_image(image)

    def test_complex_real_world_reference(self):
        image = (
            "quay.io/redhat-user-workloads/lightwell-poc-tenant"
            "/pnc-import/pnc-import:1.0.0.redhat-00001"
            "@sha256:9e1e77c92ee0de15d21f00a6d3ba1b6ad49a4ad3c8f8f7c8e7af2f0d8a5c3b1e"
        )
        assert extract_tag_from_image(image) == "1.0.0.redhat-00001"


# ---------------------------------------------------------------------------
# get_pipeline_definition_path
# ---------------------------------------------------------------------------


class TestGetPipelineDefinitionPath:
    """Test the get_pipeline_definition_path function."""

    def test_returns_path_relative_to_project_root(self):
        path = get_pipeline_definition_path()
        assert path.name == "pnc-import.yaml"
        assert path.parent.name == "pnc-import"
        assert path.parent.parent.name == "pipelines"
        assert path.parent.parent.parent.name == "tekton"

    def test_path_is_absolute(self):
        path = get_pipeline_definition_path()
        assert path.is_absolute()

    def test_path_exists_in_repository(self):
        """The actual pipeline file should exist in the repo."""
        path = get_pipeline_definition_path()
        assert path.exists(), f"Pipeline file not found at {path}"


# ---------------------------------------------------------------------------
# load_pipeline
# ---------------------------------------------------------------------------


class TestLoadAndPatchPipeline:
    """Test the load_pipeline function."""

    @pytest.fixture
    def pipeline_file(self, tmp_path: Path) -> Path:
        """Write a sample pipeline YAML to a temp file."""
        path = tmp_path / "pnc-import.yaml"
        with open(path, "w") as f:
            yaml.dump(SAMPLE_PIPELINE_YAML, f)
        return path

    def test_returns_spec_dict(self, pipeline_file: Path):
        result = load_pipeline(pipeline_file)
        assert isinstance(result, dict)
        assert "tasks" in result
        assert "params" in result

    def test_all_four_tasks_have_bundle_resolvers(self, pipeline_file: Path):
        result = load_pipeline(pipeline_file)

        bundle_tasks = [t for t in result["tasks"] if t["taskRef"].get("resolver") == "bundles"]
        assert len(bundle_tasks) == 4

    def test_preserves_catalog_task_refs(self, pipeline_file: Path):
        result = load_pipeline(pipeline_file)

        for task_name in ["clamav-scan", "sast-shell-check-oci-ta", "sast-unicode-check-oci-ta"]:
            matched = [
                t
                for t in result["tasks"]
                if t["taskRef"].get("resolver") == "bundles"
                and any(p["name"] == "name" and p["value"] == task_name for p in t["taskRef"]["params"])
            ]
            assert len(matched) == 1, f"Expected pre-resolved task for {task_name}"

    def test_raises_trigger_error_for_missing_file(self, tmp_path: Path):
        missing = tmp_path / "nonexistent.yaml"
        with pytest.raises(TriggerError, match="pipeline definition not found"):
            load_pipeline(missing)

    def test_raises_trigger_error_for_invalid_yaml(self, tmp_path: Path):
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text("{invalid: yaml: [")
        with pytest.raises(TriggerError, match="failed to load pipeline"):
            load_pipeline(bad_file)

    def test_raises_trigger_error_for_missing_spec_key(self, tmp_path: Path):
        no_spec = tmp_path / "no-spec.yaml"
        with open(no_spec, "w") as f:
            yaml.dump({"apiVersion": "tekton.dev/v1", "kind": "Pipeline"}, f)
        with pytest.raises(TriggerError, match="failed to load pipeline"):
            load_pipeline(no_spec)

    def test_preserves_task_params(self, pipeline_file: Path):
        """Verify that task parameters are preserved."""
        result = load_pipeline(pipeline_file)

        verify_task = next(t for t in result["tasks"] if t["name"] == "verify-and-mirror")
        assert "params" in verify_task
        param_names = [p["name"] for p in verify_task["params"]]
        assert "SOURCE_IMAGE" in param_names
        assert "IMAGE" in param_names

    def test_preserves_run_after(self, pipeline_file: Path):
        """Verify that runAfter fields are preserved."""
        result = load_pipeline(pipeline_file)

        clamav = next(t for t in result["tasks"] if t["name"] == "clamav-scan")
        assert clamav["runAfter"] == ["verify-and-mirror"]

    def test_loading_real_pipeline_file(self):
        """Verify that the actual pipeline file in the repo loads correctly."""
        path = get_pipeline_definition_path()
        if not path.exists():
            pytest.skip("Pipeline file not available in test environment")

        result = load_pipeline(path)
        assert "tasks" in result
        assert len(result["tasks"]) == 4

        bundle_tasks = [t for t in result["tasks"] if t["taskRef"].get("resolver") == "bundles"]
        assert len(bundle_tasks) == 4


# ---------------------------------------------------------------------------
# build_pipelinerun_manifest
# ---------------------------------------------------------------------------


class TestBuildPipelinerunManifest:
    """Test the build_pipelinerun_manifest function."""

    @pytest.fixture
    def pipeline_spec(self) -> dict:
        return {"tasks": [], "params": []}

    # Helper so callers don't need to repeat the two new required args every time.
    @staticmethod
    def _build(pipeline_spec, app="pnc-import", service_account="build-pipeline-pnc-import", **kw):
        return build_pipelinerun_manifest(
            source_image=kw.pop("source_image", "quay.io/src:tag@sha256:abc"),
            dest_image=kw.pop("dest_image", "quay.io/dst:tag"),
            pipeline_spec=pipeline_spec,
            app=app,
            service_account=service_account,
            prefix="pnc-import-",
            verification_secret="verification-public-key",
            **kw,
        )

    def test_apiversion_and_kind(self, pipeline_spec):
        manifest = self._build(pipeline_spec)
        assert manifest["apiVersion"] == "tekton.dev/v1"
        assert manifest["kind"] == "PipelineRun"

    def test_generate_name_prefix(self, pipeline_spec):
        manifest = self._build(pipeline_spec)
        assert manifest["metadata"]["generateName"] == "pnc-import-"

    def test_namespace(self, pipeline_spec):
        manifest = self._build(pipeline_spec)
        assert manifest["metadata"]["namespace"] == "lightwell-poc-tenant"

    def test_annotations(self, pipeline_spec):
        manifest = self._build(pipeline_spec)
        annotations = manifest["metadata"]["annotations"]
        assert annotations["test.appstudio.openshift.io/ignore-supersession"] == "true"

    def test_labels_for_rebuild_app(self, pipeline_spec):
        manifest = self._build(pipeline_spec)
        labels = manifest["metadata"]["labels"]
        assert labels["appstudio.openshift.io/application"] == "pnc-import"
        assert labels["appstudio.openshift.io/component"] == "pnc-import"
        assert labels["pipelines.appstudio.openshift.io/type"] == "build"

    def test_labels_for_remediated_app(self, pipeline_spec):
        manifest = self._build(
            pipeline_spec,
            app="pnc-import-remediated",
            service_account="build-pipeline-pnc-import-remediated",
        )
        labels = manifest["metadata"]["labels"]
        assert labels["appstudio.openshift.io/application"] == "pnc-import-remediated"
        assert labels["appstudio.openshift.io/component"] == "pnc-import-remediated"

    def test_service_account_in_task_run_template(self, pipeline_spec):
        manifest = self._build(pipeline_spec)
        sa = manifest["spec"]["taskRunTemplate"]["serviceAccountName"]
        assert sa == "build-pipeline-pnc-import"

    def test_pipeline_spec_embedded(self, pipeline_spec):
        manifest = self._build(pipeline_spec)
        assert manifest["spec"]["pipelineSpec"] is pipeline_spec

    def test_params_contain_source_and_dest(self, pipeline_spec):
        src = "quay.io/src:tag@sha256:abc"
        dst = "quay.io/dst:v1.0"
        manifest = self._build(pipeline_spec, source_image=src, dest_image=dst)
        params = {p["name"]: p["value"] for p in manifest["spec"]["params"]}
        assert params["SOURCE_IMAGE"] == src
        assert params["IMAGE"] == dst

    def test_manifest_is_valid_yaml(self, pipeline_spec):
        """The generated manifest should round-trip through YAML serialization."""
        manifest = self._build(
            pipeline_spec,
            source_image="quay.io/src:t@sha256:abc",
            dest_image="quay.io/dst:t",
            service_account="sa",
        )
        yaml_str = yaml.dump(manifest, default_flow_style=False)
        reloaded = yaml.safe_load(yaml_str)
        assert reloaded["kind"] == "PipelineRun"
        assert reloaded["spec"]["params"][0]["name"] == "SOURCE_IMAGE"


# ---------------------------------------------------------------------------
# PipelineRunBuilder.__init__
# ---------------------------------------------------------------------------


class TestPipelineRunBuilderInit:
    """Test PipelineRunBuilder initialization."""

    def test_rebuild_config(self):
        kube = MagicMock(spec=KubeClient)
        builder = PipelineRunBuilder(kube=kube, artifact_type="REBUILD")
        assert builder._config == ARTIFACT_CONFIGS["REBUILD"]

    def test_remediated_config(self):
        kube = MagicMock(spec=KubeClient)
        builder = PipelineRunBuilder(kube=kube, artifact_type="REMEDIATED")
        assert builder._config == ARTIFACT_CONFIGS["REMEDIATED"]

    def test_defaults_to_rebuild(self):
        kube = MagicMock(spec=KubeClient)
        builder = PipelineRunBuilder(kube=kube)
        assert builder._config == ARTIFACT_CONFIGS["REBUILD"]

    def test_invalid_artifact_type_raises_key_error(self):
        kube = MagicMock(spec=KubeClient)
        with pytest.raises(KeyError):
            PipelineRunBuilder(kube=kube, artifact_type="INVALID")


# ---------------------------------------------------------------------------
# PipelineRunBuilder.trigger (integration)
# ---------------------------------------------------------------------------


class TestPipelineRunBuilderTrigger:
    """Integration tests for the full trigger workflow."""

    @pytest.fixture
    def mock_kube(self):
        kube = MagicMock(spec=KubeClient)
        kube.create_pipelinerun.return_value = "pnc-import-abcde"
        return kube

    @pytest.fixture
    def pipeline_file(self, tmp_path: Path) -> Path:
        path = tmp_path / "pnc-import.yaml"
        with open(path, "w") as f:
            yaml.dump(SAMPLE_PIPELINE_YAML, f)
        return path

    @patch("import_orchestrator.engine.pipelinerun.get_pipeline_definition_path")
    @patch("import_orchestrator.engine.pipelinerun.digest_pin_image")
    def test_full_workflow_rebuild(
        self,
        mock_pin,
        mock_path,
        mock_kube,
        pipeline_file,
    ):
        mock_pin.return_value = "quay.io/repo:v1.0@sha256:abc123"
        mock_path.return_value = pipeline_file

        builder = PipelineRunBuilder(kube=mock_kube, artifact_type="REBUILD")
        result = builder.trigger("quay.io/repo:v1.0")

        assert result == "pnc-import-abcde"
        mock_kube.create_pipelinerun.assert_called_once()

        manifest = mock_kube.create_pipelinerun.call_args[0][0]
        assert manifest["kind"] == "PipelineRun"

        params = {p["name"]: p["value"] for p in manifest["spec"]["params"]}
        assert params["SOURCE_IMAGE"] == "quay.io/repo:v1.0@sha256:abc123"
        assert "pnc-import/pnc-import:v1.0" in params["IMAGE"]

        labels = manifest["metadata"]["labels"]
        assert labels["appstudio.openshift.io/application"] == "pnc-import"

        sa = manifest["spec"]["taskRunTemplate"]["serviceAccountName"]
        assert sa == "build-pipeline-pnc-import"

    @patch("import_orchestrator.engine.pipelinerun.get_pipeline_definition_path")
    @patch("import_orchestrator.engine.pipelinerun.digest_pin_image")
    def test_full_workflow_remediated(
        self,
        mock_pin,
        mock_path,
        mock_kube,
        pipeline_file,
    ):
        mock_pin.return_value = "quay.io/repo:v2.0@sha256:def456"
        mock_path.return_value = pipeline_file

        builder = PipelineRunBuilder(kube=mock_kube, artifact_type="REMEDIATED")
        result = builder.trigger("quay.io/repo:v2.0")

        assert result == "pnc-import-abcde"

        manifest = mock_kube.create_pipelinerun.call_args[0][0]

        params = {p["name"]: p["value"] for p in manifest["spec"]["params"]}
        assert "pnc-import-remediated/pnc-import-remediated:v2.0" in params["IMAGE"]

        labels = manifest["metadata"]["labels"]
        assert labels["appstudio.openshift.io/application"] == "pnc-import-remediated"

        sa = manifest["spec"]["taskRunTemplate"]["serviceAccountName"]
        assert sa == "build-pipeline-pnc-import-remediated"

    @patch("import_orchestrator.engine.pipelinerun.get_pipeline_definition_path")
    @patch("import_orchestrator.engine.pipelinerun.digest_pin_image")
    def test_tag_override(
        self,
        mock_pin,
        mock_path,
        mock_kube,
        pipeline_file,
    ):
        mock_pin.return_value = "quay.io/repo:v1.0@sha256:abc123"
        mock_path.return_value = pipeline_file

        builder = PipelineRunBuilder(kube=mock_kube, artifact_type="REBUILD")
        builder.trigger("quay.io/repo:v1.0", tag="custom-tag")

        manifest = mock_kube.create_pipelinerun.call_args[0][0]
        params = {p["name"]: p["value"] for p in manifest["spec"]["params"]}
        assert params["IMAGE"].endswith(":custom-tag")

    @patch("import_orchestrator.engine.pipelinerun.get_pipeline_definition_path")
    @patch("import_orchestrator.engine.pipelinerun.digest_pin_image")
    def test_already_pinned_image_skips_resolution(
        self,
        mock_pin,
        mock_path,
        mock_kube,
        pipeline_file,
    ):
        pinned = "quay.io/repo:v1.0@sha256:already_pinned"
        mock_pin.return_value = pinned
        mock_path.return_value = pipeline_file

        builder = PipelineRunBuilder(kube=mock_kube)
        builder.trigger(pinned)

        mock_pin.assert_called_once_with(pinned)

    @patch("import_orchestrator.engine.pipelinerun.digest_pin_image")
    def test_raises_trigger_error_on_digest_failure(self, mock_pin, mock_kube):
        mock_pin.side_effect = TriggerError("image reference must be digest-pinned")

        builder = PipelineRunBuilder(kube=mock_kube)
        with pytest.raises(TriggerError, match="must be digest-pinned"):
            builder.trigger("quay.io/bad:ref")

    @patch("import_orchestrator.engine.pipelinerun.get_pipeline_definition_path")
    @patch("import_orchestrator.engine.pipelinerun.digest_pin_image")
    def test_raises_trigger_error_on_missing_pipeline(self, mock_pin, mock_path, mock_kube, tmp_path):
        mock_pin.return_value = "quay.io/repo:v1@sha256:abc"
        mock_path.return_value = tmp_path / "nonexistent.yaml"

        builder = PipelineRunBuilder(kube=mock_kube)
        with pytest.raises(TriggerError, match="pipeline definition not found"):
            builder.trigger("quay.io/repo:v1")

    @patch("import_orchestrator.engine.pipelinerun.get_pipeline_definition_path")
    @patch("import_orchestrator.engine.pipelinerun.digest_pin_image")
    def test_raises_trigger_error_when_kube_returns_none(
        self,
        mock_pin,
        mock_path,
        mock_kube,
        pipeline_file,
    ):
        mock_pin.return_value = "quay.io/repo:v1.0@sha256:abc123"
        mock_path.return_value = pipeline_file
        mock_kube.create_pipelinerun.return_value = None

        builder = PipelineRunBuilder(kube=mock_kube)

        with pytest.raises(TriggerError, match="PipelineRun creation failed"):
            builder.trigger("quay.io/repo:v1.0")

    @patch("import_orchestrator.engine.pipelinerun.get_pipeline_definition_path")
    @patch("import_orchestrator.engine.pipelinerun.digest_pin_image")
    def test_manifest_pipeline_spec_has_bundle_resolver_tasks(
        self,
        mock_pin,
        mock_path,
        mock_kube,
        pipeline_file,
    ):
        """Verify that the embedded pipelineSpec has all tasks with bundle resolvers."""
        mock_pin.return_value = "quay.io/repo:v1.0@sha256:abc123"
        mock_path.return_value = pipeline_file

        builder = PipelineRunBuilder(kube=mock_kube)
        builder.trigger("quay.io/repo:v1.0")

        manifest = mock_kube.create_pipelinerun.call_args[0][0]
        tasks = manifest["spec"]["pipelineSpec"]["tasks"]

        bundle_tasks = [t for t in tasks if t["taskRef"].get("resolver") == "bundles"]
        assert len(bundle_tasks) == 4

    @patch("import_orchestrator.engine.pipelinerun.get_pipeline_definition_path")
    @patch("import_orchestrator.engine.pipelinerun.digest_pin_image")
    def test_rebuild_dest_repo(
        self,
        mock_pin,
        mock_path,
        mock_kube,
        pipeline_file,
    ):
        """Verify REBUILD uses the correct destination repo."""
        mock_pin.return_value = "quay.io/repo:v1.0@sha256:abc"
        mock_path.return_value = pipeline_file

        builder = PipelineRunBuilder(kube=mock_kube, artifact_type="REBUILD")
        builder.trigger("quay.io/repo:v1.0")

        manifest = mock_kube.create_pipelinerun.call_args[0][0]
        params = {p["name"]: p["value"] for p in manifest["spec"]["params"]}
        expected = "quay.io/redhat-user-workloads/lightwell-poc-tenant/pnc-import/pnc-import:v1.0"
        assert params["IMAGE"] == expected

    @patch("import_orchestrator.engine.pipelinerun.get_pipeline_definition_path")
    @patch("import_orchestrator.engine.pipelinerun.digest_pin_image")
    def test_remediated_dest_repo(
        self,
        mock_pin,
        mock_path,
        mock_kube,
        pipeline_file,
    ):
        """Verify REMEDIATED uses the correct destination repo."""
        mock_pin.return_value = "quay.io/repo:v1.0@sha256:abc"
        mock_path.return_value = pipeline_file

        builder = PipelineRunBuilder(kube=mock_kube, artifact_type="REMEDIATED")
        builder.trigger("quay.io/repo:v1.0")

        manifest = mock_kube.create_pipelinerun.call_args[0][0]
        params = {p["name"]: p["value"] for p in manifest["spec"]["params"]}
        expected = "quay.io/redhat-user-workloads/lightwell-poc-tenant/pnc-import-remediated/pnc-import-remediated:v1.0"
        assert params["IMAGE"] == expected
