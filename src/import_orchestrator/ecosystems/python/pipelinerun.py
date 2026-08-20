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

from typing import Any

from import_orchestrator.engine.errors import TriggerError

__all__ = ["TriggerError", "build_pipelinerun_manifest", "parse_ref"]


def parse_ref(ref: str) -> tuple[str, str]:
    """Split a ``package==version`` reference into its parts.

    Raises TriggerError if the reference is not in the expected form.
    """
    package, sep, version = ref.partition("==")
    if not sep or not package or not version:
        raise TriggerError(f"python reference must be in the form package==version: {ref}")
    return package, version


def build_pipelinerun_manifest(
    *,
    package: str,
    version: str,
    pipeline_spec: dict[str, Any],
    namespace: str,
    application: str,
    component: str,
    prefix: str,
    repo_base: str,
    image_repo_base: str,
    git_auth_secret: str,
    service_account: str | None = None,
) -> dict[str, Any]:
    """Build a python-remediated-build PipelineRun manifest for one package/version.

    ``git_auth_secret`` names the secret bound to the ``git-auth`` workspace,
    which the clone task uses to authenticate against the lightwell-builds repo.

    When ``service_account`` is None, no ``taskRunTemplate`` is emitted and the
    cluster's default service account applies.
    """
    # Push to the Konflux component repository (<tenant>/<application>/<component>),
    # which is the only repo the build service account can push to. The package and
    # version are encoded in the tag rather than a per-package repo.
    image = f"{image_repo_base}/{application}/{component}:{package}-{version}"
    spec: dict[str, Any] = {
        "pipelineSpec": pipeline_spec,
        "params": [
            {"name": "PACKAGE", "value": package},
            {"name": "VERSION", "value": version},
            {"name": "LIGHTWELL_BUILDS_REPO_URL", "value": f"{repo_base}/pypi.org-{package}"},
            {"name": "LIGHTWELL_BUILDS_TAG", "value": f"{package}/{version}"},
            {"name": "IMAGE", "value": image},
            {"name": "ociStorage", "value": f"{image}.src"},
        ],
        "workspaces": [
            {"name": "git-auth", "secret": {"secretName": git_auth_secret}},
        ],
    }
    if service_account:
        spec["taskRunTemplate"] = {"serviceAccountName": service_account}

    return {
        "apiVersion": "tekton.dev/v1",
        "kind": "PipelineRun",
        "metadata": {
            "generateName": prefix,
            "namespace": namespace,
            "labels": {
                "appstudio.openshift.io/application": application,
                "appstudio.openshift.io/component": component,
                "pipelines.appstudio.openshift.io/type": "build",
                # Package identity, so builds can be queried/filtered by package.
                "lightwell.redhat.com/package": package,
                "lightwell.redhat.com/version": version,
            },
        },
        "spec": spec,
    }
