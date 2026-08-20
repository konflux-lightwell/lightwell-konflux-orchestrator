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

JAVA_DEFAULT_DB_PATH = "./java_import_state.db"
RELEASE_PLAN = "pnc-import-java-pulp-validated-prod"
VERIFICATION_PUBLIC_KEY_SECRET = "verification-public-key"
PIPELINERUN_PREFIX = "pnc-import-"

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


def pipeline_definition_path() -> Path:
    """Return the path to the pnc-import pipeline definition.

    Configurable via TEKTON_PIPELINE_DIR; otherwise falls back to the repo layout.
    """
    if pipeline_dir := os.environ.get("TEKTON_PIPELINE_DIR"):
        return Path(pipeline_dir) / "pipelines" / "pnc-import" / "pnc-import.yaml"
    # config.py -> java/ -> ecosystems/ -> import_orchestrator/ -> src/ -> project root
    project_root = Path(__file__).resolve().parents[4]
    return project_root / "tekton" / "pipelines" / "pnc-import" / "pnc-import.yaml"
