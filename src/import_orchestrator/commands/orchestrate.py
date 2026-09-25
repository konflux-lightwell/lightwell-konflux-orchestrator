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
from import_orchestrator.database import ImportDatabase
from import_orchestrator.engine import ImportOrchestrator, ImportTrigger, PipelineMonitor, ReleaseMonitor
from import_orchestrator.engine.errors import TriggerError
from import_orchestrator.models import ImportStatus
from import_orchestrator.release_data import ReleaseDataError, ReleaseDataResolver, application_of


def _is_database_empty(db: ImportDatabase) -> bool:
    """Check whether the database has any import references at all."""
    stats = db.get_statistics()
    return sum(stats.values()) == 0


def run_orchestrate(args: argparse.Namespace, empty_db_warning: str) -> int:
    """Run the batch import orchestration loop for the given ecosystem.

    Generic across ecosystems: the ecosystem supplies its own namespace,
    PipelineRun prefix and manifest builder. ``empty_db_warning`` is the
    ecosystem-specific hint shown when the database has no references yet.
    """
    with ImportDatabase(args.db) as db:
        if _is_database_empty(db):
            print(f"WARNING: {empty_db_warning}", file=sys.stderr)

        if getattr(args, "print_resources", False):
            return _run_print_resources(args, db)

        eco = args.ecosystem
        kube = KubeClient(eco.namespace, CLUSTER_API, KUBEARCHIVE_API)

        trigger = ImportTrigger(
            db=db,
            kube=kube,
            build_pipelinerun=lambda ref: eco.build_pipelinerun(ref, args),
            max_parallel=args.max_parallel,
            max_retries=args.max_retries,
        )
        target = getattr(args, "target", None)
        skip_release = target is not None and getattr(eco, "target_skip_release", lambda t: False)(target)
        pipeline_monitor = PipelineMonitor(db=db, kube=kube, skip_release=skip_release)
        release_monitor = ReleaseMonitor(
            db=db, kube=kube, max_parallel=args.max_parallel, prefix=eco.pipelinerun_prefix
        )

        orchestrator = ImportOrchestrator(
            db=db,
            trigger=trigger,
            pipeline_monitor=pipeline_monitor,
            release_monitor=release_monitor,
            poll_interval=args.poll_interval,
            max_retries=args.max_retries,
        )

        return orchestrator.run_until_complete()


def _run_print_resources(args: argparse.Namespace, db: ImportDatabase) -> int:
    """Preview the release chain for every pending reference, without the cluster.

    For each pending item this builds the PipelineRun manifest the trigger would
    submit, then resolves its ReleasePlan and ReleasePlanAdmission from a local
    konflux-release-data checkout. Nothing is created and no KubeClient is opened.
    """
    eco = args.ecosystem
    pending = db.get_by_status(ImportStatus.PENDING)
    if not pending:
        print("No pending references to preview.", file=sys.stderr)
        return 0

    try:
        resolver = ReleaseDataResolver.ensure(args.release_data_repo)
    except ReleaseDataError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    for item in pending:
        print(f"\n=== {item.ref} ===")
        try:
            manifest = eco.build_pipelinerun(item.ref, args)
        except TriggerError as exc:
            print(f"  ERROR building PipelineRun: {exc}", file=sys.stderr)
            continue

        app = application_of(manifest)
        namespace = (manifest.get("metadata", {}) or {}).get("namespace", eco.namespace)
        print(f"Application:          {app or '(no application label)'}")

        if app:
            _print_release_chain(resolver, app, namespace)

        print("Snapshot / Release:  created at build time")

    sha = resolver.head_revision()
    suffix = f" @ {sha[:12]}" if sha else ""
    print(f"\nResolved from konflux-release-data{suffix} (desired state; may lag the cluster).", file=sys.stderr)
    return 0


def _print_release_chain(resolver: ReleaseDataResolver, app: str, namespace: str) -> None:
    """Print the resolved ReleasePlan and RPA for an application, or why not."""
    try:
        rp = resolver.find_release_plan(app, namespace)
    except ReleaseDataError as exc:
        print(f"ReleasePlan:          UNRESOLVED ({exc})")
        return

    print(f"ReleasePlan:          {rp.name}")
    try:
        rpa = resolver.find_rpa(rp)
    except ReleaseDataError as exc:
        print(f"ReleasePlanAdmission: UNRESOLVED ({exc})")
        return

    print(f"ReleasePlanAdmission: {rpa.name}")
    if rpa.policy:
        print(f"  EC policy:          {rpa.policy}")
    if rpa.pipeline:
        print(f"  Release pipeline:   {rpa.pipeline}")
    if rpa.service_account:
        print(f"  Service account:    {rpa.service_account}")
    if rpa.pulp:
        pulp = rpa.pulp
        target = "/".join(str(pulp.get(k)) for k in ("domain", "pulpDomain", "repository") if pulp.get(k))
        if target:
            print(f"  Pulp target:        {target}")
    if rpa.intention:
        print(f"  Intention:          {rpa.intention}")
