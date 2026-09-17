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
from import_orchestrator.models import (
    PipelineRunStatus,
    ReleaseLookup,
    ReleaseLookupState,
    SnapshotLookup,
    SnapshotLookupState,
)


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


class KubeClient:
    """Client for Kubernetes API operations against a cluster.

    KubeArchive returns Kubernetes ``List`` envelopes even for a named GET
    (the kubectl-ka client exposes the same shape).  Callers must therefore
    normalize named responses before reading ``metadata`` or ``spec``.

    Authenticates using either a KUBECONFIG file or the KONFLUX_TOKEN environment variable.
    """

    # Diagnostics captured on failure are bounded so a runaway build log cannot
    # bloat the ImportItem's error_message (and any Jira comment derived from it).
    _MAX_LOG_LINES = 20
    _MAX_DETAIL_CHARS = 4000

    def __init__(self, namespace: str, cluster_api: str, kubearchive_api: str = ""):
        self.namespace = namespace
        self.cluster_api = cluster_api
        auth = resolve_auth(cluster_api)
        self._api = KubeAPI(auth)
        self._ka_api: KubeAPI | None = None
        if kubearchive_api:
            ka_auth = KubeAuth(server=kubearchive_api, token=auth.token, ca_cert=auth.ca_cert)
            self._ka_api = KubeAPI(ka_auth)

    @staticmethod
    def _items(result: object) -> list[dict]:
        """Extract resources from Kubernetes and KubeArchive list envelopes.

        KubeArchive's named GET/list responses are ``v1 List`` objects whose
        ``items`` can themselves be List objects.  The normal Kubernetes API
        returns the resource directly, so flatten only that documented wrapper.
        """
        if not isinstance(result, dict):
            return []
        items = result.get("items")
        if not isinstance(items, list):
            return []
        flattened: list[dict] = []
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("items"), list) and "metadata" not in item:
                flattened.extend(KubeClient._items(item))
            elif isinstance(item, dict):
                flattened.append(item)
        return flattened

    @classmethod
    def _resource(cls, result: object) -> dict | None:
        """Return a resource from either a direct response or List envelope."""
        if isinstance(result, dict) and "metadata" in result:
            return result
        items = cls._items(result)
        return items[0] if len(items) == 1 else None

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

    def get_pipelinerun_failure_detail(self, name: str) -> str | None:
        """Best-effort human-readable diagnostics for a failed PipelineRun.

        Walks the PipelineRun's ``Succeeded`` condition and each failed child
        TaskRun (via ``status.childReferences``) to capture the failure reason,
        the failing step's termination info, and a tail of that step's
        container logs. Each source is optional: whatever can be gathered is
        returned, degrading to ``None`` only when no PipelineRun data is
        available at all. Callers (e.g. balor-fianna) surface this as the
        ImportItem's ``error_message`` instead of the opaque "PipelineRun
        failed".
        """
        pr = self._fetch_resource(
            f"/apis/tekton.dev/v1/namespaces/{self.namespace}/pipelineruns/{name}",
        )
        if pr is None:
            return None

        parts: list[str] = []

        cond = self._succeeded_condition(pr)
        if cond is not None:
            header = " - ".join(p for p in (cond.get("reason", ""), cond.get("message", "")) if p)
            if header:
                parts.append(f"PipelineRun {name}: {header}")

        for child in pr.get("status", {}).get("childReferences", []):
            if child.get("kind") != "TaskRun":
                continue
            tr_name = child.get("name")
            if not tr_name:
                continue
            detail = self._taskrun_failure_detail(tr_name)
            if detail:
                parts.append(detail)

        if not parts:
            return None
        return "\n".join(parts)[: self._MAX_DETAIL_CHARS]

    def _taskrun_failure_detail(self, tr_name: str) -> str | None:
        """Return failure diagnostics for a single TaskRun, or None if it did not fail."""
        tr = self._fetch_resource(
            f"/apis/tekton.dev/v1/namespaces/{self.namespace}/taskruns/{tr_name}",
        )
        if tr is None:
            return None

        cond = self._succeeded_condition(tr)
        if cond is None or cond.get("status") != "False":
            return None

        header = " - ".join(p for p in (cond.get("reason", ""), cond.get("message", "")) if p)
        lines = [f"TaskRun {tr_name}: {header}" if header else f"TaskRun {tr_name}: failed"]

        status = tr.get("status", {})
        pod_name = status.get("podName", "")
        for step in status.get("steps", []):
            term = step.get("terminated")
            if not term or term.get("reason") == "Completed":
                continue
            step_name = step.get("name", "")
            exit_code = term.get("exitCode")
            step_line = f"  step '{step_name}' terminated: reason={term.get('reason')} exitCode={exit_code}"
            if term.get("message"):
                step_line += f" message={term['message']}"
            lines.append(step_line)

            container = step.get("container") or (f"step-{step_name}" if step_name else "")
            if pod_name and container:
                log_tail = self._pod_log_tail(pod_name, container)
                if log_tail:
                    lines.append(f"  logs ({container}):\n{log_tail}")

        return "\n".join(lines)

    def _pod_log_tail(self, pod_name: str, container: str) -> str | None:
        """Best-effort tail of a container's logs; None if unavailable."""
        try:
            text = self._api.get_text(
                f"/api/v1/namespaces/{self.namespace}/pods/{pod_name}/log",
                container=container,
                tailLines=self._MAX_LOG_LINES,
            )
        except requests.RequestException:
            return None
        stripped = text.strip()
        return stripped or None

    def _fetch_resource(self, api_path: str) -> dict | None:
        """GET a resource from the live cluster, falling back to KubeArchive."""
        try:
            return self._api.get(api_path)
        except requests.RequestException:
            pass
        if self._ka_api is not None:
            try:
                return self._ka_api.get(api_path)
            except requests.RequestException:
                pass
        return None

    @staticmethod
    def _succeeded_condition(resource: dict) -> dict | None:
        """Return the ``Succeeded`` condition, falling back to the first condition."""
        conditions = resource.get("status", {}).get("conditions", [])
        for cond in conditions:
            if cond.get("type") == "Succeeded":
                return cond
        return conditions[0] if conditions else None

    def count_running_imports(self, prefix: str) -> int:
        """Count running PipelineRuns whose names start with the given prefix."""
        running_prs = self.get_running_pipelineruns()
        return sum(1 for pr in running_prs if pr.name.startswith(prefix))

    def find_snapshot_by_component_digest(self, digest: str, application: str | None = None) -> SnapshotLookup:
        """Find a namespaced Snapshot containing exactly the canonical component digest.

        Snapshot names and tags are deliberately ignored: only a lower-case canonical
        sha256 digest in ``spec.components`` can establish content identity. Live API
        results are preferred and KubeArchive is used when the live object is absent.
        """
        import re

        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            return SnapshotLookup(SnapshotLookupState.UNKNOWN)
        path = f"/apis/appstudio.redhat.com/v1alpha1/namespaces/{self.namespace}/snapshots"
        # The live API is authoritative.  Archive is a fallback only when live
        # cannot provide a structurally valid collection response; in particular,
        # a valid live empty result must not be combined with archive results.
        for api in (self._api, self._ka_api):
            if api is None:
                continue
            try:
                result = api.list(path)
            except (requests.RequestException, KeyError, TypeError, AttributeError):
                continue
            if not isinstance(result, dict) or not isinstance(result.get("items"), list):
                continue
            matches: set[str] = set()
            for snapshot in self._items(result):
                metadata = snapshot.get("metadata", {})
                labels = metadata.get("labels", {}) if isinstance(metadata, dict) else {}
                if application and labels.get("appstudio.openshift.io/application") != application:
                    continue
                spec = snapshot.get("spec", {})
                components = spec.get("components", []) if isinstance(spec, dict) else []
                if not isinstance(components, list):
                    continue
                if any(
                    isinstance(component, dict)
                    and component.get("containerImage", component.get("image", "")).rsplit("@", 1)[-1] == digest
                    for component in components
                ):
                    name = metadata.get("name") if isinstance(metadata, dict) else None
                    if isinstance(name, str) and name:
                        matches.add(name)
            if len(matches) > 1:
                return SnapshotLookup(SnapshotLookupState.AMBIGUOUS)
            if len(matches) == 1:
                return SnapshotLookup(SnapshotLookupState.FOUND, next(iter(matches)))
            return SnapshotLookup(SnapshotLookupState.CONFIRMED_EMPTY)
        return SnapshotLookup(SnapshotLookupState.UNKNOWN)

    def find_snapshot_for_import(self, source_ref: str, application: str | None = None) -> SnapshotLookup:
        """Resolve a Java import through successful live or archived import PLRs."""
        import re

        digest = source_ref.rsplit("@", 1)[-1] if "@" in source_ref else ""
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            return SnapshotLookup(SnapshotLookupState.UNKNOWN)
        path = f"/apis/tekton.dev/v1/namespaces/{self.namespace}/pipelineruns"
        matches: dict[str, tuple[str, dict]] = {}
        saw_collection = False
        for api in (self._api, self._ka_api):
            if api is None:
                continue
            try:
                result = api.list(path)
            except (requests.RequestException, KeyError, TypeError, AttributeError):
                continue
            if not isinstance(result, dict) or not isinstance(result.get("items"), list):
                continue
            saw_collection = True
            for pr in self._items(result):
                metadata = pr.get("metadata") or {}
                labels = metadata.get("labels") or {}
                if application and labels.get("appstudio.openshift.io/application") != application:
                    continue
                if labels.get("pipelines.appstudio.io/type") not in (None, "build"):
                    continue
                spec = pr.get("spec") or {}
                params = spec.get("params") or (spec.get("pipelineSpec") or {}).get("params") or []
                values = {
                    str(p.get("value"))
                    for p in params
                    if isinstance(p, dict) and p.get("name", "").upper() in ("SOURCE_IMAGE", "SOURCE_ARTIFACT")
                }
                if source_ref not in values:
                    continue
                condition = self._succeeded_condition(pr)
                if not condition or condition.get("status") != "True":
                    continue
                name = str(metadata.get("name", ""))
                if not name:
                    continue
                completed = str(
                    condition.get("lastTransitionTime")
                    or (pr.get("status") or {}).get("completionTime")
                    or metadata.get("creationTimestamp")
                    or ""
                )
                if name not in matches or (completed, name) > (matches[name][0], name):
                    matches[name] = (completed, pr)
        if not matches:
            return (
                SnapshotLookup(SnapshotLookupState.CONFIRMED_EMPTY)
                if saw_collection
                else SnapshotLookup(SnapshotLookupState.UNKNOWN)
            )
        ordered = sorted(
            ((when, name, pr) for name, (when, pr) in matches.items()),
            key=lambda item: (item[0], item[1]),
            reverse=True,
        )
        for _when, pr_name, pr in ordered:
            snapshot = self._snapshot_for_import_pr(pr, pr_name)
            if snapshot is None:
                continue
            output_digests = self.get_snapshot_component_digests(snapshot)
            if output_digests is None:
                return SnapshotLookup(SnapshotLookupState.UNKNOWN)
            if digest in output_digests:
                return SnapshotLookup(SnapshotLookupState.FOUND, snapshot)
            return SnapshotLookup(SnapshotLookupState.UNKNOWN)
        return SnapshotLookup(SnapshotLookupState.CONFIRMED_EMPTY)

    def _snapshot_for_import_pr(self, pr: dict, pr_name: str) -> str | None:
        """Resolve a PLR Snapshot, preferring its explicit snapshot annotation."""
        annotations = (pr.get("metadata") or {}).get("annotations") or {}
        for key in ("appstudio.openshift.io/snapshot", "appstudio.openshift.io/snapshots"):
            for name in str(annotations.get(key, "")).split(","):
                name = name.strip()
                if name and self._snapshot_exists(name):
                    return name
        return self.find_snapshot_by_pipelinerun(pr_name)

    def _snapshot_exists(self, name: str) -> bool:
        path = f"/apis/appstudio.redhat.com/v1alpha1/namespaces/{self.namespace}/snapshots/{name}"
        for api in (self._api, self._ka_api):
            if api is None:
                continue
            try:
                data = api.get(path)
                resource = self._resource(data)
                return bool(resource and resource.get("metadata", {}).get("name") == name)
            except (requests.RequestException, KeyError, TypeError, AttributeError):
                continue
        return False

    def find_snapshot_by_pipelinerun(self, pr_name: str) -> str | None:
        """Find the Snapshot created by a specific PipelineRun via its label."""
        path = f"/apis/appstudio.redhat.com/v1alpha1/namespaces/{self.namespace}/snapshots"
        for api in (self._api, self._ka_api):
            if api is None:
                continue
            try:
                result = api.list(path, labelSelector=f"appstudio.openshift.io/build-pipelinerun={pr_name}")
                items = self._items(result)
                if items:
                    return items[0]["metadata"]["name"]
            except (requests.RequestException, KeyError, IndexError, TypeError, AttributeError):
                continue
        return None

    def get_snapshot_component_digests(self, snapshot_name: str) -> set[str] | None:
        """Return component image digests recorded by a Snapshot, or None if absent."""
        try:
            data = self._api.get(
                f"/apis/appstudio.redhat.com/v1alpha1/namespaces/{self.namespace}/snapshots/{snapshot_name}"
            )
            data = self._resource(data)
            if data is None:
                raise KeyError("snapshot resource missing")
            components = data.get("spec", {}).get("components", [])
            digests = set()
            for component in components:
                image = component.get("containerImage", component.get("image", ""))
                if "@sha256:" in image:
                    digests.add(image.split("@", 1)[1])
            return digests
        except (requests.RequestException, KeyError, TypeError):
            pass
        if self._ka_api is not None:
            try:
                data = self._ka_api.get(
                    f"/apis/appstudio.redhat.com/v1alpha1/namespaces/{self.namespace}/snapshots/{snapshot_name}"
                )
                data = self._resource(data)
                if data is None:
                    raise KeyError("snapshot resource missing")
                digests = set()
                for component in data.get("spec", {}).get("components", []):
                    image = component.get("containerImage", component.get("image", ""))
                    if "@sha256:" in image:
                        digests.add(image.split("@", 1)[1])
                return digests
            except (requests.RequestException, KeyError, TypeError):
                pass
        return None

    def find_release_plan_for_snapshot(self, snapshot_name: str) -> str | None:
        """Find the single auto-releasing ReleasePlan for the snapshot's application.

        Plans labelled ``auto-release: 'false'`` are operator-driven (e.g. a production
        promotion gated on ticket state) and are never selected automatically. Only an
        explicit 'false' excludes a plan; a missing label is not a signal either way.

        Returns None if no plan matches, and also if more than one does — an application
        with two auto-releasing plans is unresolvable from a Snapshot alone, and guessing
        would mean guessing where content gets published. Callers that know which plan
        they want should pass it explicitly rather than relying on this lookup.
        """
        try:
            snap = self._api.get(
                f"/apis/appstudio.redhat.com/v1alpha1/namespaces/{self.namespace}/snapshots/{snapshot_name}",
            )
            application = (snap.get("metadata", {}).get("labels") or {}).get("appstudio.openshift.io/application", "")
            if not application:
                return None

            plans = self._api.list(
                f"/apis/appstudio.redhat.com/v1alpha1/namespaces/{self.namespace}/releaseplans",
            )
            candidates = [
                item["metadata"]["name"]
                for item in plans.get("items", [])
                if item.get("spec", {}).get("application") == application
                and (item.get("metadata", {}).get("labels") or {}).get("release.appstudio.openshift.io/auto-release")
                != "false"
            ]

            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1:
                print(
                    f"ERROR: {len(candidates)} auto-releasing ReleasePlans match application "
                    f"'{application}': {', '.join(sorted(candidates))}. Refusing to guess.",
                    file=sys.stderr,
                )
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
        """Create a PipelineRun from a manifest dict and return its generated name.

        Raises TriggerError if the API rejects the request (e.g. an invalid
        pipeline definition), including the cluster's error message. Returns
        None only if the PipelineRun was created but its name is absent from
        the response.
        """
        try:
            result = self._api.create(
                f"/apis/tekton.dev/v1/namespaces/{self.namespace}/pipelineruns",
                manifest,
            )
        except requests.RequestException as e:
            # Imported lazily: engine.errors pulls in the engine package, which
            # imports clients, so a module-level import would be circular.
            from import_orchestrator.engine.errors import TriggerError

            raise TriggerError(f"failed to create PipelineRun: {_api_error_detail(e)}") from e

        try:
            return result["metadata"]["name"]
        except KeyError:
            return None

    def find_release_for_snapshot(self, snapshot_name: str) -> str | None:
        """Compatibility wrapper returning only a discovered Release name."""
        return self.lookup_release_for_snapshot(snapshot_name).name

    def find_release_for_snapshot_and_plan(self, snapshot_name: str, release_plan: str | None = None) -> str | None:
        """Compatibility wrapper returning only a discovered Release name."""
        return self.lookup_release_for_snapshot(snapshot_name, release_plan).name

    def lookup_release_for_snapshot(self, snapshot_name: str, release_plan: str | None = None) -> ReleaseLookup:
        """Find an active Release for a snapshot, optionally scoped to one ReleasePlan.

        When `release_plan` is given, a Release created against a *different* plan for the
        same snapshot is not matched. Promotion deliberately creates a second Release for
        content that already has a successful stage Release; treating that stage Release as
        "already done" would silently skip the promotion. When `release_plan` is None, a
        Release against any plan matches.

        If several Releases match, the newest by `metadata.creationTimestamp` is returned so
        adoption is deterministic rather than dependent on API response order.
        """
        path = f"/apis/appstudio.redhat.com/v1alpha1/namespaces/{self.namespace}/releases"
        successful_lookup = False
        for api in (self._api, self._ka_api):
            if api is None:
                continue
            try:
                result = api.list(path)
                successful_lookup = True
                candidates = []
                for item in result.get("items", []):
                    spec = item.get("spec", {})
                    if spec.get("snapshot") != snapshot_name:
                        continue
                    if release_plan is not None and spec.get("releasePlan") != release_plan:
                        continue
                    released = next(
                        (c for c in item.get("status", {}).get("conditions", []) if c.get("type") == "Released"),
                        None,
                    )
                    if released and released.get("status") == "False" and released.get("reason") != "Progressing":
                        continue
                    candidates.append(item)
                if candidates:
                    newest = max(candidates, key=lambda item: item.get("metadata", {}).get("creationTimestamp", ""))
                    return ReleaseLookup(ReleaseLookupState.FOUND, newest["metadata"]["name"])
            except (requests.RequestException, KeyError, TypeError):
                continue
        return ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY if successful_lookup else ReleaseLookupState.UNKNOWN)

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
