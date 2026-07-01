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
from import_orchestrator.engine import OciIngest
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


def run(args: argparse.Namespace) -> int:
    """Execute the fetch subcommand."""
    if not args.fetch_script.exists():
        print(f"ERROR: fetch script not found: {args.fetch_script}", file=sys.stderr)
        return 2

    print("Fetching OCI references...", file=sys.stderr)

    with ImportDatabase(args.db) as db:
        ingest = OciIngest(db)
        result = ingest.from_script(args.fetch_script)

        _print_summary(result)

        if result.total > 0:
            stats = db.get_statistics()
            print(f"Database state: {stats}", file=sys.stderr)

    return 0


def _print_summary(result) -> None:
    """Print a human-readable summary of the fetch results to stderr."""
    if result.total == 0:
        print("No OCI references found - exiting successfully", file=sys.stderr)
    elif result.newly_added == 0:
        print(
            f"Fetched {result.total} OCI reference(s), all already in database",
            file=sys.stderr,
        )
    elif result.newly_added == result.total:
        print(f"Added {result.newly_added} new OCI reference(s) to database", file=sys.stderr)
    else:
        print(
            f"Fetched {result.total} OCI reference(s): {result.newly_added} new, "
            f"{result.duplicates} already in database",
            file=sys.stderr,
        )
