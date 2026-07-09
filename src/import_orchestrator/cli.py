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

from import_orchestrator.commands import fetch, import_file, orchestrate, trigger
from import_orchestrator.constants import DEFAULT_DB_PATH


def make_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="import-orchestrator",
        description="Orchestrate batch PNC import PipelineRuns",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """
        Environment Variables:
          QUAY_TOKEN                Required for fetch
          KONFLUX_TOKEN or KUBECONFIG  Required for kubectl auth
          LIGHTWELL_ARTIFACT_TYPE   STAGE, REBUILD or REMEDIATED (default: STAGE)
          TEKTON_PIPELINE_DIR   Path to the pipeline definitions (defaults to {repo_root}/tekton)
          TASK_BUNDLE_PULLSPEC  Override for the oci-verify-import task bundle (defaults to 0.1)

        Examples:
          # Fetch OCI references into the database
          import-orchestrator fetch

          # Import OCI references from a text file
          import-orchestrator import-file refs.txt

          # Orchestrate imports (up to 10 parallel)
          import-orchestrator orchestrate --max-parallel 10

          # Trigger a single PNC import PipelineRun
          import-orchestrator trigger 'quay.io/example/image:tag@sha256:abc123...'

          # Full workflow
          import-orchestrator fetch && import-orchestrator orchestrate
        """
        ),
    )

    parser.add_argument(
        "--db",
        type=Path,
        default=Path(DEFAULT_DB_PATH),
        help=f"SQLite database path (default: {DEFAULT_DB_PATH})",
    )

    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset database (delete existing data before running)",
    )

    subparsers = parser.add_subparsers(dest="command")
    fetch.register(subparsers)
    import_file.register(subparsers)
    orchestrate.register(subparsers)
    trigger.register(subparsers)

    return parser


def _handle_reset(args: argparse.Namespace) -> None:
    """Delete the existing database if --reset was specified."""
    if args.reset and args.db.exists():
        args.db.unlink()
        print(f"Deleted existing database: {args.db}", file=sys.stderr)


def main() -> int:
    """CLI entry point."""
    parser = make_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help(sys.stderr)
        return 2

    _handle_reset(args)

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
