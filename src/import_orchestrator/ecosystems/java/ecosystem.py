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

import argparse

from import_orchestrator.ecosystems.java import config
from import_orchestrator.ecosystems.java.pipelinerun import (
    build_pipelinerun_manifest,
    digest_pin_image,
    extract_tag_from_image,
    load_pipeline,
)


class JavaEcosystem:
    """Java (PNC) ecosystem: OCI image imports via the pnc-import pipeline."""

    name = "java"
    default_db_path = config.JAVA_DEFAULT_DB_PATH
    pipelinerun_prefix = config.PIPELINERUN_PREFIX

    def build_pipelinerun(self, ref: str, args: argparse.Namespace) -> dict:
        artifact_type = getattr(args, "artifact_type", "STAGE")
        cfg = config.ARTIFACT_CONFIGS[artifact_type]

        source_image = digest_pin_image(ref)
        tag = getattr(args, "tag", None) or extract_tag_from_image(source_image)
        dest_image = f"{cfg['dest_repo']}:{tag}"

        pipeline_spec = load_pipeline(config.pipeline_definition_path())
        return build_pipelinerun_manifest(
            source_image=source_image,
            dest_image=dest_image,
            pipeline_spec=pipeline_spec,
            app=cfg["app"],
            service_account=cfg["service_account"],
            prefix=self.pipelinerun_prefix,
            verification_secret=config.VERIFICATION_PUBLIC_KEY_SECRET,
        )

    def register_cli(self, subparsers: argparse._SubParsersAction) -> None:
        raise NotImplementedError  # finalized in Task 8
