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

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from import_orchestrator import clients
from import_orchestrator.clients import GitError
from import_orchestrator.constants import RELEASE_DATA_DEFAULT_PATH, RELEASE_DATA_REPO_URL

# GitOps trees within konflux-release-data, scoped to the production cluster this
# tool targets (matches constants.CLUSTER_API, api.stone-prod-p01.wcfb.p1). The
# tenant tree holds ReleasePlans; the managed tree holds ReleasePlanAdmissions.
# Scoping to the cluster's own subtree keeps the scan to a few hundred files
# instead of the ~100k across the whole repo, and excludes other clusters and
# staging automatically.
_TENANT_TREE = "tenants-config/cluster/stone-prod-p01/tenants"
_MANAGED_TREE = "config/stone-prod-p01.wcfb.p1"
# `auto-generated/` subtrees duplicate hand-authored ReleasePlans; skip them so
# the same plan is not counted twice.
_SKIP_SEGMENT = "auto-generated"

_RP_LABEL_RPA = "release.appstudio.openshift.io/releasePlanAdmission"
_RP_LABEL_AUTO_RELEASE = "release.appstudio.openshift.io/auto-release"
_APP_LABEL = "appstudio.openshift.io/application"


class ReleaseDataError(Exception):
    """The release chain could not be resolved from konflux-release-data."""


@dataclass(frozen=True)
class ReleasePlan:
    """A ReleasePlan as read from the GitOps repo."""

    name: str
    namespace: str
    application: str
    target: str
    rpa_name: str | None
    auto_release: bool


@dataclass(frozen=True)
class ReleasePlanAdmission:
    """The fields of a ReleasePlanAdmission worth surfacing in a preview."""

    name: str
    namespace: str
    applications: tuple[str, ...]
    origin: str
    policy: str | None
    service_account: str | None
    pipeline: str | None
    pulp: dict | None
    intention: str | None


