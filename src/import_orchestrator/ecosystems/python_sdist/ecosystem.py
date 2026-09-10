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

from import_orchestrator.ecosystems.python_sdist import config
from import_orchestrator.ecosystems.python_sdist.pipelinerun import build_pipelinerun_manifest, parse_ref
from import_orchestrator.engine.pipeline_loader import load_pipeline


class PythonSdistEcosystem:
    """Python sdist ecosystem: Ingests upstream source distributions via python-sdist-ingest."""

    name = "python-sdist"
    default_db_path = config.PYTHON_SDIST_DEFAULT_DB_PATH
    pipelinerun_prefix = config.PIPELINERUN_PREFIX
    namespace = config.NAMESPACE

    def build_pipelinerun(self, ref: str, args: argparse.Namespace) -> dict:
        package, version = parse_ref(ref)
        source_registries = getattr(args, "source_registries", "rhtl,pypi.org")
        pipeline_spec = load_pipeline(config.pipeline_definition_path())
        pipeline_git_url, pipeline_revision = config.pipeline_source_identity()
        return build_pipelinerun_manifest(
            package=package,
            version=version,
            pipeline_spec=pipeline_spec,
            namespace=self.namespace,
            application=config.APPLICATION,
            component=config.COMPONENT,
            service_account=config.SERVICE_ACCOUNT,
            prefix=self.pipelinerun_prefix,
            image_repo_base=config.IMAGE_REPO_BASE,
            source_registries=source_registries,
            pipeline_git_url=pipeline_git_url,
            pipeline_revision=pipeline_revision,
        )

    def register_cli(self, subparsers: argparse._SubParsersAction) -> None:
        from import_orchestrator.commands import import_file
        from import_orchestrator.ecosystems.python_sdist.commands import orchestrate, trigger

        eco_parser = subparsers.add_parser("python-sdist", help="Python source distribution ingestion (Phase 1)")
        eco_sub = eco_parser.add_subparsers(dest="command")
        import_file.register(eco_sub, self)
        orchestrate.register(eco_sub, self)
        trigger.register(eco_sub, self)
        eco_parser.set_defaults(_ecosystem_parser=eco_parser)
