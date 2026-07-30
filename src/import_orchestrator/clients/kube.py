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

import sys
from typing import Literal

import requests

from import_orchestrator.clients.kube_api import KubeAPI, KubeAuth, resolve_auth
from import_orchestrator.models import PipelineRunStatus


class KubeClient:
    """Client for Kubernetes API operations against a cluster.

    Authenticates using either a KUBECONFIG file or the KONFLUX_TOKEN environment variable.
    """

    def __init__(self, namespace: str, cluster_api: str, kubearchive_api: str = ""):
        self.namespace = namespace
        self.cluster_api = cluster_api
        auth = resolve_auth(cluster_api)
        self._api = KubeAPI(auth)
        self._ka_api: KubeAPI | None = None
        if kubearchive_api:
            ka_auth = KubeAuth(server=kubearchive_api, token=auth.token, ca_cert=auth.ca_cert)
            self._ka_api = KubeAPI(ka_auth)

    def get_running_pipelineruns(self) -> list[PipelineRunStatus]:
        """Get all PipelineRuns with status 'Unknown' (i.e. still running)."""
        try:
            result = self._api.list(
                f"/apis/tekton.dev/v1/namespaces/{self.namespace}/pipelineruns",
            )
            pipelineruns = []
            for item in result.get("items", []):
                name = item.get("metadata", {}).get("name", "")
                conditions = item.get("status", {}).get("conditions", [])
                status = conditions[0].get("status", "") if conditions else ""
                if pr_status := PipelineRunStatus.from_str(name, status):
                    if pr_status.is_running:
                        pipelineruns.append(pr_status)
            return pipelineruns
        except requests.RequestException as e:
            print(f"ERROR: Failed to get PipelineRuns: {e}", file=sys.stderr)
            return []

    def get_pipelinerun_status(self, name: str) -> PipelineRunStatus | None:
        """Get the status of a specific PipelineRun by name.

        Checks the live cluster first, then falls back to the KubeArchive API.
        Returns None if not found in either place.
        """
        try:
            data = self._api.get(
                f"/apis/tekton.dev/v1/namespaces/{self.namespace}/pipelineruns/{name}",
            )
            conditions = data.get("status", {}).get("conditions", [])
            status = conditions[0].get("status", "") if conditions else ""
            if pr_status := PipelineRunStatus.from_str(name, status):
                return pr_status
        except requests.RequestException:
            pass

        if self._ka_api is not None:
            try:
                data = self._ka_api.get(
                    f"/apis/tekton.dev/v1/namespaces/{self.namespace}/pipelineruns/{name}",
                )
                for cond in data.get("status", {}).get("conditions", []):
                    if cond.get("type") == "Succeeded":
                        status = cond.get("status", "")
                        if pr_status := PipelineRunStatus.from_str(name, status):
                            return pr_status
            except requests.RequestException:
                pass

        return None

    def count_running_imports(self) -> int:
        """Count running PipelineRuns whose names start with 'pnc-import-'."""
        running_prs = self.get_running_pipelineruns()
        return sum(1 for pr in running_prs if pr.name.startswith("pnc-import-"))

    def find_snapshot_by_pipelinerun(self, pr_name: str) -> str | None:
        """Find the Snapshot created by a specific PipelineRun via its label."""
        try:
            result = self._api.list(
                f"/apis/appstudio.redhat.com/v1alpha1/namespaces/{self.namespace}/snapshots",
                labelSelector=f"appstudio.openshift.io/build-pipelinerun={pr_name}",
            )
            items = result.get("items", [])
            return items[0]["metadata"]["name"] if items else None
        except (requests.RequestException, KeyError, IndexError):
            return None

    def find_release_plan_for_snapshot(self, snapshot_name: str) -> str | None:
        """Find the ReleasePlan whose spec.application matches the snapshot's application label."""
        try:
            snap = self._api.get(
                f"/apis/appstudio.redhat.com/v1alpha1/namespaces/{self.namespace}/snapshots/{snapshot_name}",
            )
            application = snap.get("metadata", {}).get("labels", {}).get("appstudio.openshift.io/application", "")
            if not application:
                return None

            plans = self._api.list(
                f"/apis/appstudio.redhat.com/v1alpha1/namespaces/{self.namespace}/releaseplans",
            )
            for item in plans.get("items", []):
                if item.get("spec", {}).get("application") == application:
                    return item["metadata"]["name"]
            return None
        except (requests.RequestException, KeyError):
            return None

    def create_release(self, snapshot_name: str, release_plan: str) -> str | None:
        """Create a Release for the given snapshot and return its name."""
        manifest = {
            "apiVersion": "appstudio.redhat.com/v1alpha1",
            "kind": "Release",
            "metadata": {
                "generateName": "pnc-import-",
                "namespace": self.namespace,
            },
            "spec": {
                "releasePlan": release_plan,
                "snapshot": snapshot_name,
            },
        }
        try:
            result = self._api.create(
                f"/apis/appstudio.redhat.com/v1alpha1/namespaces/{self.namespace}/releases",
                manifest,
            )
            return result["metadata"]["name"]
        except (requests.RequestException, KeyError) as e:
            print(f"ERROR: Failed to create release for {snapshot_name}: {e}", file=sys.stderr)
            return None

    def create_pipelinerun(self, manifest: dict) -> str | None:
        """Create a PipelineRun from a manifest dict and return its generated name."""
        try:
            result = self._api.create(
                f"/apis/tekton.dev/v1/namespaces/{self.namespace}/pipelineruns",
                manifest,
            )
            return result["metadata"]["name"]
        except (requests.RequestException, KeyError) as e:
            print(f"ERROR: Failed to create PipelineRun: {e}", file=sys.stderr)
            return None

    def find_release_for_snapshot(self, snapshot_name: str) -> str | None:
        """Find an active (non-terminally-failed) Release for the given snapshot."""
        try:
            result = self._api.list(
                f"/apis/appstudio.redhat.com/v1alpha1/namespaces/{self.namespace}/releases",
            )
            for item in result.get("items", []):
                if item.get("spec", {}).get("snapshot") != snapshot_name:
                    continue
                released = next(
                    (c for c in item.get("status", {}).get("conditions", []) if c.get("type") == "Released"),
                    None,
                )
                # Skip terminally failed releases so a new one gets created
                if released and released.get("status") == "False" and released.get("reason") != "Progressing":
                    continue
                return item["metadata"]["name"]
            return None
        except (requests.RequestException, KeyError):
            return None

    def get_release_status(self, release_name: str) -> Literal["True", "False", "Unknown"] | None:
        """Get the effective status of the 'Released' condition.

        Returns "True" on success, "False" on terminal failure, "Unknown" while still progressing,
        and None if the Release object itself cannot be fetched.

        The Released condition starts as False/Progressing while in flight, so we only treat
        False as a failure when the reason is not "Progressing".
        """
        try:
            data = self._api.get(
                f"/apis/appstudio.redhat.com/v1alpha1/namespaces/{self.namespace}/releases/{release_name}",
            )
            released = next(
                (c for c in data.get("status", {}).get("conditions", []) if c.get("type") == "Released"),
                None,
            )
            if released is None:
                return "Unknown"
            status = released.get("status", "")
            reason = released.get("reason", "")
            if status == "True":
                return "True"
            if status == "False" and reason != "Progressing":
                return "False"
            return "Unknown"
        except requests.RequestException:
            return None
