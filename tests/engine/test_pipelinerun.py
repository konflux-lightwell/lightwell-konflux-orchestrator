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

import hashlib
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from import_orchestrator.clients.kube import KubeClient
from import_orchestrator.constants import ARTIFACT_CONFIGS, CATALOG_BUNDLE_REFS
from import_orchestrator.engine.pipelinerun import (
    PipelineRunBuilder,
    TriggerError,
    _compute_sha256_digest,
    _make_bundle_resolver_ref,
    _patch_task_ref,
    _skopeo_inspect_raw,
    build_pipelinerun_manifest,
    digest_pin_image,
    extract_tag_from_image,
    get_pipeline_definition_path,
    load_and_patch_pipeline,
    resolve_task_bundle,
)

# ---------------------------------------------------------------------------
# Sample pipeline YAML used by multiple test classes
# ---------------------------------------------------------------------------

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
                "taskRef": {"name": "oci-verify-import", "version": "0.1"},
                "params": [
                    {"name": "SOURCE_IMAGE", "value": "$(params.SOURCE_IMAGE)"},
                    {"name": "IMAGE", "value": "$(params.IMAGE)"},
                ],
            },
            {
                "name": "clamav-scan",
                "taskRef": {"name": "clamav-scan", "version": "0.3"},
                "runAfter": ["verify-and-mirror"],
                "params": [],
            },
            {
                "name": "sast-shell-check",
                "taskRef": {"name": "sast-shell-check-oci-ta", "version": "0.1"},
                "runAfter": ["verify-and-mirror"],
                "params": [],
            },
            {
                "name": "sast-unicode-check",
                "taskRef": {"name": "sast-unicode-check-oci-ta", "version": "0.4"},
                "runAfter": ["verify-and-mirror"],
                "params": [],
            },
        ],
    },
}

FAKE_MANIFEST_BYTES = b'{"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json"}'
FAKE_DIGEST = "sha256:" + hashlib.sha256(FAKE_MANIFEST_BYTES).hexdigest()

TASK_BUNDLE_REF = f"quay.io/konflux-ci/tekton-catalog/task-oci-verify-import:0.1@{FAKE_DIGEST}"


# ---------------------------------------------------------------------------
# _compute_sha256_digest
# ---------------------------------------------------------------------------


class TestComputeSha256Digest:
    """Test the _compute_sha256_digest helper."""

    def test_returns_sha256_prefixed_hex(self):
        data = b"hello world"
        result = _compute_sha256_digest(data)
        assert result.startswith("sha256:")
        assert result == "sha256:" + hashlib.sha256(data).hexdigest()

    def test_empty_input(self):
        result = _compute_sha256_digest(b"")
        assert result == "sha256:" + hashlib.sha256(b"").hexdigest()

    def test_deterministic_for_same_input(self):
        data = b"deterministic"
        assert _compute_sha256_digest(data) == _compute_sha256_digest(data)

    def test_different_inputs_produce_different_digests(self):
        assert _compute_sha256_digest(b"aaa") != _compute_sha256_digest(b"bbb")


# ---------------------------------------------------------------------------
# _skopeo_inspect_raw
# ---------------------------------------------------------------------------


