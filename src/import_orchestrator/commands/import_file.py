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


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the 'import-file' subcommand with the given subparsers."""
    parser = subparsers.add_parser(
        "import-file",
        help="Import OCI references from a text file into the database",
        description="Import OCI references from a text file into the database",
    )

    parser.add_argument(
        "file",
        type=Path,
        help="Path to a text file containing OCI references (one per line)",
    )

    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the import-file subcommand."""
    if not args.file.exists():
        print(f"ERROR: file not found: {args.file}", file=sys.stderr)
        return 2

    oci_refs = _read_oci_references(args.file)

    with ImportDatabase(args.db) as db:
        newly_added = _import_references(db, oci_refs)

    _print_summary(total=len(oci_refs), newly_added=newly_added)
    return 0


def _read_oci_references(file_path: Path) -> list[str]:
    """Read OCI references from a text file, skipping blank lines and comments."""
    lines = file_path.read_text().splitlines()
    return [line.strip() for line in lines if _is_oci_reference(line)]


def _is_oci_reference(line: str) -> bool:
    """Return True if the line contains an OCI reference (not blank or a comment)."""
    stripped = line.strip()
    return stripped != "" and not stripped.startswith("#")


def _import_references(db: ImportDatabase, oci_refs: list[str]) -> int:
    """Add each OCI reference to the database. Returns the count of newly added entries."""
    newly_added = 0
    for ref in oci_refs:
        _, was_inserted = db.add_oci_reference(ref)
        if was_inserted:
            newly_added += 1
    return newly_added


def _print_summary(total: int, newly_added: int) -> None:
    """Print a human-readable summary of the import results to stderr."""
    if total == 0:
        print("No OCI references found in file", file=sys.stderr)
    elif newly_added == 0:
        print(
            f"Read {total} OCI reference(s) from file, all already in database",
            file=sys.stderr,
        )
    elif newly_added == total:
        print(f"Added {newly_added} new OCI reference(s) to database", file=sys.stderr)
    else:
        print(
            f"Read {total} OCI reference(s) from file: {newly_added} new, {total - newly_added} already in database",
            file=sys.stderr,
        )
