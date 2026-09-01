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

from import_orchestrator.ecosystems.python import config
from import_orchestrator.ecosystems.python.pipelinerun import build_pipelinerun_manifest, parse_ref
from import_orchestrator.engine.pipeline_loader import load_pipeline


class PythonEcosystem:
    """Python ecosystem: CVE-remediated wheel builds via the python-remediated-build pipeline."""

    name = "python"
    default_db_path = config.PYTHON_DEFAULT_DB_PATH
    pipelinerun_prefix = config.PIPELINERUN_PREFIX
    namespace = config.NAMESPACE

    def build_pipelinerun(self, ref: str, args: argparse.Namespace, *, attempt: int = 0) -> dict:
        package, version = parse_ref(ref)
        target = getattr(args, "target", config.DEFAULT_TARGET)
        cfg = config.TARGET_CONFIGS[target]
        pipeline_spec = load_pipeline(config.pipeline_definition_path())
        return build_pipelinerun_manifest(
            package=package,
            version=version,
            pipeline_spec=pipeline_spec,
            namespace=self.namespace,
            application=cfg["app"],
            component=cfg["component"],
            service_account=config.SERVICE_ACCOUNT,
            prefix=self.pipelinerun_prefix,
            repo_base=config.LIGHTWELL_BUILDS_REPO_BASE,
            image_repo_base=config.IMAGE_REPO_BASE,
            git_auth_secret=config.GIT_AUTH_SECRET,
            attempt=attempt,
        )

    def register_cli(self, subparsers: argparse._SubParsersAction) -> None:
        from import_orchestrator.commands import import_file
        from import_orchestrator.ecosystems.python.commands import orchestrate, trigger

        eco_parser = subparsers.add_parser("python", help="Python CVE-remediated wheel builds")
        eco_sub = eco_parser.add_subparsers(dest="command")
        import_file.register(eco_sub, self)
        orchestrate.register(eco_sub, self)
        trigger.register(eco_sub, self)
        eco_parser.set_defaults(_ecosystem_parser=eco_parser)
