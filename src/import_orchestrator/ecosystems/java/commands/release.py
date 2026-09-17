from __future__ import annotations

import argparse
import os

from import_orchestrator.clients import KubeClient
from import_orchestrator.constants import CLUSTER_API, DEFAULT_MAX_PARALLEL, KUBEARCHIVE_API
from import_orchestrator.database import ImportDatabase
from import_orchestrator.ecosystems.base import Ecosystem
from import_orchestrator.ecosystems.java import config
from import_orchestrator.engine.release_only import ReleaseOnly


def register(subparsers: argparse._SubParsersAction, ecosystem: Ecosystem) -> None:
    parser = subparsers.add_parser(
        "release",
        help="Reconcile/re-release existing Snapshots without rerunning imports",
        description=(
            "Adopt progressing/successful Releases or create a Release after a terminal "
            "failure. Never creates an import PipelineRun."
        ),
    )
    parser.add_argument(
        "--max-parallel",
        type=int,
        default=DEFAULT_MAX_PARALLEL,
        help="Maximum concurrent import/Release work; pending Snapshot matches do not consume capacity",
    )
    parser.add_argument(
        "--release-plan",
        default=os.environ.get("KONFLUX_RELEASE_PLAN"),
        metavar="NAME",
        help="Target a specific ReleasePlan instead of resolving from the snapshot's application",
    )
    parser.add_argument(
        "--artifact-type",
        choices=list(config.ARTIFACT_CONFIGS),
        default=os.environ.get("LIGHTWELL_ARTIFACT_TYPE", "STAGE"),
        help="Artifact type (default: STAGE, or LIGHTWELL_ARTIFACT_TYPE env var)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report actions without mutating DB or cluster",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Watch and reconcile running imports/releases repeatedly (default: one pass)",
    )
    parser.set_defaults(func=run, ecosystem=ecosystem)


def run(args: argparse.Namespace) -> int:
    with ImportDatabase(args.db) as db:
        kube = KubeClient(args.ecosystem.namespace, CLUSTER_API, KUBEARCHIVE_API)
        application = args.ecosystem.snapshot_application(args)
        return ReleaseOnly(
            db,
            kube,
            args.ecosystem.pipelinerun_prefix,
            args.max_parallel,
            args.release_plan,
            expected_application=application,
            import_snapshot_resolver=getattr(args.ecosystem, "import_snapshot_resolver", False),
        ).run(args.dry_run, poll_interval=args.poll_interval)
