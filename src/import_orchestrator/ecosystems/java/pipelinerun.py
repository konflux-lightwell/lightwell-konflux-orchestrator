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

import re
from typing import Any

from import_orchestrator.constants import NAMESPACE
from import_orchestrator.engine.errors import TriggerError
from import_orchestrator.engine.pipeline_loader import load_pipeline
from import_orchestrator.pipelinerun import (
    IMPORT_IDENTITY_ANNOTATION,
    build_execution_spec_fingerprint,
    build_import_identity,
    build_pipelinerun_name,
)

__all__ = [
    "TriggerError",
    "build_pipelinerun_manifest",
    "digest_pin_image",
    "extract_tag_from_image",
    "load_pipeline",
]


def digest_pin_image(source_image: str) -> str:
    if "@sha256:" in source_image:
        return source_image
    raise TriggerError(f"image reference must be digest-pinned (contain @sha256:): {source_image}")


def extract_tag_from_image(source_image: str) -> str:
    match = re.search(r":([^@]+)@", source_image)
    if match:
        return match.group(1)
    digest_match = re.search(r"@sha256:([a-f0-9]+)", source_image)
    if digest_match:
        return digest_match.group(1)
    raise TriggerError(f"could not extract tag from {source_image}")


def build_pipelinerun_manifest(
    *,
    source_image: str,
    dest_image: str,
    pipeline_spec: dict[str, Any],
    app: str,
    service_account: str,
    prefix: str,
    verification_secret: str,
    namespace: str = NAMESPACE,
    attempt: int = 0,
) -> dict[str, Any]:
    spec = {
        "taskRunTemplate": {"serviceAccountName": service_account},
        "pipelineSpec": pipeline_spec,
        "params": [
            {"name": "SOURCE_IMAGE", "value": source_image},
            {"name": "IMAGE", "value": dest_image},
            {"name": "VERIFICATION_PUBLIC_KEY_SECRET", "value": verification_secret},
        ],
    }
    execution_spec_fingerprint = build_execution_spec_fingerprint(
        {
            "spec": spec,
            "application": app,
        }
    )
    identity = build_import_identity(
        "java",
        source_image=source_image,
        destination_image=dest_image,
        attempt=attempt,
        execution_spec_fingerprint=execution_spec_fingerprint,
    )
    return {
        "apiVersion": "tekton.dev/v1",
        "kind": "PipelineRun",
        "metadata": {
            "name": build_pipelinerun_name(prefix, identity),
            "namespace": namespace,
            "annotations": {
                "test.appstudio.openshift.io/ignore-supersession": "true",
                IMPORT_IDENTITY_ANNOTATION: identity,
            },
            "labels": {
                "appstudio.openshift.io/application": app,
                "appstudio.openshift.io/component": app,
                "pipelines.appstudio.openshift.io/type": "build",
            },
        },
        "spec": spec,
    }
