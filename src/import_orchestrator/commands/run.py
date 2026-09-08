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
import shutil
import sys
import tempfile
from pathlib import Path

from import_orchestrator.clients import KubeClient
from import_orchestrator.constants import CLUSTER_API, KUBEARCHIVE_API
from import_orchestrator.database import ImportDatabase
from import_orchestrator.engine import (
    ImportOrchestrator,
    ImportTrigger,
    Ingest,
    PipelineMonitor,
    ReleaseMonitor,
)


def _resolve_db(args: argparse.Namespace) -> tuple[Path, str | None]:
    """Resolve the state database path for a single-ref orchestration run.

    When the user supplied ``--db`` explicitly, that database is used as-is and
    persists across the run (enabling resume/inspection). Otherwise an ephemeral
    database is created in a fresh temporary directory, isolated from the shared
    per-ecosystem database so the monitoring loop only ever sees the one ref.

    Returns:
        A ``(db_path, cleanup_dir)`` tuple. ``cleanup_dir`` is the temporary
        directory to remove after the run, or ``None`` for a persistent database.
    """
    if getattr(args, "db_explicit", False):
        return args.db, None

    tmp_dir = tempfile.mkdtemp(prefix="import-run-")
    return Path(tmp_dir) / "state.db", tmp_dir


def run_single(args: argparse.Namespace, ref: str) -> int:
    """Orchestrate a single ref end-to-end: seed, trigger, monitor, retry.

    Unlike ``trigger`` (fire-and-forget), this seeds ``ref`` into a state
    database and runs the full ``ImportOrchestrator`` loop until the import
    reaches a terminal state, applying retries on failure. Generic across
    ecosystems: the ecosystem supplies its own namespace, PipelineRun prefix
    and manifest builder.

    Returns:
        Exit code: 0 if the import succeeded, 1 if it failed.
    """
    db_path, cleanup_dir = _resolve_db(args)
    try:
        with ImportDatabase(db_path) as db:
            Ingest(db).from_lines([ref])

            eco = args.ecosystem
            kube = KubeClient(eco.namespace, CLUSTER_API, KUBEARCHIVE_API)

            trigger = ImportTrigger(
                db=db,
                kube=kube,
                build_pipelinerun=lambda r: eco.build_pipelinerun(r, args),
                max_parallel=1,
                max_retries=args.max_retries,
            )
            pipeline_monitor = PipelineMonitor(db=db, kube=kube)
            release_monitor = ReleaseMonitor(db=db, kube=kube, max_parallel=1, prefix=eco.pipelinerun_prefix)

            orchestrator = ImportOrchestrator(
                db=db,
                trigger=trigger,
                pipeline_monitor=pipeline_monitor,
                release_monitor=release_monitor,
                poll_interval=args.poll_interval,
                max_retries=args.max_retries,
            )

            return orchestrator.run_until_complete()
    finally:
        if cleanup_dir is not None:
            shutil.rmtree(cleanup_dir, ignore_errors=True)
            print(f"Removed ephemeral database: {cleanup_dir}", file=sys.stderr)
