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

from import_orchestrator.clients import QuayClient
from import_orchestrator.database import ImportDatabase
from import_orchestrator.ecosystems.base import Ecosystem
from import_orchestrator.ecosystems.java import config
from import_orchestrator.engine import Ingest


def register(subparsers: argparse._SubParsersAction, ecosystem: Ecosystem) -> None:
    """Register the 'fetch' subcommand with the given subparsers."""
    parser = subparsers.add_parser(
        "fetch",
        help="Fetch OCI references from Quay and store them in the database",
        description="Fetch OCI references from Quay and store them in the database",
    )

    parser.add_argument(
        "--artifact-type",
        choices=list(config.ARTIFACT_CONFIGS),
        default=os.environ.get("LIGHTWELL_ARTIFACT_TYPE", "STAGE"),
        help="Artifact type (default: STAGE, or LIGHTWELL_ARTIFACT_TYPE env var)",
    )
    parser.set_defaults(func=run, ecosystem=ecosystem)


def run(args: argparse.Namespace) -> int:
    """Execute the fetch subcommand."""
    print(f"Fetching OCI references (artifact_type={args.artifact_type})...", file=sys.stderr)

    token = os.environ.get("QUAY_TOKEN")
    if not token:
        print(
            "ERROR: QUAY_TOKEN is required. Export QUAY_TOKEN='your-token-here'.",
            file=sys.stderr,
        )
        return 2

    artifact_config = config.ARTIFACT_CONFIGS[args.artifact_type]

    client = QuayClient(token=token, ref=artifact_config["source_repo"])

    with ImportDatabase(args.db) as db:
        ingest = Ingest(db)
        result = ingest.from_quay(client)

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
