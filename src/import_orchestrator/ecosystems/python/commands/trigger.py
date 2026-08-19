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

from import_orchestrator.commands.trigger import run_trigger
from import_orchestrator.ecosystems.base import Ecosystem


def register(subparsers: argparse._SubParsersAction, ecosystem: Ecosystem) -> None:
    """Register the 'trigger' subcommand with the given subparsers."""
    parser = subparsers.add_parser(
        "trigger",
        help="Trigger a python-remediated-build PipelineRun",
        description=(
            "Trigger a python-remediated-build PipelineRun on the Konflux cluster "
            "for a single package==version reference."
        ),
    )

    parser.add_argument(
        "ref",
        help="Package reference to build, in the form package==version (e.g. ntplib==0.4.0)",
    )

    parser.set_defaults(func=run, ecosystem=ecosystem)


def run(args: argparse.Namespace) -> int:
    """Execute the trigger subcommand."""
    return run_trigger(args, args.ref)
