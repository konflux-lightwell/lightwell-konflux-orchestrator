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

import json
import os
import re
import subprocess
import sys
from typing import Literal

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

    def find_snapshot_by_pipelinerun(self, pr_name: str) -> str | None:
        """Find the Snapshot created by a specific PipelineRun via its label."""
        try:
            result = subprocess.run(
                ["kubectl", *self._kubectl_base_args, "get", "snapshots",
                 "-l", f"appstudio.openshift.io/build-pipelinerun={pr_name}",
                 "-o", "jsonpath={.items[0].metadata.name}"],
                capture_output=True, check=True, text=True,
            )
            name = result.stdout.strip()
            return name if name else None
        except subprocess.CalledProcessError:
            return None

    def get_snapshot_auto_release_status(self, snapshot_name: str) -> str | None:
        """Check if snapshot was auto-released and return the newer snapshot name, or None.

        Returns the name of the newer snapshot from the AutoReleased condition message,
        or None if not auto-released.
        """
        try:
            result = subprocess.run(
                ["kubectl", *self._kubectl_base_args, "get", "snapshot", snapshot_name,
                 "-o", "jsonpath={.status.conditions[?(@.type==\"AutoReleased\")].status}{'\\t'}"
                       "{.status.conditions[?(@.type==\"AutoReleased\")].message}"],
                capture_output=True, check=True, text=True,
            )
            parts = result.stdout.strip().split("\t")
            if parts[0] != "True":
                return None
            # Message is e.g. "Released in newer Snapshot pnc-import-20260630-014147-000"
            msg = parts[1] if len(parts) > 1 else ""
            words = msg.split()
            return words[-1] if words else ""
        except subprocess.CalledProcessError:
            return None

    def find_release_plan_for_snapshot(self, snapshot_name: str) -> str | None:
        """Find the ReleasePlan whose spec.application matches the snapshot's application label."""
        try:
            snap = subprocess.run(
                ["kubectl", *self._kubectl_base_args, "get", "snapshot", snapshot_name,
                 "-o", "jsonpath={.metadata.labels.appstudio\\.openshift\\.io/application}"],
                capture_output=True, check=True, text=True,
            )
            application = snap.stdout.strip()
            if not application:
                return None

            plans = subprocess.run(
                ["kubectl", *self._kubectl_base_args, "get", "releaseplans", "-o", "json"],
                capture_output=True, check=True, text=True,
            )
            data = json.loads(plans.stdout)
            for item in data.get("items", []):
                if item.get("spec", {}).get("application") == application:
                    return item["metadata"]["name"]
            return None
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
            return None

    def create_release(self, snapshot_name: str, release_plan: str) -> str | None:
        """Create a Release for the given snapshot and return its name."""
        manifest = (
            f"apiVersion: appstudio.redhat.com/v1alpha1\n"
            f"kind: Release\n"
            f"metadata:\n"
            f"  generateName: pnc-import-\n"
            f"  namespace: {self.namespace}\n"
            f"spec:\n"
            f"  releasePlan: {release_plan}\n"
            f"  snapshot: {snapshot_name}\n"
        )
        try:
            result = subprocess.run(
                ["kubectl", *self._kubectl_base_args, "create", "-f", "-"],
                input=manifest, capture_output=True, check=True, text=True,
            )
            match = re.search(r"release\.appstudio\.redhat\.com/(\S+)\s+created", result.stdout + result.stderr)
            return match.group(1) if match else None
        except subprocess.CalledProcessError as e:
            print(f"ERROR: Failed to create release for {snapshot_name}: {e.stderr}", file=sys.stderr)
            return None

    def find_release_for_snapshot(self, snapshot_name: str) -> str | None:
        """Find a Release whose spec.snapshot matches snapshot_name."""
        try:
            result = subprocess.run(
                ["kubectl", *self._kubectl_base_args, "get", "releases", "-o", "json"],
                capture_output=True, check=True, text=True,
            )
            data = json.loads(result.stdout)
            for item in data.get("items", []):
                if item.get("spec", {}).get("snapshot") == snapshot_name:
                    return item["metadata"]["name"]
            return None
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
            return None

    def get_release_status(self, release_name: str) -> Literal["True", "False", "Unknown"] | None:
        """Get the effective status of the 'Released' condition.

        Returns "True" on success, "False" on terminal failure, "Unknown" while still progressing,
        and None if the Release object itself cannot be fetched.

        The Released condition starts as False/Progressing while in flight, so we only treat
        False as a failure when the reason is not "Progressing".
        """
        try:
            result = subprocess.run(
                ["kubectl", *self._kubectl_base_args, "get", "release", release_name,
                 "-o", "jsonpath={.status.conditions[?(@.type==\"Released\")].status}{'\\t'}"
                       "{.status.conditions[?(@.type==\"Released\")].reason}"],
                capture_output=True, check=True, text=True,
            )
            parts = result.stdout.strip().split("\t")
            status = parts[0] if parts else ""
            reason = parts[1] if len(parts) > 1 else ""
            if status == "True":
                return "True"
            if status == "False" and reason != "Progressing":
                return "False"
            return "Unknown"
        except subprocess.CalledProcessError:
            return None