class ReleaseDataResolver:
    """Resolve application -> ReleasePlan -> ReleasePlanAdmission from files.

    Reads a local checkout of konflux-release-data. No cluster access: this is
    the GitOps *desired state*, which a CI pipeline applies to the cluster.
    """

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
        # konflux-release-data has ~100k YAML files; parsing them is expensive, so
        # each tree is scanned and parsed at most once and reused across refs.
        self._release_plans: list[ReleasePlan] | None = None
        self._admissions: list[ReleasePlanAdmission] | None = None

    @classmethod
    def ensure(cls, repo_path: str | None) -> ReleaseDataResolver:
        """Return a resolver, cloning the default checkout if it is absent.

        When ``repo_path`` is given, it must already exist (the caller pointed at
        a specific checkout on purpose). When it is ``None``, the default path is
        used and cloned on demand if missing.
        """
        if repo_path is not None:
            path = Path(repo_path)
            if not path.exists():
                raise ReleaseDataError(f"release-data repo not found: {path}")
            return cls(path)

        path = Path(RELEASE_DATA_DEFAULT_PATH)
        if not path.exists():
            print(
                f"Cloning konflux-release-data into {path} ...",
                file=sys.stderr,
            )
            try:
                clients.clone(RELEASE_DATA_REPO_URL, path)
            except GitError as exc:
                raise ReleaseDataError(f"failed to clone konflux-release-data: {exc}") from exc
        return cls(path)

    def head_revision(self) -> str | None:
        """Return the checkout's HEAD sha, or None if it cannot be read."""
        try:
            return clients.GitClient(self.repo_path).head_revision()
        except GitError:
            return None

    def find_release_plan(self, application: str, namespace: str) -> ReleasePlan:
        """Return the single auto-releasing ReleasePlan for the application.

        Mirrors the live selection rule (``KubeClient.find_release_plan_for_snapshot``):
        exactly one plan whose ``spec.application`` matches and that is not
        labelled ``auto-release: 'false'``.
        """
        matches: dict[tuple[str, str], ReleasePlan] = {}
        for rp in self._all_release_plans():
            if rp.application != application or rp.namespace != namespace:
                continue
            if not rp.auto_release:
                continue
            matches[(rp.name, rp.namespace)] = rp  # dedupe identical plans

        plans = list(matches.values())
        if not plans:
            raise ReleaseDataError(
                f"no auto-releasing ReleasePlan for application '{application}' in namespace '{namespace}'"
            )
        if len(plans) > 1:
            names = ", ".join(sorted(p.name for p in plans))
            raise ReleaseDataError(
                f"{len(plans)} auto-releasing ReleasePlans match application '{application}': {names}"
            )
        return plans[0]

    def find_rpa(self, release_plan: ReleasePlan) -> ReleasePlanAdmission:
        """Resolve the ReleasePlanAdmission the ReleasePlan binds to.

        The RP's ``releasePlanAdmission`` label names the RPA authoritatively;
        the (application, origin, target) tuple validates it. A mismatch is
        surfaced as a warning rather than silently trusted.
        """
        admissions = self._all_admissions()

        if release_plan.rpa_name:
            for rpa in admissions:
                if rpa.name == release_plan.rpa_name and rpa.namespace == release_plan.target:
                    _warn_on_tuple_mismatch(release_plan, rpa)
                    return rpa
            raise ReleaseDataError(
                f"ReleasePlan '{release_plan.name}' references RPA '{release_plan.rpa_name}' "
                f"in namespace '{release_plan.target}', but it was not found"
            )

        # No explicit label: fall back to tuple match.
        tuple_matches = [
            rpa
            for rpa in admissions
            if release_plan.application in rpa.applications
            and rpa.namespace == release_plan.target
            and rpa.origin == release_plan.namespace
        ]
        if not tuple_matches:
            raise ReleaseDataError(f"no ReleasePlanAdmission matches ReleasePlan '{release_plan.name}'")
        if len(tuple_matches) > 1:
            names = ", ".join(sorted(r.name for r in tuple_matches))
            raise ReleaseDataError(f"ambiguous ReleasePlanAdmission for ReleasePlan '{release_plan.name}': {names}")
        return tuple_matches[0]

    def _all_release_plans(self) -> list[ReleasePlan]:
        """Parse (once) and cache every ReleasePlan in the tenant tree."""
        if self._release_plans is None:
            self._release_plans = [
                rp
                for doc in self._iter_docs(self.repo_path / _TENANT_TREE, "ReleasePlan")
                if (rp := _parse_release_plan(doc)) is not None
            ]
        return self._release_plans

    def _all_admissions(self) -> list[ReleasePlanAdmission]:
        """Parse (once) and cache every ReleasePlanAdmission in the managed tree."""
        if self._admissions is None:
            self._admissions = [
                rpa
                for doc in self._iter_docs(self.repo_path / _MANAGED_TREE, "ReleasePlanAdmission")
                if (rpa := _parse_rpa(doc)) is not None
            ]
        return self._admissions

    def _iter_docs(self, root: Path, kind: str):
        """Yield every YAML doc of the given ``kind`` under ``root``.

        The repo holds ~100k YAML files, so YAML-parsing all of them is far too
        slow. A fast ``grep`` first narrows the walk to files that actually carry
        a ``kind: <kind>`` line; only those are parsed. If ``grep`` is missing,
        this falls back to walking every ``*.yaml``.
        """
        if not root.is_dir():
            return
        for path in self._candidate_files(root, kind):
            if _SKIP_SEGMENT in path.parts:
                continue
            try:
                docs = yaml.safe_load_all(path.read_text())
                for doc in docs:
                    if isinstance(doc, dict) and doc.get("kind") == kind:
                        yield doc
            except (OSError, yaml.YAMLError):
                continue

    @staticmethod
    def _candidate_files(root: Path, kind: str) -> list[Path]:
        """Return YAML files under ``root`` whose text has a ``kind: <kind>`` line.

        Uses ``grep`` for speed; on any failure falls back to every ``*.yaml`` so
        correctness never depends on ``grep`` being present.
        """
        # Anchored so `kind: ReleasePlan` does not also match `ReleasePlanAdmission`.
        pattern = rf"^kind:[[:space:]]*{kind}[[:space:]]*$"
        try:
            result = subprocess.run(
                ["grep", "-rlE", "--include=*.yaml", pattern, str(root)],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return list(root.rglob("*.yaml"))
        if result.returncode not in (0, 1):  # 0 = matches, 1 = no matches
            return list(root.rglob("*.yaml"))
        return [Path(line) for line in result.stdout.splitlines() if line]


def application_of(manifest: dict) -> str | None:
    """Return the Application name a PipelineRun manifest targets, if labelled."""
    labels = (manifest.get("metadata", {}) or {}).get("labels", {}) or {}
    return labels.get(_APP_LABEL)


def _parse_release_plan(doc: dict) -> ReleasePlan | None:
    meta = doc.get("metadata", {}) or {}
    spec = doc.get("spec", {}) or {}
    name = meta.get("name")
    application = spec.get("application")
    if not name or not application:
        return None
    labels = meta.get("labels", {}) or {}
    return ReleasePlan(
        name=name,
        namespace=meta.get("namespace", ""),
        application=application,
        target=spec.get("target", ""),
        rpa_name=labels.get(_RP_LABEL_RPA),
        auto_release=labels.get(_RP_LABEL_AUTO_RELEASE) != "false",
    )


def _parse_rpa(doc: dict) -> ReleasePlanAdmission | None:
    meta = doc.get("metadata", {}) or {}
    spec = doc.get("spec", {}) or {}
    name = meta.get("name")
    if not name:
        return None
    pipeline = None
    pipeline_ref = (spec.get("pipeline", {}) or {}).get("pipelineRef", {}) or {}
    for param in pipeline_ref.get("params", []) or []:
        if param.get("name") == "pathInRepo":
            pipeline = param.get("value")
    data = spec.get("data", {}) or {}
    return ReleasePlanAdmission(
        name=name,
        namespace=meta.get("namespace", ""),
        applications=tuple(spec.get("applications", []) or []),
        origin=spec.get("origin", ""),
        policy=spec.get("policy"),
        service_account=(spec.get("pipeline", {}) or {}).get("serviceAccountName"),
        pipeline=pipeline,
        pulp=data.get("pulp"),
        intention=data.get("intention"),
    )


def _warn_on_tuple_mismatch(rp: ReleasePlan, rpa: ReleasePlanAdmission) -> None:
    """Warn if the label-resolved RPA disagrees with the (app, origin, target) tuple."""
    problems = []
    if rp.application not in rpa.applications:
        problems.append(f"application '{rp.application}' not in RPA applications {list(rpa.applications)}")
    if rp.target and rpa.namespace and rp.target != rpa.namespace:
        problems.append(f"RP target '{rp.target}' != RPA namespace '{rpa.namespace}'")
    if rpa.origin and rp.namespace and rpa.origin != rp.namespace:
        problems.append(f"RPA origin '{rpa.origin}' != RP namespace '{rp.namespace}'")
    if problems:
        print(
            f"WARNING: ReleasePlan '{rp.name}' -> RPA '{rpa.name}' label/tuple mismatch: {'; '.join(problems)}",
            file=sys.stderr,
        )
