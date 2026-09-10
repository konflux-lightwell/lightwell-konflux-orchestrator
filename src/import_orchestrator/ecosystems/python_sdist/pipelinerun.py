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
    image_repo_base: str,
    service_account: str | None = None,
    source_registries: str = "rhtl,pypi.org",
    pipeline_git_url: str | None = None,
    pipeline_revision: str | None = None,
) -> dict[str, Any]:
    """Build a python-sdist-ingest PipelineRun manifest for one package/version.

    ``pipeline_git_url`` and ``pipeline_revision`` identify the repository and
    immutable commit that supplied this inline pipeline definition to Chains.
    """
    if bool(pipeline_git_url) != bool(pipeline_revision):
        raise TriggerError("inline pipeline Git URL and revision must be supplied together")

    image = f"{image_repo_base}/{application}/{component}:{package}-{version}"
    params = [
        {"name": "PACKAGE", "value": package},
        {"name": "VERSION", "value": version},
        {"name": "IMAGE", "value": image},
        {"name": "SOURCE_REGISTRIES", "value": source_registries},
        {"name": "ociStorage", "value": f"{image}.src"},
    ]
    if pipeline_git_url and pipeline_revision:
        params.extend(
            [
                {"name": "git-url", "value": pipeline_git_url},
                {"name": "revision", "value": pipeline_revision},
            ]
        )

    spec: dict[str, Any] = {
        "pipelineSpec": pipeline_spec,
        "params": params,
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
                "lightwell.redhat.com/package": package,
                "lightwell.redhat.com/version": version,
            },
        },
        "spec": spec,
    }
