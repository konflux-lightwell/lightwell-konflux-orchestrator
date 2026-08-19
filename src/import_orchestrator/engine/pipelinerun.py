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

from import_orchestrator.clients.kube import KubeClient
from import_orchestrator.ecosystems.java import config

# Temporary shim: the PipelineRun helpers moved to the Java ecosystem.
# Removed once the engine/commands route through Ecosystem.build_pipelinerun (Task 7/8).
from import_orchestrator.ecosystems.java.pipelinerun import (  # noqa: F401
    TriggerError,
    build_pipelinerun_manifest,
    digest_pin_image,
    extract_tag_from_image,
    load_pipeline,
)

# Alias retained for the test shim until Task 8.
get_pipeline_definition_path = config.pipeline_definition_path


class PipelineRunBuilder:
    """Temporary shim retained until ImportTrigger routes through the ecosystem (Task 7)."""

    def __init__(self, kube: KubeClient, artifact_type: str = "REBUILD"):
        self.kube = kube
        self._config = config.ARTIFACT_CONFIGS[artifact_type]

    def trigger(self, source_image: str, tag: str | None = None) -> str | None:
        source_image = digest_pin_image(source_image)
        tag = tag or extract_tag_from_image(source_image)
        dest_image = f"{self._config['dest_repo']}:{tag}"
        pipeline_spec = load_pipeline(get_pipeline_definition_path())
        manifest = build_pipelinerun_manifest(
            source_image=source_image,
            dest_image=dest_image,
            pipeline_spec=pipeline_spec,
            app=self._config["app"],
            service_account=self._config["service_account"],
            prefix=config.PIPELINERUN_PREFIX,
            verification_secret=config.VERIFICATION_PUBLIC_KEY_SECRET,
        )
        pr_name = self.kube.create_pipelinerun(manifest)
        if pr_name is None:
            raise TriggerError("PipelineRun creation failed (API returned no name)")
        return pr_name
