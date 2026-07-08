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

# ---------------------------------------------------------------------------
# Default configuration constants for the import orchestrator.
# ---------------------------------------------------------------------------

NAMESPACE = "lightwell-poc-tenant"
CLUSTER_API = "https://api.stone-prod-p01.wcfb.p1.openshiftapps.com:6443"
DEFAULT_DB_PATH = "./pnc_import_state.db"
DEFAULT_MAX_PARALLEL = 1
DEFAULT_POLL_INTERVAL = 30  # seconds
DEFAULT_MAX_RETRIES = 3
RELEASE_PLAN = "pnc-import-java-pulp-validated-prod"

# ---------------------------------------------------------------------------
# Tekton pipeline configuration
# ---------------------------------------------------------------------------

# oci-verify-import task bundle: floating tag resolved to digest at runtime.
TASK_BUNDLE_BASE = "quay.io/konflux-ci/tekton-catalog/task-oci-verify-import"
TASK_BUNDLE_FLOATING_TAG = "0.1"

# Digest-pinned bundle references for catalog tasks embedded in the PipelineRun.
CATALOG_BUNDLE_REFS: dict[str, str] = {
    "clamav-scan": (
        "quay.io/konflux-ci/tekton-catalog/task-clamav-scan:0.3"
        "@sha256:567cb66bd2e1f4b58b9d4d756f3317fc62479e0b40aa0de66094b1f12d296cfc"
    ),
    "sast-shell-check-oci-ta": (
        "quay.io/konflux-ci/tekton-catalog/task-sast-shell-check-oci-ta:0.1"
        "@sha256:fc685d6f7dfb7c9ab2f2db38bbe2c8d383407847350ccd8b96352322c487b13c"
    ),
    "sast-unicode-check-oci-ta": (
        "quay.io/konflux-ci/tekton-catalog/task-sast-unicode-check-oci-ta:0.4"
        "@sha256:5807ffe3a0cca5cf970076bbc7a404642cc6e3eebe64e9e5e6a4f20da740bf73"
    ),
}

# Artifact-type-specific configuration.
ARTIFACT_CONFIGS: dict[str, dict[str, str]] = {
    "REBUILD": {
        "app": "pnc-import",
        "service_account": "build-pipeline-pnc-import",
        "dest_repo": "quay.io/redhat-user-workloads/lightwell-poc-tenant/pnc-import/pnc-import",
    },
    "REMEDIATED": {
        "app": "pnc-import-remediated",
        "service_account": "build-pipeline-pnc-import-remediated",
        "dest_repo": ("quay.io/redhat-user-workloads/lightwell-poc-tenant/pnc-import-remediated/pnc-import-remediated"),
    },
}
