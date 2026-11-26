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
REMEDIATED_PIPELINERUN_PREFIX = "javascript-remediated-build-"

# Assess npm dependency-closure compliance against Lightwell validated Pulp.
TL_NPM_REGISTRY_URL_PROD = "https://packages.redhat.com/api/pulp-content/lightwell/javascript/validated"
TL_NPM_REGISTRY_URL_STAGE = "https://packages.redhat.com/api/pulp-content/lightwell-stage/javascript/validated"
# pulp-config distribution javascript/remediated on the lightwell-stage domain.
SBOM_REPOSITORY_URL_REMEDIATED_STAGE = (
    "https://packages.redhat.com/api/pulp-content/lightwell-stage/javascript/remediated"
)

# Pipeline YAML relative to tekton/ (or TEKTON_PIPELINE_DIR).
VALIDATED_PIPELINE_SUBPATH = Path("pipelines") / "javascript-validated-build" / "javascript-validated-build.yaml"
REMEDIATED_PIPELINE_SUBPATH = Path("pipelines") / "javascript-remediated-build" / "javascript-remediated-build.yaml"

# Build targets, keyed by target name. Selected via ``--target``.
# Component/SA names match the provisioned Konflux resources on stone-prod-p01.
TARGET_CONFIGS: dict[str, dict] = {
    "SCRATCH": {
        "app": "javascript-validated-stage",
        "component": "javascript-validated-stage-build",
        "service_account": "build-pipeline-javascript-validated-stage-build",
        "tl_npm_registry_url": TL_NPM_REGISTRY_URL_STAGE,
        "skip_release": True,
        "allow_publish_version_suffix": True,
        "pipeline_subpath": VALIDATED_PIPELINE_SUBPATH,
        "pipelinerun_prefix": PIPELINERUN_PREFIX,
        "include_assess": True,
    },
    "VALIDATED_STAGE": {
        "app": "javascript-validated-stage",
        "component": "javascript-validated-stage-build",
        "service_account": "build-pipeline-javascript-validated-stage-build",
        "tl_npm_registry_url": TL_NPM_REGISTRY_URL_STAGE,
        "pipeline_subpath": VALIDATED_PIPELINE_SUBPATH,
        "pipelinerun_prefix": PIPELINERUN_PREFIX,
        "include_assess": True,
    },
    "VALIDATED": {
        "app": "javascript-validated",
        "component": "javascript-validated-build",
        "service_account": "build-pipeline-javascript-validated-build",
        "tl_npm_registry_url": TL_NPM_REGISTRY_URL_PROD,
        "pipeline_subpath": VALIDATED_PIPELINE_SUBPATH,
        "pipelinerun_prefix": PIPELINERUN_PREFIX,
        "include_assess": True,
    },
    "REMEDIATED_STAGE": {
        "app": "javascript-remediated-stage",
        "component": "javascript-remediated-stage-build",
        "service_account": "build-pipeline-javascript-remediated-stage-build",
        # Thin remediations: no assess against a TL registry.
        "pipeline_subpath": REMEDIATED_PIPELINE_SUBPATH,
        "pipelinerun_prefix": REMEDIATED_PIPELINERUN_PREFIX,
        "include_assess": False,
        "sbom_repository_url": SBOM_REPOSITORY_URL_REMEDIATED_STAGE,
    },
}
# No SCRATCH target until a dedicated scratch Application/Component/
# ImageRepository (and no releasing ReleasePlan) exists, matching
# python-sdist SCRATCH. Reusing javascript-validated would still push
# Snapshots to the validated Quay path and risk auto-release.
DEFAULT_TARGET = "VALIDATED"

IMAGE_REPO_BASE = "quay.io/redhat-user-workloads/lightwell-javascript-tenant"

# Recipe monorepo under Lightwell build-configs (calunga-javascript).
DEFAULT_SOURCE_GIT_URL = "https://gitlab.cee.redhat.com/lightwell/lightwell-builds/build-configs/calunga-javascript"
DEFAULT_SOURCE_REVISION = "main"

