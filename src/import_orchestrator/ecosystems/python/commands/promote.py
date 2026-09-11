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
import time

from import_orchestrator.clients import KubeClient
from import_orchestrator.commands.run import _emit_result
from import_orchestrator.constants import CLUSTER_API, DEFAULT_POLL_INTERVAL, KUBEARCHIVE_API
from import_orchestrator.ecosystems.base import Ecosystem

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_TIMEOUT = 2

# Matches the `pipeline` timeout on the production ReleasePlanAdmission. Polling
# for less than that would report a timeout on a release that is still healthy.
DEFAULT_TIMEOUT = 4 * 60 * 60


def register(subparsers: argparse._SubParsersAction, ecosystem: Ecosystem) -> None:
    """Register the 'promote' subcommand with the given subparsers."""
    parser = subparsers.add_parser(
        "promote",
        help="Promote an already-built Snapshot by releasing it against a second ReleasePlan",
        description=(
            "Create a Release for an existing Snapshot against a named ReleasePlan and "
            "wait for it to finish. Nothing is rebuilt: promotion re-releases the exact "
            "content that was already built and verified, which is what makes it a "
            "promotion rather than a second build. Intended for moving remediated Python "
            "content from staging to production once its Cumulative Ticket is cleared for "
            "release, but nothing here is specific to that policy -- the caller decides "
            "when promotion is warranted and which plan to target."
        ),
    )

    parser.add_argument(
        "snapshot",
        help="Name of the existing Konflux Snapshot to promote (e.g. remediated-build-xyz12)",
    )
    parser.add_argument(
        "--release-plan",
        required=True,
        help=(
            "ReleasePlan to release the snapshot against (e.g. remediated-build-prod). "
            "Required and never inferred: a snapshot's application maps to more than one "
            "plan once a production plan exists, and the difference between them is the "
            "difference between publishing to staging and publishing to production."
        ),
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=DEFAULT_POLL_INTERVAL,
        help=f"Seconds between status checks (default: {DEFAULT_POLL_INTERVAL})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=(
            f"Seconds to wait for the release to reach a terminal state "
            f"(default: {DEFAULT_TIMEOUT}, matching the production RPA pipeline timeout). "
            "On expiry the command exits 2 and the release keeps running; re-running "
            "adopts it rather than starting a second one."
        ),
    )
    parser.add_argument(
        "--output-json",
        default=None,
        metavar="PATH",
        help="Also write the final result payload as JSON to PATH (in addition to stdout)",
    )

    parser.set_defaults(func=promote, ecosystem=ecosystem)


def _payload(snapshot: str, release_plan: str, release_name: str | None, status: str, adopted: bool) -> dict:
    return {
        "snapshot": snapshot,
        "release_plan": release_plan,
        "release_name": release_name,
        "status": status,
        "adopted": adopted,
    }


def promote(args: argparse.Namespace) -> int:
    """Execute the promote subcommand."""
    eco = args.ecosystem
    kube = KubeClient(eco.namespace, CLUSTER_API, KUBEARCHIVE_API)

    # Scoped to the target plan: the snapshot almost certainly has a successful
    # Release against the *stage* plan already, and that must not be mistaken for
    # this promotion having been done.
    release_name = kube.find_release_for_snapshot_and_plan(args.snapshot, args.release_plan)
    adopted = release_name is not None

    if adopted:
        print(
            f"Found existing Release {release_name} for {args.snapshot} against "
            f"{args.release_plan}; adopting it instead of creating another.",
            file=sys.stderr,
        )
    else:
        release_name = kube.create_release(args.snapshot, args.release_plan, eco.pipelinerun_prefix)
        if not release_name:
            print(
                f"ERROR: failed to create a Release for {args.snapshot} against {args.release_plan}.",
                file=sys.stderr,
            )
            _emit_result(
                _payload(args.snapshot, args.release_plan, None, "CreateFailed", False),
                args.output_json,
            )
            return EXIT_FAILED
        print(f"Created Release {release_name}", file=sys.stderr)

    deadline = time.monotonic() + args.timeout
    status = None
    while True:
        status = kube.get_release_status(release_name)
        if status in ("True", "False"):
            break
        if time.monotonic() >= deadline:
            print(
                f"Timed out after {args.timeout}s waiting for {release_name}. "
                "It may still be running -- re-run to adopt it.",
                file=sys.stderr,
            )
            _emit_result(
                _payload(args.snapshot, args.release_plan, release_name, "Timeout", adopted),
                args.output_json,
            )
            return EXIT_TIMEOUT
        print(f"Release {release_name} still in progress, sleeping {args.poll_interval}s...", file=sys.stderr)
        time.sleep(args.poll_interval)

    _emit_result(
        _payload(args.snapshot, args.release_plan, release_name, status, adopted),
        args.output_json,
    )

    if status == "True":
        print(f"Release {release_name} succeeded.", file=sys.stderr)
        return EXIT_OK

    print(f"ERROR: Release {release_name} failed.", file=sys.stderr)
    return EXIT_FAILED