class TestSkopeoInspectRaw:
    """Test the _skopeo_inspect_raw subprocess wrapper."""

    @patch("import_orchestrator.engine.pipelinerun.subprocess.run")
    def test_returns_stdout_bytes(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=FAKE_MANIFEST_BYTES, stderr=b""
        )

        result = _skopeo_inspect_raw("quay.io/repo:tag")
        assert result == FAKE_MANIFEST_BYTES

        mock_run.assert_called_once_with(
            ["skopeo", "inspect", "--raw", "docker://quay.io/repo:tag"],
            capture_output=True,
            check=True,
        )

    @patch("import_orchestrator.engine.pipelinerun.subprocess.run")
    def test_raises_trigger_error_on_subprocess_failure(self, mock_run):
        error = subprocess.CalledProcessError(1, "skopeo")
        error.stderr = b"unauthorized: access denied"
        mock_run.side_effect = error

        with pytest.raises(TriggerError, match="could not inspect"):
            _skopeo_inspect_raw("quay.io/private:latest")

    @patch("import_orchestrator.engine.pipelinerun.subprocess.run")
    def test_raises_trigger_error_on_empty_output(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")

        with pytest.raises(TriggerError, match="empty response"):
            _skopeo_inspect_raw("quay.io/repo:tag")

    @patch("import_orchestrator.engine.pipelinerun.subprocess.run")
    def test_includes_stderr_in_error_message(self, mock_run):
        error = subprocess.CalledProcessError(1, "skopeo")
        error.stderr = b"network timeout"
        mock_run.side_effect = error

        with pytest.raises(TriggerError, match="network timeout"):
            _skopeo_inspect_raw("quay.io/repo:tag")

    @patch("import_orchestrator.engine.pipelinerun.subprocess.run")
    def test_handles_none_stderr_in_error(self, mock_run):
        error = subprocess.CalledProcessError(1, "skopeo")
        error.stderr = None
        mock_run.side_effect = error

        with pytest.raises(TriggerError, match="could not inspect"):
            _skopeo_inspect_raw("quay.io/repo:tag")


# ---------------------------------------------------------------------------
# digest_pin_image
# ---------------------------------------------------------------------------


class TestDigestPinImage:
    """Test the digest_pin_image function."""

    def test_already_pinned_returns_unchanged(self):
        image = "quay.io/repo:tag@sha256:abc123"
        assert digest_pin_image(image) == image

    @patch("import_orchestrator.engine.pipelinerun._skopeo_inspect_raw")
    def test_resolves_unpinned_image(self, mock_inspect):
        mock_inspect.return_value = FAKE_MANIFEST_BYTES

        result = digest_pin_image("quay.io/repo:tag")
        assert result == f"quay.io/repo:tag@{FAKE_DIGEST}"
        mock_inspect.assert_called_once_with("quay.io/repo:tag")

    @patch("import_orchestrator.engine.pipelinerun._skopeo_inspect_raw")
    def test_propagates_trigger_error_from_skopeo(self, mock_inspect):
        mock_inspect.side_effect = TriggerError("inspect failed")

        with pytest.raises(TriggerError, match="inspect failed"):
            digest_pin_image("quay.io/bad:ref")


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

    def test_raises_trigger_error_for_digest_only(self):
        image = "quay.io/repo@sha256:abc123"
        with pytest.raises(TriggerError, match="could not extract tag"):
            extract_tag_from_image(image)

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
# resolve_task_bundle
# ---------------------------------------------------------------------------


class TestResolveTaskBundle:
    """Test the resolve_task_bundle function."""

    def test_uses_env_var_when_set(self, monkeypatch):
        pullspec = "quay.io/custom/bundle:latest@sha256:custom123"
        monkeypatch.setenv("TASK_BUNDLE_PULLSPEC", pullspec)

        result = resolve_task_bundle()
        assert result == pullspec

    @patch("import_orchestrator.engine.pipelinerun._skopeo_inspect_raw")
    def test_resolves_via_skopeo_when_no_env_var(self, mock_inspect, monkeypatch):
        monkeypatch.delenv("TASK_BUNDLE_PULLSPEC", raising=False)
        mock_inspect.return_value = FAKE_MANIFEST_BYTES

        result = resolve_task_bundle()
        expected = f"quay.io/konflux-ci/tekton-catalog/task-oci-verify-import:0.1@{FAKE_DIGEST}"
        assert result == expected
        mock_inspect.assert_called_once_with("quay.io/konflux-ci/tekton-catalog/task-oci-verify-import:0.1")

    @patch("import_orchestrator.engine.pipelinerun._skopeo_inspect_raw")
    def test_propagates_skopeo_error(self, mock_inspect, monkeypatch):
        monkeypatch.delenv("TASK_BUNDLE_PULLSPEC", raising=False)
        mock_inspect.side_effect = TriggerError("skopeo failed")

        with pytest.raises(TriggerError, match="skopeo failed"):
            resolve_task_bundle()

    def test_empty_env_var_falls_through(self, monkeypatch):
        """An empty TASK_BUNDLE_PULLSPEC should trigger skopeo resolution."""
        monkeypatch.setenv("TASK_BUNDLE_PULLSPEC", "")
        with patch("import_orchestrator.engine.pipelinerun._skopeo_inspect_raw") as mock_inspect:
            mock_inspect.return_value = FAKE_MANIFEST_BYTES
            result = resolve_task_bundle()
            assert "@" in result
            mock_inspect.assert_called_once()


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
# _make_bundle_resolver_ref
# ---------------------------------------------------------------------------


class TestMakeBundleResolverRef:
    """Test the _make_bundle_resolver_ref helper."""

    def test_structure(self):
        result = _make_bundle_resolver_ref("my-task", "quay.io/bundle:v1@sha256:abc")

        assert result["resolver"] == "bundles"
        assert isinstance(result["params"], list)
        assert len(result["params"]) == 3

    def test_params_contain_bundle_name_kind(self):
        bundle = "quay.io/bundle:v1@sha256:abc"
        result = _make_bundle_resolver_ref("my-task", bundle)

        params_dict = {p["name"]: p["value"] for p in result["params"]}
        assert params_dict["bundle"] == bundle
        assert params_dict["name"] == "my-task"
        assert params_dict["kind"] == "Task"

    def test_no_extra_keys(self):
        result = _make_bundle_resolver_ref("t", "b")
        assert set(result.keys()) == {"resolver", "params"}


# ---------------------------------------------------------------------------
# _patch_task_ref
# ---------------------------------------------------------------------------


class TestPatchTaskRef:
    """Test the _patch_task_ref in-place patching logic."""

    def test_patches_oci_verify_import(self):
        task = {"taskRef": {"name": "oci-verify-import", "version": "0.1"}}
        _patch_task_ref(task, TASK_BUNDLE_REF)

        assert task["taskRef"]["resolver"] == "bundles"
        params = {p["name"]: p["value"] for p in task["taskRef"]["params"]}
        assert params["bundle"] == TASK_BUNDLE_REF
        assert params["name"] == "oci-verify-import"
        assert params["kind"] == "Task"

    def test_patches_catalog_task_clamav(self):
        task = {"taskRef": {"name": "clamav-scan", "version": "0.3"}}
        _patch_task_ref(task, TASK_BUNDLE_REF)

        assert task["taskRef"]["resolver"] == "bundles"
        params = {p["name"]: p["value"] for p in task["taskRef"]["params"]}
        assert params["bundle"] == CATALOG_BUNDLE_REFS["clamav-scan"]
        assert params["name"] == "clamav-scan"

    def test_patches_catalog_task_sast_shell(self):
        task = {"taskRef": {"name": "sast-shell-check-oci-ta", "version": "0.1"}}
        _patch_task_ref(task, TASK_BUNDLE_REF)

        params = {p["name"]: p["value"] for p in task["taskRef"]["params"]}
        assert params["bundle"] == CATALOG_BUNDLE_REFS["sast-shell-check-oci-ta"]

    def test_patches_catalog_task_sast_unicode(self):
        task = {"taskRef": {"name": "sast-unicode-check-oci-ta", "version": "0.4"}}
        _patch_task_ref(task, TASK_BUNDLE_REF)

        params = {p["name"]: p["value"] for p in task["taskRef"]["params"]}
        assert params["bundle"] == CATALOG_BUNDLE_REFS["sast-unicode-check-oci-ta"]

    def test_strips_version_from_unknown_task_with_version(self):
        task = {"taskRef": {"name": "some-other-task", "version": "1.0"}}
        _patch_task_ref(task, TASK_BUNDLE_REF)

        assert "version" not in task["taskRef"]
        assert task["taskRef"]["name"] == "some-other-task"
        assert "resolver" not in task["taskRef"]

    def test_leaves_task_without_version_unchanged(self):
        task = {"taskRef": {"name": "no-version-task"}}
        original = {"taskRef": {"name": "no-version-task"}}
        _patch_task_ref(task, TASK_BUNDLE_REF)

        assert task == original

    def test_handles_empty_taskref(self):
        task = {"taskRef": {}}
        _patch_task_ref(task, TASK_BUNDLE_REF)
        assert task == {"taskRef": {}}

    def test_handles_missing_taskref(self):
        task = {"name": "no-ref-task"}
        _patch_task_ref(task, TASK_BUNDLE_REF)
        assert task == {"name": "no-ref-task"}


# ---------------------------------------------------------------------------
# load_and_patch_pipeline
# ---------------------------------------------------------------------------


class TestLoadAndPatchPipeline:
    """Test the load_and_patch_pipeline function."""

    @pytest.fixture
    def pipeline_file(self, tmp_path: Path) -> Path:
        """Write a sample pipeline YAML to a temp file."""
        path = tmp_path / "pnc-import.yaml"
        with open(path, "w") as f:
            yaml.dump(SAMPLE_PIPELINE_YAML, f)
        return path

    def test_returns_spec_dict(self, pipeline_file: Path):
        result = load_and_patch_pipeline(pipeline_file, TASK_BUNDLE_REF)
        assert isinstance(result, dict)
        assert "tasks" in result
        assert "params" in result

    def test_patches_oci_verify_import_task(self, pipeline_file: Path):
        result = load_and_patch_pipeline(pipeline_file, TASK_BUNDLE_REF)

        verify_task = next(t for t in result["tasks"] if t["name"] == "verify-and-mirror")
        assert verify_task["taskRef"]["resolver"] == "bundles"
        params = {p["name"]: p["value"] for p in verify_task["taskRef"]["params"]}
        assert params["bundle"] == TASK_BUNDLE_REF
        assert params["name"] == "oci-verify-import"

    def test_patches_all_catalog_tasks(self, pipeline_file: Path):
        result = load_and_patch_pipeline(pipeline_file, TASK_BUNDLE_REF)

        for task_name in ["clamav-scan", "sast-shell-check-oci-ta", "sast-unicode-check-oci-ta"]:
            # Find the task by looking for the bundle resolver param that has the task name
            matched = [
                t
                for t in result["tasks"]
                if t["taskRef"].get("resolver") == "bundles"
                and any(p["name"] == "name" and p["value"] == task_name for p in t["taskRef"]["params"])
            ]
            assert len(matched) == 1, f"Expected exactly one patched task for {task_name}"

    def test_all_four_tasks_become_bundle_resolvers(self, pipeline_file: Path):
        result = load_and_patch_pipeline(pipeline_file, TASK_BUNDLE_REF)

        bundle_tasks = [t for t in result["tasks"] if t["taskRef"].get("resolver") == "bundles"]
        assert len(bundle_tasks) == 4

    def test_raises_trigger_error_for_missing_file(self, tmp_path: Path):
        missing = tmp_path / "nonexistent.yaml"
        with pytest.raises(TriggerError, match="pipeline definition not found"):
            load_and_patch_pipeline(missing, TASK_BUNDLE_REF)

    def test_raises_trigger_error_for_invalid_yaml(self, tmp_path: Path):
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text("{invalid: yaml: [")
        with pytest.raises(TriggerError, match="failed to load pipeline"):
            load_and_patch_pipeline(bad_file, TASK_BUNDLE_REF)

    def test_raises_trigger_error_for_missing_spec_key(self, tmp_path: Path):
        no_spec = tmp_path / "no-spec.yaml"
        with open(no_spec, "w") as f:
            yaml.dump({"apiVersion": "tekton.dev/v1", "kind": "Pipeline"}, f)
        with pytest.raises(TriggerError, match="failed to load pipeline"):
            load_and_patch_pipeline(no_spec, TASK_BUNDLE_REF)

    def test_preserves_task_params(self, pipeline_file: Path):
        """Verify that patching does not remove task parameters."""
        result = load_and_patch_pipeline(pipeline_file, TASK_BUNDLE_REF)

        verify_task = next(t for t in result["tasks"] if t["name"] == "verify-and-mirror")
        assert "params" in verify_task
        param_names = [p["name"] for p in verify_task["params"]]
        assert "SOURCE_IMAGE" in param_names
        assert "IMAGE" in param_names

    def test_preserves_run_after(self, pipeline_file: Path):
        """Verify that runAfter fields survive patching."""
        result = load_and_patch_pipeline(pipeline_file, TASK_BUNDLE_REF)

        clamav = next(t for t in result["tasks"] if t["name"] == "clamav-scan")
        assert clamav["runAfter"] == ["verify-and-mirror"]

    def test_loading_real_pipeline_file(self):
        """Verify that the actual pipeline file in the repo loads and patches correctly."""
        path = get_pipeline_definition_path()
        if not path.exists():
            pytest.skip("Pipeline file not available in test environment")

        result = load_and_patch_pipeline(path, TASK_BUNDLE_REF)
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

    def test_apiversion_and_kind(self, pipeline_spec):
        manifest = build_pipelinerun_manifest(
            source_image="quay.io/src:tag@sha256:abc",
            dest_image="quay.io/dst:tag",
            pipeline_spec=pipeline_spec,
            app="pnc-import",
            service_account="build-pipeline-pnc-import",
        )
        assert manifest["apiVersion"] == "tekton.dev/v1"
        assert manifest["kind"] == "PipelineRun"

    def test_generate_name_prefix(self, pipeline_spec):
        manifest = build_pipelinerun_manifest(
            source_image="quay.io/src:tag@sha256:abc",
            dest_image="quay.io/dst:tag",
            pipeline_spec=pipeline_spec,
            app="pnc-import",
            service_account="build-pipeline-pnc-import",
        )
        assert manifest["metadata"]["generateName"] == "pnc-import-"

    def test_namespace(self, pipeline_spec):
        manifest = build_pipelinerun_manifest(
            source_image="quay.io/src:tag@sha256:abc",
            dest_image="quay.io/dst:tag",
            pipeline_spec=pipeline_spec,
            app="pnc-import",
            service_account="build-pipeline-pnc-import",
        )
        assert manifest["metadata"]["namespace"] == "lightwell-poc-tenant"

    def test_annotations(self, pipeline_spec):
        manifest = build_pipelinerun_manifest(
            source_image="quay.io/src:tag@sha256:abc",
            dest_image="quay.io/dst:tag",
            pipeline_spec=pipeline_spec,
            app="pnc-import",
            service_account="build-pipeline-pnc-import",
        )
        annotations = manifest["metadata"]["annotations"]
        assert annotations["test.appstudio.openshift.io/ignore-supersession"] == "true"

    def test_labels_for_rebuild_app(self, pipeline_spec):
        manifest = build_pipelinerun_manifest(
            source_image="quay.io/src:tag@sha256:abc",
            dest_image="quay.io/dst:tag",
            pipeline_spec=pipeline_spec,
            app="pnc-import",
            service_account="build-pipeline-pnc-import",
        )
        labels = manifest["metadata"]["labels"]
        assert labels["appstudio.openshift.io/application"] == "pnc-import"
        assert labels["appstudio.openshift.io/component"] == "pnc-import"
        assert labels["pipelines.appstudio.openshift.io/type"] == "build"

    def test_labels_for_remediated_app(self, pipeline_spec):
        manifest = build_pipelinerun_manifest(
            source_image="quay.io/src:tag@sha256:abc",
            dest_image="quay.io/dst:tag",
            pipeline_spec=pipeline_spec,
            app="pnc-import-remediated",
            service_account="build-pipeline-pnc-import-remediated",
        )
        labels = manifest["metadata"]["labels"]
        assert labels["appstudio.openshift.io/application"] == "pnc-import-remediated"
        assert labels["appstudio.openshift.io/component"] == "pnc-import-remediated"

    def test_service_account_in_task_run_template(self, pipeline_spec):
        manifest = build_pipelinerun_manifest(
            source_image="quay.io/src:tag@sha256:abc",
            dest_image="quay.io/dst:tag",
            pipeline_spec=pipeline_spec,
            app="pnc-import",
            service_account="build-pipeline-pnc-import",
        )
        sa = manifest["spec"]["taskRunTemplate"]["serviceAccountName"]
        assert sa == "build-pipeline-pnc-import"

    def test_pipeline_spec_embedded(self, pipeline_spec):
        manifest = build_pipelinerun_manifest(
            source_image="quay.io/src:tag@sha256:abc",
            dest_image="quay.io/dst:tag",
            pipeline_spec=pipeline_spec,
            app="pnc-import",
            service_account="build-pipeline-pnc-import",
        )
        assert manifest["spec"]["pipelineSpec"] is pipeline_spec

    def test_params_contain_source_and_dest(self, pipeline_spec):
        src = "quay.io/src:tag@sha256:abc"
        dst = "quay.io/dst:v1.0"
        manifest = build_pipelinerun_manifest(
            source_image=src,
            dest_image=dst,
            pipeline_spec=pipeline_spec,
            app="pnc-import",
            service_account="build-pipeline-pnc-import",
        )
        params = {p["name"]: p["value"] for p in manifest["spec"]["params"]}
        assert params["SOURCE_IMAGE"] == src
        assert params["IMAGE"] == dst

    def test_manifest_is_valid_yaml(self, pipeline_spec):
        """The generated manifest should round-trip through YAML serialization."""
        manifest = build_pipelinerun_manifest(
            source_image="quay.io/src:t@sha256:abc",
            dest_image="quay.io/dst:t",
            pipeline_spec=pipeline_spec,
            app="pnc-import",
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

    @patch("import_orchestrator.engine.pipelinerun.resolve_task_bundle")
    @patch("import_orchestrator.engine.pipelinerun.get_pipeline_definition_path")
    @patch("import_orchestrator.engine.pipelinerun.digest_pin_image")
    def test_full_workflow_rebuild(
        self,
        mock_pin,
        mock_path,
        mock_resolve,
        mock_kube,
        pipeline_file,
    ):
        mock_pin.return_value = "quay.io/repo:v1.0@sha256:abc123"
        mock_resolve.return_value = TASK_BUNDLE_REF
        mock_path.return_value = pipeline_file

        builder = PipelineRunBuilder(kube=mock_kube, artifact_type="REBUILD")
        result = builder.trigger("quay.io/repo:v1.0")

        assert result == "pnc-import-abcde"
        mock_kube.create_pipelinerun.assert_called_once()

        # Verify the YAML manifest passed to create_pipelinerun
        manifest_yaml = mock_kube.create_pipelinerun.call_args[0][0]
        manifest = yaml.safe_load(manifest_yaml)
        assert manifest["kind"] == "PipelineRun"

        params = {p["name"]: p["value"] for p in manifest["spec"]["params"]}
        assert params["SOURCE_IMAGE"] == "quay.io/repo:v1.0@sha256:abc123"
        assert "pnc-import/pnc-import:v1.0" in params["IMAGE"]

        labels = manifest["metadata"]["labels"]
        assert labels["appstudio.openshift.io/application"] == "pnc-import"

        sa = manifest["spec"]["taskRunTemplate"]["serviceAccountName"]
        assert sa == "build-pipeline-pnc-import"

    @patch("import_orchestrator.engine.pipelinerun.resolve_task_bundle")
    @patch("import_orchestrator.engine.pipelinerun.get_pipeline_definition_path")
    @patch("import_orchestrator.engine.pipelinerun.digest_pin_image")
    def test_full_workflow_remediated(
        self,
        mock_pin,
        mock_path,
        mock_resolve,
        mock_kube,
        pipeline_file,
    ):
        mock_pin.return_value = "quay.io/repo:v2.0@sha256:def456"
        mock_resolve.return_value = TASK_BUNDLE_REF
        mock_path.return_value = pipeline_file

        builder = PipelineRunBuilder(kube=mock_kube, artifact_type="REMEDIATED")
        result = builder.trigger("quay.io/repo:v2.0")

        assert result == "pnc-import-abcde"

        manifest_yaml = mock_kube.create_pipelinerun.call_args[0][0]
        manifest = yaml.safe_load(manifest_yaml)

        params = {p["name"]: p["value"] for p in manifest["spec"]["params"]}
        assert "pnc-import-remediated/pnc-import-remediated:v2.0" in params["IMAGE"]

        labels = manifest["metadata"]["labels"]
        assert labels["appstudio.openshift.io/application"] == "pnc-import-remediated"

        sa = manifest["spec"]["taskRunTemplate"]["serviceAccountName"]
        assert sa == "build-pipeline-pnc-import-remediated"

    @patch("import_orchestrator.engine.pipelinerun.resolve_task_bundle")
    @patch("import_orchestrator.engine.pipelinerun.get_pipeline_definition_path")
    @patch("import_orchestrator.engine.pipelinerun.digest_pin_image")
    def test_tag_override(
        self,
        mock_pin,
        mock_path,
        mock_resolve,
        mock_kube,
        pipeline_file,
    ):
        mock_pin.return_value = "quay.io/repo:v1.0@sha256:abc123"
        mock_resolve.return_value = TASK_BUNDLE_REF
        mock_path.return_value = pipeline_file

        builder = PipelineRunBuilder(kube=mock_kube, artifact_type="REBUILD")
        builder.trigger("quay.io/repo:v1.0", tag="custom-tag")

        manifest_yaml = mock_kube.create_pipelinerun.call_args[0][0]
        manifest = yaml.safe_load(manifest_yaml)
        params = {p["name"]: p["value"] for p in manifest["spec"]["params"]}
        assert params["IMAGE"].endswith(":custom-tag")

    @patch("import_orchestrator.engine.pipelinerun.resolve_task_bundle")
    @patch("import_orchestrator.engine.pipelinerun.get_pipeline_definition_path")
    @patch("import_orchestrator.engine.pipelinerun.digest_pin_image")
    def test_already_pinned_image_skips_resolution(
        self,
        mock_pin,
        mock_path,
        mock_resolve,
        mock_kube,
        pipeline_file,
    ):
        pinned = "quay.io/repo:v1.0@sha256:already_pinned"
        mock_pin.return_value = pinned
        mock_resolve.return_value = TASK_BUNDLE_REF
        mock_path.return_value = pipeline_file

        builder = PipelineRunBuilder(kube=mock_kube)
        builder.trigger(pinned)

        mock_pin.assert_called_once_with(pinned)

    @patch("import_orchestrator.engine.pipelinerun.digest_pin_image")
    def test_raises_trigger_error_on_digest_failure(self, mock_pin, mock_kube):
        mock_pin.side_effect = TriggerError("skopeo inspect failed")

        builder = PipelineRunBuilder(kube=mock_kube)
        with pytest.raises(TriggerError, match="skopeo inspect failed"):
            builder.trigger("quay.io/bad:ref")

    @patch("import_orchestrator.engine.pipelinerun.resolve_task_bundle")
    @patch("import_orchestrator.engine.pipelinerun.digest_pin_image")
    def test_raises_trigger_error_on_bundle_resolution_failure(self, mock_pin, mock_resolve, mock_kube):
        mock_pin.return_value = "quay.io/repo:v1@sha256:abc"
        mock_resolve.side_effect = TriggerError("bundle resolution failed")

        builder = PipelineRunBuilder(kube=mock_kube)
        with pytest.raises(TriggerError, match="bundle resolution failed"):
            builder.trigger("quay.io/repo:v1")

    @patch("import_orchestrator.engine.pipelinerun.resolve_task_bundle")
    @patch("import_orchestrator.engine.pipelinerun.get_pipeline_definition_path")
    @patch("import_orchestrator.engine.pipelinerun.digest_pin_image")
    def test_raises_trigger_error_on_missing_pipeline(self, mock_pin, mock_path, mock_resolve, mock_kube, tmp_path):
        mock_pin.return_value = "quay.io/repo:v1@sha256:abc"
        mock_resolve.return_value = TASK_BUNDLE_REF
        mock_path.return_value = tmp_path / "nonexistent.yaml"

        builder = PipelineRunBuilder(kube=mock_kube)
        with pytest.raises(TriggerError, match="pipeline definition not found"):
            builder.trigger("quay.io/repo:v1")

    @patch("import_orchestrator.engine.pipelinerun.resolve_task_bundle")
    @patch("import_orchestrator.engine.pipelinerun.get_pipeline_definition_path")
    @patch("import_orchestrator.engine.pipelinerun.digest_pin_image")
    def test_returns_none_when_kube_returns_none(
        self,
        mock_pin,
        mock_path,
        mock_resolve,
        mock_kube,
        pipeline_file,
    ):
        mock_pin.return_value = "quay.io/repo:v1.0@sha256:abc123"
        mock_resolve.return_value = TASK_BUNDLE_REF
        mock_path.return_value = pipeline_file
        mock_kube.create_pipelinerun.return_value = None

        builder = PipelineRunBuilder(kube=mock_kube)
        result = builder.trigger("quay.io/repo:v1.0")

        assert result is None

    @patch("import_orchestrator.engine.pipelinerun.resolve_task_bundle")
    @patch("import_orchestrator.engine.pipelinerun.get_pipeline_definition_path")
    @patch("import_orchestrator.engine.pipelinerun.digest_pin_image")
    def test_manifest_pipeline_spec_has_patched_tasks(
        self,
        mock_pin,
        mock_path,
        mock_resolve,
        mock_kube,
        pipeline_file,
    ):
        """Verify that the embedded pipelineSpec has all tasks converted to bundle resolvers."""
        mock_pin.return_value = "quay.io/repo:v1.0@sha256:abc123"
        mock_resolve.return_value = TASK_BUNDLE_REF
        mock_path.return_value = pipeline_file

        builder = PipelineRunBuilder(kube=mock_kube)
        builder.trigger("quay.io/repo:v1.0")

        manifest_yaml = mock_kube.create_pipelinerun.call_args[0][0]
        manifest = yaml.safe_load(manifest_yaml)
        tasks = manifest["spec"]["pipelineSpec"]["tasks"]

        bundle_tasks = [t for t in tasks if t["taskRef"].get("resolver") == "bundles"]
        assert len(bundle_tasks) == 4

    @patch("import_orchestrator.engine.pipelinerun.resolve_task_bundle")
    @patch("import_orchestrator.engine.pipelinerun.get_pipeline_definition_path")
    @patch("import_orchestrator.engine.pipelinerun.digest_pin_image")
    def test_rebuild_dest_repo(
        self,
        mock_pin,
        mock_path,
        mock_resolve,
        mock_kube,
        pipeline_file,
    ):
        """Verify REBUILD uses the correct destination repo."""
        mock_pin.return_value = "quay.io/repo:v1.0@sha256:abc"
        mock_resolve.return_value = TASK_BUNDLE_REF
        mock_path.return_value = pipeline_file

        builder = PipelineRunBuilder(kube=mock_kube, artifact_type="REBUILD")
        builder.trigger("quay.io/repo:v1.0")

        manifest_yaml = mock_kube.create_pipelinerun.call_args[0][0]
        manifest = yaml.safe_load(manifest_yaml)
        params = {p["name"]: p["value"] for p in manifest["spec"]["params"]}
        expected = "quay.io/redhat-user-workloads/lightwell-poc-tenant/pnc-import/pnc-import:v1.0"
        assert params["IMAGE"] == expected

    @patch("import_orchestrator.engine.pipelinerun.resolve_task_bundle")
    @patch("import_orchestrator.engine.pipelinerun.get_pipeline_definition_path")
    @patch("import_orchestrator.engine.pipelinerun.digest_pin_image")
    def test_remediated_dest_repo(
        self,
        mock_pin,
        mock_path,
        mock_resolve,
        mock_kube,
        pipeline_file,
    ):
        """Verify REMEDIATED uses the correct destination repo."""
        mock_pin.return_value = "quay.io/repo:v1.0@sha256:abc"
        mock_resolve.return_value = TASK_BUNDLE_REF
        mock_path.return_value = pipeline_file

        builder = PipelineRunBuilder(kube=mock_kube, artifact_type="REMEDIATED")
        builder.trigger("quay.io/repo:v1.0")

        manifest_yaml = mock_kube.create_pipelinerun.call_args[0][0]
        manifest = yaml.safe_load(manifest_yaml)
        params = {p["name"]: p["value"] for p in manifest["spec"]["params"]}
        expected = "quay.io/redhat-user-workloads/lightwell-poc-tenant/pnc-import-remediated/pnc-import-remediated:v1.0"
        assert params["IMAGE"] == expected
