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
import os
import sys

from import_orchestrator.clients import KubeClient
from import_orchestrator.constants import CLUSTER_API, KUBEARCHIVE_API, NAMESPACE
from import_orchestrator.ecosystems.base import Ecosystem
from import_orchestrator.ecosystems.java import config
from import_orchestrator.engine.errors import TriggerError


def register(subparsers: argparse._SubParsersAction, ecosystem: Ecosystem) -> None:
    """Register the 'trigger' subcommand with the given subparsers."""
    parser = subparsers.add_parser(
        "trigger",
        help="Trigger a PNC import PipelineRun",
        description=(
            "Trigger a PNC import PipelineRun on the Konflux cluster. "
            "Patches the pipeline definition "
            "and submits the PipelineRun via the K8s API."
        ),
    )

    parser.add_argument(
        "source_image",
        help="OCI image reference to import (must be digest-pinned with @sha256:)",
    )
    parser.add_argument(
        "tag",
        nargs="?",
        default=None,
        help="Destination tag override (default: derived from source image)",
    )
    parser.add_argument(
        "--artifact-type",
        choices=list(config.ARTIFACT_CONFIGS),
        default=os.environ.get("LIGHTWELL_ARTIFACT_TYPE", "STAGE"),
        help="Artifact type (default: STAGE, or LIGHTWELL_ARTIFACT_TYPE env var)",
    )

    parser.set_defaults(func=run, ecosystem=ecosystem)


def run(args: argparse.Namespace) -> int:
    """Execute the trigger subcommand."""
    try:
        kube = KubeClient(NAMESPACE, CLUSTER_API, KUBEARCHIVE_API)
        eco = args.ecosystem
        manifest = eco.build_pipelinerun(args.source_image, args)
        pr_name = kube.create_pipelinerun(manifest)

        if pr_name:
            print(f"pipelinerun.tekton.dev/{pr_name} created")
            return 0

        print("ERROR: PipelineRun may have been created but its name could not be parsed", file=sys.stderr)
        return 1

    except TriggerError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
