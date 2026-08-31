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
from urllib3.exceptions import ConnectTimeoutError as Urllib3ConnectTimeoutError
from urllib3.exceptions import ReadTimeoutError as Urllib3ReadTimeoutError

from import_orchestrator.clients.kube_api import KubeAPI, KubeAuth, resolve_auth
from import_orchestrator.models import PipelineRunStatus
from import_orchestrator.pipelinerun import IMPORT_IDENTITY_ANNOTATION


def _api_error_detail(exc: requests.RequestException) -> str:
    """Extract a human-readable message from a failed Kubernetes API response.

    Kubernetes returns a Status object whose ``message`` field explains why a
    request was rejected (e.g. pipeline validation errors). Surface it so a 400
    is actionable instead of an opaque "Bad Request".
    """
    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)
    try:
        body = response.json()
    except (ValueError, AttributeError):
        body = None
    if isinstance(body, dict) and body.get("message"):
        return f"{exc}: {body['message']}"
    text = (getattr(response, "text", "") or "").strip()
    return f"{exc}: {text}" if text else str(exc)


def _http_status(exc: requests.RequestException) -> int | None:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return status_code if isinstance(status_code, int) else None


def _is_ambiguous_timeout(exc: requests.RequestException) -> bool:
    timeout_types = (
        requests.ConnectTimeout,
        requests.ReadTimeout,
        Urllib3ConnectTimeoutError,
        Urllib3ReadTimeoutError,
    )
    causes = (getattr(exc, "__cause__", None), getattr(exc, "__context__", None))
    return isinstance(exc, timeout_types) or any(isinstance(cause, timeout_types) for cause in causes)


def _manifest_identity(manifest: dict) -> tuple[str, str]:
    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("PipelineRun manifest metadata is missing")
    name = metadata.get("name")
    annotations = metadata.get("annotations")
    identity = annotations.get(IMPORT_IDENTITY_ANNOTATION) if isinstance(annotations, dict) else None
    if not isinstance(name, str) or not name or not isinstance(identity, str) or not identity:
        raise ValueError("PipelineRun manifest must contain metadata.name and import identity")
    return name, identity


def _has_matching_identity(existing: dict, identity: str) -> bool:
    metadata = existing.get("metadata")
    annotations = metadata.get("annotations") if isinstance(metadata, dict) else None
    return isinstance(annotations, dict) and annotations.get(IMPORT_IDENTITY_ANNOTATION) == identity


def _get_existing_pipelinerun(api: KubeAPI, api_path: str) -> dict | None:
    try:
        return api.get(api_path)
    except requests.RequestException as e:
        if _http_status(e) == 404:
            return None
        raise


def _reuse_existing_pipelinerun(name: str, existing: dict, identity: str) -> str:
    if _has_matching_identity(existing, identity):
        return name
    raise ValueError(f"PipelineRun {name} exists but has a different import identity")


def _reconcile_pipelinerun_conflict(
    api: KubeAPI,
    api_path: str,
    name: str,
    identity: str,
    conflict: requests.RequestException,
) -> str:
    from import_orchestrator.engine.errors import PipelineRunReconciliationError, PipelineRunRetryableError

    try:
        existing = api.get(api_path)
    except requests.RequestException as reconciliation_error:
        if _http_status(reconciliation_error) == 404:
            raise PipelineRunRetryableError(
                f"PipelineRun {name} conflict resolved with confirmed absence"
            ) from conflict
        raise PipelineRunReconciliationError(
            name,
            f"PipelineRun {name} creation conflicted and reconciliation failed: "
            f"{_api_error_detail(reconciliation_error)}; original conflict: {_api_error_detail(conflict)}",
        ) from reconciliation_error
    if _has_matching_identity(existing, identity):
        return name
    raise PipelineRunReconciliationError(
        name,
        f"PipelineRun {name} conflict resolved to a different import identity",
    ) from conflict


