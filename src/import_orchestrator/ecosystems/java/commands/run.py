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
from import_orchestrator.ecosystems.java import config


def register(subparsers: argparse._SubParsersAction, ecosystem: Ecosystem) -> None:
    """Register the 'run' subcommand with the given subparsers."""
    parser = subparsers.add_parser(
        "run",
        help="Orchestrate a single PNC import PipelineRun end-to-end",
        description=(
            "Trigger a single PNC import PipelineRun on the Konflux cluster and "
            "monitor it end-to-end, with state tracking and retries. Like "
            "'trigger', but synchronous: it waits for the import to reach a "
            "terminal state instead of returning immediately. By default it uses "
            "an ephemeral, isolated state database; pass a top-level --db to "
            "persist state for resume or inspection."
        ),
    )

    parser.add_argument(
        "source_image",
        help="OCI image reference to import (must be digest-pinned with @sha256:)",
    )
    parser.add_argument(
        "tag",
        nargs="?",
        default=None,
        help="Destination tag override (default: derived from source image)",
    )
    parser.add_argument(
        "--artifact-type",
        choices=list(config.ARTIFACT_CONFIGS),
        default=os.environ.get("LIGHTWELL_ARTIFACT_TYPE", "STAGE"),
        help="Artifact type (default: STAGE, or LIGHTWELL_ARTIFACT_TYPE env var)",
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
    return run_single(args, args.source_image)
