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

# Build and submit PNC import PipelineRuns to a Konflux cluster.
#
# Replaces the shell script trigger-pnc-import.sh with native Python.
# The only behavioral change from the shell script is that the pipeline
# definition is loaded from tekton/ in the main repository instead of
# from the build-definitions submodule.

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from import_orchestrator.clients.kube import KubeClient
from import_orchestrator.constants import (
    ARTIFACT_CONFIGS,
    CATALOG_BUNDLE_REFS,
    NAMESPACE,
    TASK_BUNDLE_BASE,
    TASK_BUNDLE_FLOATING_TAG,
)


class TriggerError(Exception):
    """Non-recoverable error during the PipelineRun trigger process."""


# ---------------------------------------------------------------------------
# Image digest resolution
# ---------------------------------------------------------------------------


def _compute_sha256_digest(raw_bytes: bytes) -> str:
    """Compute ``sha256:<hex>`` from raw manifest bytes."""
    return "sha256:" + hashlib.sha256(raw_bytes).hexdigest()


def _skopeo_inspect_raw(image_ref: str) -> bytes:
    """Run ``skopeo inspect --raw`` and return the raw manifest bytes.

    Raises:
        TriggerError: If skopeo fails or returns empty output.
    """
    try:
        result = subprocess.run(
            ["skopeo", "inspect", "--raw", f"docker://{image_ref}"],
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        stderr_text = e.stderr.decode() if e.stderr else ""
        raise TriggerError(
            f"could not inspect {image_ref} -- check credentials and image reference: {stderr_text}"
        ) from e

    if not result.stdout:
        raise TriggerError(f"could not inspect {image_ref} -- empty response from skopeo")

    return result.stdout


def digest_pin_image(source_image: str) -> str:
    """Ensure *source_image* is digest-pinned, resolving via skopeo if needed."""
    if "@sha256:" in source_image:
        return source_image

    print(f"Resolving digest for {source_image}...", file=sys.stderr)
    raw = _skopeo_inspect_raw(source_image)
    digest = _compute_sha256_digest(raw)
    pinned = f"{source_image}@{digest}"
    print(f"Pinned:  {pinned}", file=sys.stderr)
    return pinned


def extract_tag_from_image(source_image: str) -> str:
    """Extract the tag portion from a digest-pinned OCI reference.

    From ``repo:tag@sha256:...`` returns ``tag``.

    Raises:
        TriggerError: If no tag can be parsed from the reference.
    """
    match = re.search(r":([^@]+)@", source_image)
    if match:
        return match.group(1)
    raise TriggerError(f"could not extract tag from {source_image}")


def resolve_task_bundle() -> str:
    """Resolve the oci-verify-import task bundle to a digest-pinned pullspec.

    Uses the ``TASK_BUNDLE_PULLSPEC`` environment variable if set; otherwise
    resolves the floating tag via skopeo.
    """
    if pullspec := os.environ.get("TASK_BUNDLE_PULLSPEC"):
        return pullspec

    floating_ref = f"{TASK_BUNDLE_BASE}:{TASK_BUNDLE_FLOATING_TAG}"
    raw = _skopeo_inspect_raw(floating_ref)
    digest = _compute_sha256_digest(raw)
    return f"{floating_ref}@{digest}"


# ---------------------------------------------------------------------------
# Pipeline loading and patching
# ---------------------------------------------------------------------------


def get_pipeline_definition_path() -> Path:
    """Return the path to the pnc-import pipeline definition.

    The path can be configured via the TEKTON_PIPELINE_DIR environment variable.
    If not set, falls back to the development layout (relative to the source file).
    """
    # Allow override via environment variable for deployed environments
    if pipeline_dir := os.environ.get("TEKTON_PIPELINE_DIR"):
        return Path(pipeline_dir) / "pipelines" / "pnc-import" / "pnc-import.yaml"

    # Development fallback: navigate from source file location
    # pipelinerun.py -> engine/ -> import_orchestrator/ -> src/ -> <project root>
    project_root = Path(__file__).resolve().parents[3]
    return project_root / "tekton" / "pipelines" / "pnc-import" / "pnc-import.yaml"


def load_and_patch_pipeline(pipeline_path: Path, task_bundle_ref: str) -> dict[str, Any]:
    """Load the pipeline YAML and patch taskRefs to use bundle resolvers.

    - ``oci-verify-import`` is patched to use *task_bundle_ref*.
    - Catalog tasks listed in ``CATALOG_BUNDLE_REFS`` are resolved to
      their digest-pinned bundles.
    - Any remaining tasks with a ``version`` field (not in the catalog)
      have the ``version`` stripped.

    Returns:
        The patched ``spec`` dict from the Pipeline resource.

    Raises:
        TriggerError: If the pipeline file cannot be found or parsed.
    """
    if not pipeline_path.exists():
        raise TriggerError(f"pipeline definition not found: {pipeline_path}")

    try:
        with open(pipeline_path) as f:
            pipeline = yaml.safe_load(f)

        for task in pipeline["spec"]["tasks"]:
            _patch_task_ref(task, task_bundle_ref)

        return pipeline["spec"]
    except (yaml.YAMLError, KeyError, TypeError) as e:
        raise TriggerError(f"failed to load pipeline from {pipeline_path}: {e}") from e


def _patch_task_ref(task: dict[str, Any], task_bundle_ref: str) -> None:
    """Patch a single task's taskRef in-place to use the bundle resolver."""
    ref = task.get("taskRef", {})
    name = ref.get("name", "")

    if name == "oci-verify-import":
        task["taskRef"] = _make_bundle_resolver_ref(name, task_bundle_ref)
    elif "version" in ref:
        if name in CATALOG_BUNDLE_REFS:
            task["taskRef"] = _make_bundle_resolver_ref(name, CATALOG_BUNDLE_REFS[name])
        else:
            del ref["version"]


def _make_bundle_resolver_ref(task_name: str, bundle_pullspec: str) -> dict[str, Any]:
    """Build a Tekton ``bundles`` resolver taskRef dict."""
    return {
        "resolver": "bundles",
        "params": [
            {"name": "bundle", "value": bundle_pullspec},
            {"name": "name", "value": task_name},
            {"name": "kind", "value": "Task"},
        ],
    }


# ---------------------------------------------------------------------------
# PipelineRun manifest
# ---------------------------------------------------------------------------


def build_pipelinerun_manifest(
    *,
    source_image: str,
    dest_image: str,
    pipeline_spec: dict[str, Any],
    app: str,
    service_account: str,
) -> dict[str, Any]:
    """Build a complete PipelineRun manifest dict ready for ``kubectl create``."""
    return {
        "apiVersion": "tekton.dev/v1",
        "kind": "PipelineRun",
        "metadata": {
            "generateName": "pnc-import-",
            "namespace": NAMESPACE,
            "annotations": {
                "test.appstudio.openshift.io/ignore-supersession": "true",
            },
            "labels": {
                "appstudio.openshift.io/application": app,
                "appstudio.openshift.io/component": app,
                "pipelines.appstudio.openshift.io/type": "build",
            },
        },
        "spec": {
            "taskRunTemplate": {"serviceAccountName": service_account},
            "pipelineSpec": pipeline_spec,
            "params": [
                {"name": "SOURCE_IMAGE", "value": source_image},
                {"name": "IMAGE", "value": dest_image},
            ],
        },
    }


# ---------------------------------------------------------------------------
# Orchestrator class
# ---------------------------------------------------------------------------


class PipelineRunBuilder:
    """Builds and submits PNC import PipelineRuns to a Konflux cluster.

    Orchestrates the full workflow: digest-pin the source image, resolve
    the task bundle, load and patch the pipeline definition, build the
    PipelineRun manifest, and submit it via kubectl.
    """

    def __init__(self, kube: KubeClient, artifact_type: str = "REBUILD"):
        self.kube = kube
        self._config = ARTIFACT_CONFIGS[artifact_type]

    def trigger(self, source_image: str, tag: str | None = None) -> str | None:
        """Execute the full trigger workflow.

        Args:
            source_image: OCI image reference (will be digest-pinned if needed).
            tag: Optional destination tag override; derived from source_image if omitted.

        Returns:
            The generated PipelineRun name, or None if it could not be parsed.

        Raises:
            TriggerError: If any step in the workflow fails.
        """
        source_image = digest_pin_image(source_image)
        tag = tag or extract_tag_from_image(source_image)
        task_bundle_ref = resolve_task_bundle()

        dest_image = f"{self._config['dest_repo']}:{tag}"

        print(f"SOURCE_IMAGE:  {source_image}", file=sys.stderr)
        print(f"DEST_IMAGE:    {dest_image}", file=sys.stderr)
        print(f"TASK_BUNDLE:   {task_bundle_ref}", file=sys.stderr)
        print("", file=sys.stderr)

        pipeline_path = get_pipeline_definition_path()
        pipeline_spec = load_and_patch_pipeline(pipeline_path, task_bundle_ref)

        manifest = build_pipelinerun_manifest(
            source_image=source_image,
            dest_image=dest_image,
            pipeline_spec=pipeline_spec,
            app=self._config["app"],
            service_account=self._config["service_account"],
        )

        manifest_yaml = yaml.dump(manifest, default_flow_style=False)
        return self.kube.create_pipelinerun(manifest_yaml)
