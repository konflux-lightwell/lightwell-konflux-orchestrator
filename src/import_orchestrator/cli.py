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

from import_orchestrator.ecosystems import ECOSYSTEMS


def make_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with one subparser per ecosystem."""
    parser = argparse.ArgumentParser(
        prog="import-orchestrator",
        description="Orchestrate Konflux import PipelineRuns across ecosystems",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """
        Environment Variables:
          QUAY_TOKEN                Required for fetch
          KONFLUX_TOKEN or KUBECONFIG  Required for cluster auth
          LIGHTWELL_ARTIFACT_TYPE   STAGE, REBUILD, REMEDIATED or NOVEL (default: STAGE)
          TEKTON_PIPELINE_DIR   Path to the pipeline definitions (defaults to {repo_root}/tekton)

        Examples:
          # Fetch OCI references into the database
          import-orchestrator java fetch

          # Import OCI references from a text file
          import-orchestrator java import-file refs.txt

          # Import OCI references from a consolidated build manifest
          import-orchestrator java import-manifest consolidated.yaml

          # Orchestrate imports (up to 10 parallel)
          import-orchestrator java orchestrate --max-parallel 10

          # Trigger a single PNC import PipelineRun
          import-orchestrator java trigger 'quay.io/example/image:tag@sha256:abc123...'

          # Full workflow
          import-orchestrator java fetch && import-orchestrator java orchestrate
        """
        ),
    )

    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="SQLite database path (default: per-ecosystem)",
    )

    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset database (delete existing data before running)",
    )

    subparsers = parser.add_subparsers(dest="ecosystem_name")
    for eco in ECOSYSTEMS:
        eco.register_cli(subparsers)

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

    if getattr(args, "ecosystem_name", None) is None:
        parser.print_help(sys.stderr)
        return 2

    if getattr(args, "func", None) is None:
        # Ecosystem chosen but no command given.
        args._ecosystem_parser.print_help(sys.stderr)
        return 2

    if args.db is None:
        args.db = Path(args.ecosystem.default_db_path)

    _handle_reset(args)

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
