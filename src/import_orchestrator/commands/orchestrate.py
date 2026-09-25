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
from import_orchestrator.constants import (
    CLUSTER_API,
    DEFAULT_MAX_PARALLEL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_POLL_INTERVAL,
    KUBEARCHIVE_API,
)
from import_orchestrator.database import ImportDatabase
from import_orchestrator.engine import ImportOrchestrator, ImportTrigger, PipelineMonitor, ReleaseMonitor


def add_common_orchestrate_args(parser: argparse.ArgumentParser) -> None:
    """Add the flags shared by every ecosystem's ``orchestrate`` subcommand.

    Ecosystems add their own selectors (``--artifact-type``, ``--target``, ...)
    on top of these.
    """
    parser.add_argument(
        "--max-parallel",
        type=int,
        default=DEFAULT_MAX_PARALLEL,
        help=f"Maximum parallel PipelineRuns (default: {DEFAULT_MAX_PARALLEL})",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=DEFAULT_POLL_INTERVAL,
        help=f"Seconds between status checks (default: {DEFAULT_POLL_INTERVAL})",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help=f"Max retry attempts for failed imports (default: {DEFAULT_MAX_RETRIES})",
    )
    parser.add_argument(
        "--print-resources",
        action="store_true",
        help="Preview the release chain (PipelineRun YAML + resolved ReleasePlan/RPA) "
        "for each pending reference and exit, without contacting the cluster.",
    )
    parser.add_argument(
        "--release-data-repo",
        default=None,
        help="Path to a konflux-release-data checkout for --print-resources "
        "(default: reference/konflux-release-data, cloned on demand).",
    )


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
