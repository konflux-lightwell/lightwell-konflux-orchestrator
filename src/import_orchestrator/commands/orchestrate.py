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
from import_orchestrator.constants import (
    ARTIFACT_CONFIGS,
    CLUSTER_API,
    DEFAULT_MAX_PARALLEL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_POLL_INTERVAL,
    NAMESPACE,
)
from import_orchestrator.database import ImportDatabase
from import_orchestrator.engine import ImportOrchestrator, ImportTrigger, PipelineMonitor, ReleaseMonitor
from import_orchestrator.engine.pipelinerun import PipelineRunBuilder


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the 'orchestrate' subcommand with the given subparsers."""
    parser = subparsers.add_parser(
        "orchestrate",
        help="Orchestrate batch PNC import PipelineRuns",
        description="Orchestrate batch PNC import PipelineRuns",
    )

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
        "--artifact-type",
        choices=list(ARTIFACT_CONFIGS),
        default=os.environ.get("LIGHTWELL_ARTIFACT_TYPE", "STAGE"),
        help="Artifact type (default: STAGE, or LIGHTWELL_ARTIFACT_TYPE env var)",
    )

    parser.set_defaults(func=run)


def _is_database_empty(db: ImportDatabase) -> bool:
    """Check whether the database has any OCI references at all."""
    stats = db.get_statistics()
    return sum(stats.values()) == 0


def run(args: argparse.Namespace) -> int:
    """Execute the orchestrate subcommand."""
    with ImportDatabase(args.db) as db:
        if _is_database_empty(db):
            print(
                "WARNING: No OCI references in database. Run 'import-orchestrator fetch' first.",
                file=sys.stderr,
            )

        kube = KubeClient(NAMESPACE, CLUSTER_API)
        builder = PipelineRunBuilder(kube=kube, artifact_type=args.artifact_type)

        # Construct the specialized components
        trigger = ImportTrigger(
            db=db,
            builder=builder,
            max_parallel=args.max_parallel,
            max_retries=args.max_retries,
        )
        pipeline_monitor = PipelineMonitor(db=db, kube=kube)
        release_monitor = ReleaseMonitor(db=db, kube=kube, max_parallel=args.max_parallel)

        # Construct the coordinator
        orchestrator = ImportOrchestrator(
            db=db,
            trigger=trigger,
            pipeline_monitor=pipeline_monitor,
            release_monitor=release_monitor,
            poll_interval=args.poll_interval,
            max_retries=args.max_retries,
        )

        return orchestrator.run_until_complete()
