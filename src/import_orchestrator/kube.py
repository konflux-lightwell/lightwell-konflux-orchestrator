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

import os
import subprocess
import sys

from import_orchestrator.models import PipelineRunStatus


class KubeClient:
    """Wrapper for kubectl operations against a Kubernetes cluster.

    Authenticates using either a KUBECONFIG file or the KONFLUX_TOKEN environment variable.
    """

    def __init__(self, namespace: str, cluster_api: str):
        self.namespace = namespace
        self.cluster_api = cluster_api
        self._kubectl_base_args = self._build_kubectl_args()

    def _build_kubectl_args(self) -> list[str]:
        """Build base kubectl arguments from KUBECONFIG or KONFLUX_TOKEN."""
        args = ["-n", self.namespace]
        if token := os.getenv("KONFLUX_TOKEN"):
            args.extend(["--token", token, "--server", self.cluster_api])
        return args

    def get_running_pipelineruns(self) -> list[PipelineRunStatus]:
        """Get all PipelineRuns with status 'Unknown' (i.e. still running)."""
        try:
            result = subprocess.run(
                [
                    "kubectl",
                    *self._kubectl_base_args,
                    "get",
                    "pr",
                    "-o",
                    "jsonpath={range .items[*]}{.metadata.name}{'\\t'}{.status.conditions[0].status}{'\\n'}{end}",
                ],
                capture_output=True,
                check=True,
                text=True,
            )

            pipelineruns = []
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) == 2:
                    name, status = parts
                    if status in ("True", "False", "Unknown"):
                        pr = PipelineRunStatus(name=name, status=status)  # type: ignore
                        if pr.is_running:
                            pipelineruns.append(pr)

            return pipelineruns

        except subprocess.CalledProcessError as e:
            print(
                f"ERROR: Failed to get PipelineRuns: {e.stderr}",
                file=sys.stderr,
            )
            return []

    def get_pipelinerun_status(self, name: str) -> PipelineRunStatus | None:
        """Get the status of a specific PipelineRun by name.

        Returns None if the PipelineRun is not found or its status is unrecognized.
        """
        try:
            result = subprocess.run(
                [
                    "kubectl",
                    *self._kubectl_base_args,
                    "get",
                    "pr",
                    name,
                    "-o",
                    "jsonpath={.status.conditions[0].status}",
                ],
                capture_output=True,
                check=True,
                text=True,
            )

            status = result.stdout.strip()
            if status in ("True", "False", "Unknown"):
                return PipelineRunStatus(name=name, status=status)  # type: ignore

            return None

        except subprocess.CalledProcessError:
            return None

    def count_running_imports(self) -> int:
        """Count running PipelineRuns whose names start with 'pnc-import-'."""
        running_prs = self.get_running_pipelineruns()
        return sum(1 for pr in running_prs if pr.name.startswith("pnc-import-"))
