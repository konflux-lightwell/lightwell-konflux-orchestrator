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

from import_orchestrator.ecosystems.base import DEFAULT_PIPELINERUN_ANNOTATIONS
from import_orchestrator.ecosystems.javascript import config
from import_orchestrator.engine.errors import TriggerError

__all__ = [
    "TriggerError",
    "build_pipelinerun_manifest",
    "parse_ref",
    "sanitize_package_for_resource",
]


def parse_ref(ref: str) -> tuple[str, str]:
    """Split a ``package==version`` reference into its parts.

    Raises TriggerError if the reference is not in the expected form.
    """
    package, sep, version = ref.partition("==")
    if not sep or not package or not version:
        raise TriggerError(f"javascript reference must be in the form package==version: {ref}")
    return package, version


def sanitize_package_for_resource(package: str) -> str:
    """Encode an npm package name for OCI tags and Kubernetes label values.

    Scoped names like ``@babel/core`` are invalid as Docker tags (may not start
    with ``@`` or contain ``/``) and as Kubernetes label values. Strip leading
    ``@`` and replace ``/`` with ``-`` so ``@babel/core`` becomes ``babel-core``.
    Unscoped names are unchanged.
    """
    return package.lstrip("@").replace("/", "-")


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
    source_git_url: str,
    source_revision: str,
    builder_image: str,
    task_build_npm_package_bundle: str,
    task_assess_npm_compliance_bundle: str | None = None,
    tl_npm_registry_url: str = config.DEFAULT_TL_NPM_REGISTRY_URL,
    sbom_repository_url: str = "",
    pulp_read_secret_name: str = config.DEFAULT_PULP_READ_SECRET_NAME,
    git_auth_secret: str = config.GIT_AUTH_SECRET,
    publish_version_suffix: str | None = None,
    service_account: str | None = None,
    include_assess: bool = True,
) -> dict[str, Any]:
    """Build a javascript factory PipelineRun for one package/version.

    The recipe lives under ``packages/<name>/<version>/`` in the source monorepo
    (scoped names keep ``@scope/name`` in the path). Output OCI tag and the
    ``lightwell.redhat.com/package`` label use a sanitized package slug.

    ``git_auth_secret`` names the secret bound to the ``git-auth`` workspace,
    which the clone task uses to authenticate against the recipe monorepo.

    ``publish_version_suffix`` makes the recipes publish ``<version>-<suffix>``
    instead of ``version``; when unset they publish ``version`` unchanged.

    When ``include_assess`` is False (remediated thin builds), assess-related
    params are omitted and the runtime label is ``build-npm``.

    ``sbom_repository_url`` is the CycloneDX purl ``repository_url`` qualifier.
    It is required for every target, including thin remediations.
    """
    package_path = config.package_dir(package, version)
    package_slug = sanitize_package_for_resource(package)
    image = f"{image_repo_base}/{application}/{component}:{package_slug}-{version}"
    publish_version_override = f"{version}-{publish_version_suffix}" if publish_version_suffix else ""
    if not sbom_repository_url:
        raise TriggerError("sbom_repository_url is required")
    params: list[dict[str, Any]] = [
        {"name": "git-url", "value": source_git_url},
        {"name": "revision", "value": source_revision},
        {"name": "output-image", "value": image},
        {"name": "packages", "value": [package_path]},
        {"name": "builder-image", "value": builder_image},
        {"name": "task-build-npm-package-bundle", "value": task_build_npm_package_bundle},
        {"name": "lightwell-publish-version-override", "value": publish_version_override},
        {"name": "sbom-repository-url", "value": sbom_repository_url},
    ]
    if include_assess:
        if not task_assess_npm_compliance_bundle:
            raise TriggerError("task_assess_npm_compliance_bundle is required when include_assess is True")
        params.extend(
            [
                {"name": "task-assess-npm-compliance-bundle", "value": task_assess_npm_compliance_bundle},
                {"name": "tl-npm-registry-url", "value": tl_npm_registry_url},
                {"name": "pulp-read-secret-name", "value": pulp_read_secret_name},
            ]
        )

    spec: dict[str, Any] = {
        "pipelineSpec": pipeline_spec,
        "params": params,
        "workspaces": [
            {"name": "git-auth", "secret": {"secretName": git_auth_secret}},
        ],
    }
    if service_account:
        spec["taskRunTemplate"] = {"serviceAccountName": service_account}

    runtime = "build-npm-compliance" if include_assess else "build-npm"
    return {
        "apiVersion": "tekton.dev/v1",
        "kind": "PipelineRun",
        "metadata": {
            "generateName": prefix,
            "namespace": namespace,
            "annotations": DEFAULT_PIPELINERUN_ANNOTATIONS,
            "labels": {
                "appstudio.openshift.io/application": application,
                "appstudio.openshift.io/component": component,
                "pipelines.appstudio.openshift.io/type": "build",
                "pipelines.openshift.io/runtime": runtime,
                "lightwell.redhat.com/package": package_slug,
                "lightwell.redhat.com/version": version,
            },
        },
        "spec": spec,
    }
