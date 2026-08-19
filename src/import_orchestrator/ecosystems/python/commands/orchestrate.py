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

from import_orchestrator.commands.orchestrate import run_orchestrate
from import_orchestrator.constants import (
    DEFAULT_MAX_PARALLEL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_POLL_INTERVAL,
)
from import_orchestrator.ecosystems.base import Ecosystem

_EMPTY_DB_WARNING = "No package references in database. Run 'import-orchestrator python import-file' first."


def register(subparsers: argparse._SubParsersAction, ecosystem: Ecosystem) -> None:
    """Register the 'orchestrate' subcommand with the given subparsers."""
    parser = subparsers.add_parser(
        "orchestrate",
        help="Orchestrate batch python-remediated-build PipelineRuns",
        description="Orchestrate batch python-remediated-build PipelineRuns",
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

    parser.set_defaults(func=run, ecosystem=ecosystem)


def run(args: argparse.Namespace) -> int:
    """Execute the orchestrate subcommand."""
    return run_orchestrate(args, _EMPTY_DB_WARNING)