def _reconcile_ambiguous_create(
    api: KubeAPI,
    api_path: str,
    name: str,
    identity: str,
    timeout: requests.RequestException,
) -> str:
    from import_orchestrator.engine.errors import PipelineRunReconciliationError, PipelineRunRetryableError

    try:
        existing = api.get(api_path)
    except requests.RequestException as reconciliation_error:
        if _http_status(reconciliation_error) == 404:
            raise PipelineRunRetryableError(f"PipelineRun {name} timeout resolved with confirmed absence") from timeout
        raise PipelineRunReconciliationError(
            name,
            f"PipelineRun {name} creation timed out and reconciliation failed: "
            f"{_api_error_detail(reconciliation_error)}; original timeout: {_api_error_detail(timeout)}",
        ) from reconciliation_error
    if _has_matching_identity(existing, identity):
        return name
    raise PipelineRunReconciliationError(
        name,
        f"PipelineRun {name} timeout resolved to a different import identity",
    ) from timeout


def _validated_manifest_identity(manifest: dict) -> tuple[str, str]:
    from import_orchestrator.engine.errors import TriggerError

    try:
        return _manifest_identity(manifest)
    except ValueError as e:
        raise TriggerError(str(e)) from e


def _find_owned_pipelinerun(api: KubeAPI, api_path: str, name: str, identity: str) -> str | None:
    from import_orchestrator.engine.errors import PipelineRunReconciliationError

    try:
        existing = _get_existing_pipelinerun(api, api_path)
    except requests.RequestException as e:
        raise PipelineRunReconciliationError(
            name,
            f"failed to check PipelineRun {name}: {_api_error_detail(e)}",
        ) from e
    if existing is None:
        return None
    try:
        return _reuse_existing_pipelinerun(name, existing, identity)
    except ValueError as e:
        raise PipelineRunReconciliationError(name, str(e)) from e


def _create_or_reconcile_pipelinerun(
    api: KubeAPI,
    collection_path: str,
    api_path: str,
    manifest: dict,
    name: str,
    identity: str,
) -> str | None:
    from import_orchestrator.engine.errors import TriggerError

    try:
        result = api.create(collection_path, manifest)
    except requests.RequestException as e:
        if _is_ambiguous_timeout(e):
            try:
                return _reconcile_ambiguous_create(api, api_path, name, identity, e)
            except requests.RequestException as reconciliation_error:
                raise TriggerError(f"failed to create PipelineRun: {_api_error_detail(e)}") from reconciliation_error
        if _http_status(e) != 409:
            raise TriggerError(f"failed to create PipelineRun: {_api_error_detail(e)}") from e
        return _reconcile_pipelinerun_conflict(api, api_path, name, identity, e)
    try:
        return result["metadata"]["name"]
    except KeyError:
        return None


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

    def count_running_imports(self, prefix: str) -> int:
        """Count running PipelineRuns whose names start with the given prefix."""
        running_prs = self.get_running_pipelineruns()
        return sum(1 for pr in running_prs if pr.name.startswith(prefix))

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

    def create_release(self, snapshot_name: str, release_plan: str, prefix: str) -> str | None:
        """Create a Release for the given snapshot and return its name."""
        manifest = {
            "apiVersion": "appstudio.redhat.com/v1alpha1",
            "kind": "Release",
            "metadata": {
                "generateName": prefix,
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
        """Create or reuse an owned PipelineRun from a manifest dict.

        A deterministic name and immutable import identity are required so an
        existing object can be safely distinguished from another import.
        """
        name, identity = _validated_manifest_identity(manifest)

        api_path = f"/apis/tekton.dev/v1/namespaces/{self.namespace}/pipelineruns/{name}"
        collection_path = f"/apis/tekton.dev/v1/namespaces/{self.namespace}/pipelineruns"
        existing = _find_owned_pipelinerun(self._api, api_path, name, identity)
        if existing is not None:
            return existing
        return _create_or_reconcile_pipelinerun(self._api, collection_path, api_path, manifest, name, identity)

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
