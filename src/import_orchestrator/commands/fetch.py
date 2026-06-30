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

from import_orchestrator.database import ImportDatabase
from import_orchestrator.orchestrator import ImportOrchestrator
from import_orchestrator.utils import get_build_definitions_scripts_dir


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the 'fetch' subcommand with the given subparsers."""
    scripts_dir = get_build_definitions_scripts_dir()

    parser = subparsers.add_parser(
        "fetch",
        help="Fetch OCI references and store them in the database",
        description="Fetch OCI references and store them in the database",
    )

    parser.add_argument(
        "--fetch-script",
        type=Path,
        default=scripts_dir / "fetch_pnc_oci_references.sh",
        help="Path to fetch_pnc_oci_references.sh",
    )

    parser.set_defaults(func=run)


def _create_fetch_only_orchestrator(db: ImportDatabase) -> ImportOrchestrator:
    """Create an ImportOrchestrator configured only for fetching OCI references.

    The orchestrator's trigger/polling parameters are set to dummy values
    because they are unused during the fetch phase.
    """
    return ImportOrchestrator(
        db=db,
        kube=None,  # type: ignore[arg-type]
        trigger_script=Path("/unused"),
        max_parallel=0,
        poll_interval=0,
        max_retries=0,
    )


def run(args: argparse.Namespace) -> int:
    """Execute the fetch subcommand."""
    if not args.fetch_script.exists():
        print(f"ERROR: fetch script not found: {args.fetch_script}", file=sys.stderr)
        return 2

    with ImportDatabase(args.db) as db:
        orchestrator = _create_fetch_only_orchestrator(db)

        total_fetched, newly_added = _run_fetch_phase(orchestrator, args.fetch_script)

        if total_fetched > 0:
            stats = db.get_statistics()
            print(f"Database state: {stats}", file=sys.stderr)

    return 0


def _run_fetch_phase(orchestrator: ImportOrchestrator, fetch_script: Path) -> tuple[int, int]:
    """Execute the fetch phase and print results. Returns (total_fetched, newly_added)."""
    print("Fetching OCI references...", file=sys.stderr)
    total_fetched, newly_added = orchestrator.fetch_and_store_oci_refs(fetch_script)

    if total_fetched == 0:
        print("No OCI references found - exiting successfully", file=sys.stderr)
    elif newly_added == 0:
        print(
            f"Fetched {total_fetched} OCI reference(s), all already in database",
            file=sys.stderr,
        )
    elif newly_added == total_fetched:
        print(f"Added {newly_added} new OCI reference(s) to database", file=sys.stderr)
    else:
        print(
            f"Fetched {total_fetched} OCI reference(s): {newly_added} new, "
            f"{total_fetched - newly_added} already in database",
            file=sys.stderr,
        )

    return total_fetched, newly_added
