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
from pathlib import Path

from import_orchestrator.constants import (
    CLUSTER_API,
    DEFAULT_MAX_PARALLEL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_POLL_INTERVAL,
    NAMESPACE,
)
from import_orchestrator.database import ImportDatabase
from import_orchestrator.kube import KubeClient
from import_orchestrator.orchestrator import ImportOrchestrator
from import_orchestrator.utils import get_build_definitions_scripts_dir


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the 'orchestrate' subcommand with the given subparsers."""
    scripts_dir = get_build_definitions_scripts_dir()

    parser = subparsers.add_parser(
        "orchestrate",
        help="Orchestrate batch PNC import PipelineRuns",
        description="Orchestrate batch PNC import PipelineRuns",
    )

    parser.add_argument(
        "--trigger-script",
        type=Path,
        default=scripts_dir / "trigger-pnc-import.sh",
        help="Path to trigger-pnc-import.sh",
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

    parser.set_defaults(func=run)


def _validate_trigger_script(args: argparse.Namespace) -> int | None:
    """Validate that the trigger script exists. Returns an exit code on failure, None on success."""
    if not args.trigger_script.exists():
        print(f"ERROR: trigger script not found: {args.trigger_script}", file=sys.stderr)
        return 2

    return None


def _is_database_empty(db: ImportDatabase) -> bool:
    """Check whether the database has any OCI references at all."""
    stats = db.get_statistics()
    return sum(stats.values()) == 0


def run(args: argparse.Namespace) -> int:
    """Execute the orchestrate subcommand."""
    validation_error = _validate_trigger_script(args)
    if validation_error is not None:
        return validation_error

    with ImportDatabase(args.db) as db:
        if _is_database_empty(db):
            print(
                "WARNING: No OCI references in database. Run 'import-orchestrator fetch' first.",
                file=sys.stderr,
            )

        kube = KubeClient(NAMESPACE, CLUSTER_API)
        orchestrator = ImportOrchestrator(
            db=db,
            kube=kube,
            trigger_script=args.trigger_script,
            max_parallel=args.max_parallel,
            poll_interval=args.poll_interval,
            max_retries=args.max_retries,
        )

        return orchestrator.run_until_complete()
