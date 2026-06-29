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
import textwrap
from pathlib import Path

from import_orchestrator.constants import (
    CLUSTER_API,
    DEFAULT_DB_PATH,
    DEFAULT_MAX_PARALLEL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_POLL_INTERVAL,
    NAMESPACE,
)
from import_orchestrator.database import ImportDatabase
from import_orchestrator.kube import KubeClient
from import_orchestrator.orchestrator import ImportOrchestrator


def make_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all CLI options."""
    parser = argparse.ArgumentParser(
        description="Orchestrate batch PNC import PipelineRuns",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """
        Environment Variables:
          QUAY_TOKEN                Required for fetch (unless --skip-fetch)
          KONFLUX_TOKEN or KUBECONFIG  Required for kubectl auth
          LIGHTWELL_ARTIFACT_TYPE   REBUILD or REMEDIATED (default: REBUILD)

        Examples:
          # Fetch and import up to 10 parallel
          ./orchestrate-pnc-import.py --max-parallel 10

          # Resume from existing database
          ./orchestrate-pnc-import.py --db existing.db --skip-fetch

          # Dry run: fetch and populate database only
          ./orchestrate-pnc-import.py --fetch-only
        """
        ),
    )

    parser.add_argument(
        "--db",
        type=Path,
        default=Path(DEFAULT_DB_PATH),
        help=f"SQLite database path (default: {DEFAULT_DB_PATH})",
    )

    # Default to scripts in build-definitions submodule
    project_root = Path(__file__).parent.parent.parent.parent
    scripts_dir = project_root / "build-definitions" / "docs" / "examples"

    parser.add_argument(
        "--fetch-script",
        type=Path,
        default=scripts_dir / "fetch_pnc_oci_references.sh",
        help="Path to fetch_pnc_oci_references.sh",
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

    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Skip fetching OCI refs (resume from existing database)",
    )

    parser.add_argument(
        "--fetch-only",
        action="store_true",
        help="Only fetch and populate database, don't trigger imports",
    )

    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset database (delete existing data before fetch)",
    )

    return parser


def _validate_scripts(args: argparse.Namespace) -> int | None:
    """Validate that required scripts exist. Returns an exit code on failure, None on success."""
    if not args.skip_fetch and not args.fetch_script.exists():
        print(f"ERROR: fetch script not found: {args.fetch_script}", file=sys.stderr)
        return 2

    if not args.fetch_only and not args.trigger_script.exists():
        print(f"ERROR: trigger script not found: {args.trigger_script}", file=sys.stderr)
        return 2

    return None


def _handle_reset(args: argparse.Namespace) -> None:
    """Delete the existing database if --reset was specified."""
    if args.reset and args.db.exists():
        args.db.unlink()
        print(f"Deleted existing database: {args.db}", file=sys.stderr)


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


def main() -> int:
    """CLI entry point."""
    parser = make_parser()
    args = parser.parse_args()

    validation_error = _validate_scripts(args)
    if validation_error is not None:
        return validation_error

    _handle_reset(args)

    with ImportDatabase(args.db) as db:
        kube = KubeClient(NAMESPACE, CLUSTER_API)
        orchestrator = ImportOrchestrator(
            db=db,
            kube=kube,
            trigger_script=args.trigger_script,
            max_parallel=args.max_parallel,
            poll_interval=args.poll_interval,
            max_retries=args.max_retries,
        )

        if not args.skip_fetch:
            total_fetched, _ = _run_fetch_phase(orchestrator, args.fetch_script)
            if total_fetched == 0:
                return 0

        if args.fetch_only:
            stats = db.get_statistics()
            print(f"Database state: {stats}", file=sys.stderr)
            return 0

        return orchestrator.run_until_complete()


if __name__ == "__main__":
    sys.exit(main())
