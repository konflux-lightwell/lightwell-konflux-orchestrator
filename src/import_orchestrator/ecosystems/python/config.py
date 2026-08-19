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

PYTHON_DEFAULT_DB_PATH = "./python_import_state.db"
NAMESPACE = "lightwell-python-tenant"
PIPELINERUN_PREFIX = "python-remediated-build-"

# Konflux application/component and service account for the build pipeline.
# TODO: confirm these against the cluster once the python tenant is provisioned.
APPLICATION = "python-remediated-build"
SERVICE_ACCOUNT = "build-pipeline-python-remediated-build"

# Base of the per-package lightwell-builds source repositories. Each package
# lives at "<repo_base>/pypi.org-<package>".
LIGHTWELL_BUILDS_REPO_BASE = "https://gitlab.cee.redhat.com/lightwell/lightwell-builds"

# Base of the destination image repository. Built wheels are pushed to
# "<image_repo_base>/<package>:<version>".
IMAGE_REPO_BASE = "quay.io/redhat-user-workloads/lightwell-python-tenant"


def pipeline_definition_path() -> Path:
    """Return the path to the python-remediated-build pipeline definition.

    Configurable via TEKTON_PIPELINE_DIR; otherwise falls back to the repo layout.
    """
    if pipeline_dir := os.environ.get("TEKTON_PIPELINE_DIR"):
        return Path(pipeline_dir) / "pipelines" / "python-remediated-build" / "python-remediated-build.yaml"
    # config.py -> python/ -> ecosystems/ -> import_orchestrator/ -> src/ -> project root
    project_root = Path(__file__).resolve().parents[4]
    return project_root / "tekton" / "pipelines" / "python-remediated-build" / "python-remediated-build.yaml"
