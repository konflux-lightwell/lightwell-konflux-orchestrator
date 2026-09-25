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
KUBEARCHIVE_API = "https://kubearchive-api-server-product-kubearchive.apps.stone-prod-p01.wcfb.p1.openshiftapps.com"

DEFAULT_MAX_PARALLEL = 1
DEFAULT_POLL_INTERVAL = 30  # seconds
DEFAULT_MAX_RETRIES = 3

# ---------------------------------------------------------------------------
# konflux-release-data (GitOps source of truth for ReleasePlan / RPA).
# Used by `orchestrate --print-resources` to resolve the release chain offline.
# ---------------------------------------------------------------------------

# Cloned here (relative to the working directory) when not supplied explicitly.
RELEASE_DATA_REPO_URL = "git@gitlab.cee.redhat.com:releng/konflux-release-data.git"
RELEASE_DATA_DEFAULT_PATH = "reference/konflux-release-data"
