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
from import_orchestrator.ecosystems.java.parser import parse_manifest
from import_orchestrator.engine import Ingest


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the 'import-manifest' subcommand with the given subparsers."""
    parser = subparsers.add_parser(
        "import-manifest",
        help="Import OCI references from a consolidated build manifest (YAML)",
        description=(
            "Import OCI references from a consolidated build manifest into the database. "
            "Each library entry's artifact tag and digest are combined into a "
            "tag@digest reference when both are present, falling back to digest-only."
        ),
    )

    parser.add_argument(
        "file",
        type=Path,
        help="Path to a consolidated build manifest YAML file",
    )

    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the import-manifest subcommand."""
    if not args.file.exists():
        print(f"ERROR: file not found: {args.file}", file=sys.stderr)
        return 2

    refs = parse_manifest(args.file)
    with ImportDatabase(args.db) as db:
        ingest = Ingest(db)
        result = ingest.from_lines(refs)

    if result.total == 0:
        print("No OCI references found in manifest", file=sys.stderr)
    elif result.newly_added == 0:
        print(
            f"Read {result.total} OCI reference(s) from manifest, all already in database",
            file=sys.stderr,
        )
    elif result.newly_added == result.total:
        print(f"Added {result.newly_added} new OCI reference(s) to database", file=sys.stderr)
    else:
        print(
            f"Read {result.total} OCI reference(s) from manifest: "
            f"{result.newly_added} new, {result.duplicates} already in database",
            file=sys.stderr,
        )

    return 0
