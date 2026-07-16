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

# Name of the K8s Secret containing the cosign public key (cosign.pub) used
# to verify PNC artifacts. Must exist in NAMESPACE before imports can run.
VERIFICATION_PUBLIC_KEY_SECRET = "verification-public-key"

# Artifact-type-specific configuration.
ARTIFACT_CONFIGS: dict[str, dict[str, str]] = {
    "REBUILD": {
        "app": "pnc-import",
        "service_account": "build-pipeline-pnc-import",
        "source_repo": "quay.io/light-castle/rebuild-pnc",
        "dest_repo": "quay.io/redhat-user-workloads/lightwell-poc-tenant/pnc-import/pnc-import",
    },
    "REMEDIATED": {
        "app": "pnc-import-remediated",
        "service_account": "build-pipeline-pnc-import-remediated",
        "source_repo": "quay.io/light-castle/secure-pnc",
        "dest_repo": "quay.io/redhat-user-workloads/lightwell-poc-tenant/pnc-import-remediated/pnc-import-remediated",
    },
    "STAGE": {
        "app": "pnc-import-stage",
        "service_account": "build-pipeline-pnc-import-stage",
        "source_repo": "quay.io/light-castle/rebuild-pnc",
        "dest_repo": "quay.io/redhat-user-workloads/lightwell-poc-tenant/pnc-import-stage/pnc-import-stage",
    },
}