# Secret bound to the git-auth workspace. The clone task uses it to authenticate
# against gitlab.cee.redhat.com (same secret name as the python ecosystem).
GIT_AUTH_SECRET = "lightwell-builds-git-auth"

# Pinned digests from the calunga npm PoC (reuse until lightwell npm-builder lands).
# TODO(cdx-sbom): After the npm-builder PR that generates the CycloneDX referrer
# merges and Konflux publishes the image + task-build-npm-package bundle, replace
# both DEFAULT_BUILDER_IMAGE and DEFAULT_TASK_BUILD_NPM_PACKAGE_BUNDLE below.
# The pipelines already pass SBOM_REPOSITORY_URL; the current task pin does not
# declare that param, so PipelineRuns fail validation until the digests move.
DEFAULT_BUILDER_IMAGE = (
    "quay.io/light-factory/npm-builder:"
    "6a93271ad6350d6bbebd99f18da3abf6957db002@"
    "sha256:5ccbec169ed5516d264979c4d56d8dc8f034989106b0e890dde964a0ed0be6c4"
)
DEFAULT_TASK_BUILD_NPM_PACKAGE_BUNDLE = (
    "quay.io/konflux-ci/tekton-catalog/task-build-npm-package:"
    "0.1-1793720581@"
    "sha256:57411609b4731f79af966ade016492cb44baceef4ca0e23a6a28d95b2188a6b7"
)
DEFAULT_TASK_ASSESS_NPM_COMPLIANCE_BUNDLE = (
    "quay.io/konflux-ci/tekton-catalog/task-assess-npm-compliance:"
    "0.1-1793012461@"
    "sha256:08786871676181177e136089f6420a11309971364e81b4c49dd7db193227d6a2"
)

# Default assess registry matches DEFAULT_TARGET (VALIDATED / prod). Prefer the
# per-target ``tl_npm_registry_url`` in TARGET_CONFIGS; CLI/env can still override.
DEFAULT_TL_NPM_REGISTRY_URL = TL_NPM_REGISTRY_URL_PROD


def resolve_sbom_repository_url(target: str, explicit: str | None = None) -> str:
    """Return the CycloneDX ``repository_url`` for ``target``.

    Validated targets use their npm content URL. REMEDIATED_STAGE uses the
    lightwell-stage ``javascript/remediated`` distribution. An explicit value
    overrides either.

    Args:
        target: Key in ``TARGET_CONFIGS``.
        explicit: Caller override. Empty means unset.

    Raises:
        ValueError: ``target`` is unknown, or it has no URL and ``explicit`` is empty.
    """
    if explicit:
        return explicit
    cfg = TARGET_CONFIGS.get(target)
    if cfg is None:
        raise ValueError(f"unknown javascript target {target}")
    configured = cfg.get("sbom_repository_url") or cfg.get("tl_npm_registry_url") or ""
    if configured:
        return str(configured)
    raise ValueError(
        f"target {target} has no SBOM repository_url; "
        "pass --sbom-repository-url or set LIGHTWELL_JAVASCRIPT_SBOM_REPOSITORY_URL"
    )


# Optional basic-auth Secret for gated TL registry reads (assess task mount).
DEFAULT_PULP_READ_SECRET_NAME = "lightwell-pulp-read-secret"


def pipeline_definition_path(target: str | None = None) -> Path:
    """Return the path to the pipeline YAML for ``target`` (default VALIDATED).

    Configurable via TEKTON_PIPELINE_DIR; otherwise falls back to the repo layout.
    """
    cfg = TARGET_CONFIGS.get(target or DEFAULT_TARGET, TARGET_CONFIGS[DEFAULT_TARGET])
    pipeline_subpath = cfg.get("pipeline_subpath", VALIDATED_PIPELINE_SUBPATH)
    if pipeline_dir := os.environ.get("TEKTON_PIPELINE_DIR"):
        return Path(pipeline_dir) / pipeline_subpath
    project_root = Path(__file__).resolve().parents[4]
    return project_root / "tekton" / pipeline_subpath


def package_dir(package: str, version: str) -> str:
    """Return the recipe path ``packages/<name>/<version>`` for a package ref."""
    return f"packages/{package}/{version}"
