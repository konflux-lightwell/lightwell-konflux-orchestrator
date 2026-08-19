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
import sys

from import_orchestrator.clients import KubeClient
from import_orchestrator.constants import CLUSTER_API, KUBEARCHIVE_API
from import_orchestrator.engine.errors import TriggerError


def run_trigger(args: argparse.Namespace, ref: str) -> int:
    """Trigger a single PipelineRun for ``ref`` on the ecosystem's cluster.

    Generic across ecosystems: the ecosystem builds the manifest and supplies
    its own target namespace; this only submits it and reports the result.
    """
    try:
        eco = args.ecosystem
        kube = KubeClient(eco.namespace, CLUSTER_API, KUBEARCHIVE_API)
        manifest = eco.build_pipelinerun(ref, args)
        pr_name = kube.create_pipelinerun(manifest)

        if pr_name:
            print(f"pipelinerun.tekton.dev/{pr_name} created")
            return 0

        print("ERROR: PipelineRun may have been created but its name could not be parsed", file=sys.stderr)
        return 1

    except TriggerError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
