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

from import_orchestrator.commands.run import run_single
from import_orchestrator.constants import DEFAULT_MAX_RETRIES, DEFAULT_POLL_INTERVAL
from import_orchestrator.ecosystems.base import Ecosystem
from import_orchestrator.ecosystems.python import config


def register(subparsers: argparse._SubParsersAction, ecosystem: Ecosystem) -> None:
    """Register the 'run' subcommand with the given subparsers."""
    parser = subparsers.add_parser(
        "run",
        help="Orchestrate a single python-remediated-build PipelineRun end-to-end",
        description=(
            "Trigger a python-remediated-build PipelineRun for a single "
            "package==version reference and monitor it end-to-end, with state "
            "tracking and retries. Like 'trigger', but synchronous: it waits for "
            "the import to reach a terminal state instead of returning immediately. "
            "By default it uses an ephemeral, isolated state database; pass a "
            "top-level --db to persist state for resume or inspection."
        ),
    )

    parser.add_argument(
        "ref",
        help="Package reference to build, in the form package==version (e.g. ntplib==0.4.0)",
    )
    parser.add_argument(
        "--target",
        choices=list(config.TARGET_CONFIGS),
        default=os.environ.get("LIGHTWELL_PYTHON_TARGET", config.DEFAULT_TARGET),
        help=f"Build target (default: {config.DEFAULT_TARGET}, or LIGHTWELL_PYTHON_TARGET env var)",
    )
    parser.add_argument(
        "--builds-tag",
        "--builds-ref",
        dest="builds_tag",
        default=None,
        help=(
            "Git revision -- branch, tag, or commit SHA -- in the lightwell-builds "
            "repo to build from (--builds-ref is a synonym). When omitted, defaults "
            "to the '<package>/<version>' tag derived from the ref argument -- by "
            "convention the tag the validated build publishes. The revision's "
            "existence is not checked here; a missing one fails later when the "
            "pipeline clones it."
        ),
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
        help=f"Max retry attempts for a failed import (default: {DEFAULT_MAX_RETRIES})",
    )

    parser.set_defaults(func=run, ecosystem=ecosystem)


def run(args: argparse.Namespace) -> int:
    """Execute the run subcommand."""
    return run_single(args, args.ref)
