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

import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from import_orchestrator.clients.kube import KubeClient
from import_orchestrator.constants import (
    ARTIFACT_CONFIGS,
    NAMESPACE,
    VERIFICATION_PUBLIC_KEY_SECRET,
)


class TriggerError(Exception):
    """Non-recoverable error during the PipelineRun trigger process."""


# ---------------------------------------------------------------------------
# Image validation
# ---------------------------------------------------------------------------


def digest_pin_image(source_image: str) -> str:
    """Validate that *source_image* is digest-pinned (contains ``@sha256:``).

    Raises:
        TriggerError: If the image reference is not digest-pinned.
    """
    if "@sha256:" in source_image:
        return source_image

    raise TriggerError(f"image reference must be digest-pinned (contain @sha256:): {source_image}")


def extract_tag_from_image(source_image: str) -> str:
    """Extract the destination tag from a digest-pinned OCI reference.

    From ``repo:tag@sha256:...`` returns ``tag``.
    From ``repo@sha256:<hex>`` returns the full digest hex (without ``sha256:``
    prefix) so the caller can construct a valid push destination.

    Raises:
        TriggerError: If no digest can be parsed from the reference.
    """
    match = re.search(r":([^@]+)@", source_image)
    if match:
        return match.group(1)
    digest_match = re.search(r"@sha256:([a-f0-9]+)", source_image)
    if digest_match:
        return digest_match.group(1)
    raise TriggerError(f"could not extract tag from {source_image}")


# ---------------------------------------------------------------------------
# Pipeline loading
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


def load_pipeline(pipeline_path: Path) -> dict[str, Any]:
    """Load the pipeline YAML and return its spec.

    All task bundle refs are expected to be pre-pinned in the pipeline YAML.

    Returns:
        The ``spec`` dict from the Pipeline resource.

    Raises:
        TriggerError: If the pipeline file cannot be found or parsed.
    """
    if not pipeline_path.exists():
        raise TriggerError(f"pipeline definition not found: {pipeline_path}")

    try:
        with open(pipeline_path) as f:
            pipeline = yaml.safe_load(f)

        return pipeline["spec"]
    except (yaml.YAMLError, KeyError, TypeError) as e:
        raise TriggerError(f"failed to load pipeline from {pipeline_path}: {e}") from e


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
                {"name": "VERIFICATION_PUBLIC_KEY_SECRET", "value": VERIFICATION_PUBLIC_KEY_SECRET},
            ],
        },
    }


# ---------------------------------------------------------------------------
# Orchestrator class
# ---------------------------------------------------------------------------


class PipelineRunBuilder:
    """Builds and submits PNC import PipelineRuns to a Konflux cluster.

    Orchestrates the full workflow: validate the source image is
    digest-pinned, load the pipeline definition, build the PipelineRun
    manifest, and submit it via the K8s API.
    """

    def __init__(self, kube: KubeClient, artifact_type: str = "REBUILD"):
        self.kube = kube
        self._config = ARTIFACT_CONFIGS[artifact_type]

    def trigger(self, source_image: str, tag: str | None = None) -> str | None:
        """Execute the full trigger workflow.

        Args:
            source_image: Digest-pinned OCI image reference (must contain @sha256:).
            tag: Optional destination tag override; derived from source_image if omitted.

        Returns:
            The generated PipelineRun name, or None if it could not be parsed.

        Raises:
            TriggerError: If any step in the workflow fails.
        """
        source_image = digest_pin_image(source_image)
        tag = tag or extract_tag_from_image(source_image)

        dest_image = f"{self._config['dest_repo']}:{tag}"

        print(f"SOURCE_IMAGE:  {source_image}", file=sys.stderr)
        print(f"DEST_IMAGE:    {dest_image}", file=sys.stderr)
        print("", file=sys.stderr)

        pipeline_path = get_pipeline_definition_path()
        pipeline_spec = load_pipeline(pipeline_path)

        manifest = build_pipelinerun_manifest(
            source_image=source_image,
            dest_image=dest_image,
            pipeline_spec=pipeline_spec,
            app=self._config["app"],
            service_account=self._config["service_account"],
        )

        return self.kube.create_pipelinerun(manifest)
