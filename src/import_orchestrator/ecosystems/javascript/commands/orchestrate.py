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

from import_orchestrator.commands.orchestrate import add_common_orchestrate_args, run_orchestrate
from import_orchestrator.ecosystems.base import Ecosystem
from import_orchestrator.ecosystems.javascript.commands.trigger import add_build_args

_EMPTY_DB_WARNING = "No package references in database. Run 'import-orchestrator javascript import-file' first."


def register(subparsers: argparse._SubParsersAction, ecosystem: Ecosystem) -> None:
    """Register the 'orchestrate' subcommand with the given subparsers."""
    parser = subparsers.add_parser(
        "orchestrate",
        help="Orchestrate batch javascript-validated-build PipelineRuns",
        description="Orchestrate batch javascript-validated-build PipelineRuns",
    )
    add_common_orchestrate_args(parser)
    add_build_args(parser)
    parser.set_defaults(func=run, ecosystem=ecosystem)


def run(args: argparse.Namespace) -> int:
    """Execute the orchestrate subcommand."""
    return run_orchestrate(args, _EMPTY_DB_WARNING)
