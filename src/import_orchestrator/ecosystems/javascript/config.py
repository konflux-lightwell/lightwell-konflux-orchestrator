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

import os
from pathlib import Path

JAVASCRIPT_DEFAULT_DB_PATH = "./javascript_import_state.db"
NAMESPACE = "lightwell-javascript-tenant"
PIPELINERUN_PREFIX = "javascript-validated-build-"

# Build targets, keyed by target name. Selected via ``--target``.
# Component/SA names match the provisioned Konflux resources on stone-prod-p01
# (``javascript-validated-build``). Align with a future rename to
# ``javascript-validated`` when the KRD component is renamed.
TARGET_CONFIGS: dict[str, dict] = {
    "VALIDATED": {
        "app": "javascript-validated",
        "component": "javascript-validated-build",
        "service_account": "build-pipeline-javascript-validated-build",
    },
}
# No SCRATCH target until a dedicated scratch Application/Component/
# ImageRepository (and no releasing ReleasePlan) exists, matching
# python-sdist SCRATCH. Reusing javascript-validated would still push
# Snapshots to the validated Quay path and risk auto-release.
DEFAULT_TARGET = "VALIDATED"

IMAGE_REPO_BASE = "quay.io/redhat-user-workloads/lightwell-javascript-tenant"

# Interim recipe monorepo (per-package lightwell-builds repos come later).
DEFAULT_SOURCE_GIT_URL = "https://github.com/calungaproject/npm-registry"
DEFAULT_SOURCE_REVISION = "main"

# Pinned digests from the calunga npm PoC (reuse until lightwell npm-builder lands).
DEFAULT_BUILDER_IMAGE = (
    "quay.io/redhat-user-workloads/calunga-tenant/npm-builder:"
    "4223587b7123d8c7d5a4b2ff8b5253b8076a1c06@"
    "sha256:8705cb985e00a236673a384cbdf7a0deaeeca3d359e611fdb8bc23b321ae5755"
)
DEFAULT_TASK_BUILD_NPM_PACKAGE_BUNDLE = (
    "quay.io/redhat-user-workloads/calunga-tenant/task-build-npm-package:"
    "8efe12cc3fe2cd0825888ec5a198cc55871320cb@"
    "sha256:89d5c491ef5efe3aaebae9182a190a9eeb5352fd6b549d1855ad38c6ca549cf7"
)
DEFAULT_TASK_ASSESS_NPM_COMPLIANCE_BUNDLE = (
    "quay.io/redhat-user-workloads/calunga-tenant/task-promote-npm-oci:"
    "4223587b7123d8c7d5a4b2ff8b5253b8076a1c06@"
    "sha256:73e9b4cc00094e24f024bdb7e68cc2d346edefffad9e27a931c018fa964df682"
)


def pipeline_definition_path() -> Path:
    """Return the path to the javascript-validated-build pipeline definition.

    Configurable via TEKTON_PIPELINE_DIR; otherwise falls back to the repo layout.
    """
    pipeline_subpath = Path("pipelines") / "javascript-validated-build" / "javascript-validated-build.yaml"
    if pipeline_dir := os.environ.get("TEKTON_PIPELINE_DIR"):
        return Path(pipeline_dir) / pipeline_subpath
    project_root = Path(__file__).resolve().parents[4]
    return project_root / "tekton" / pipeline_subpath


def package_dir(package: str, version: str) -> str:
    """Return the recipe path ``packages/<name>/<version>`` for a package ref."""
    return f"packages/{package}/{version}"
