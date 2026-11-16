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
from typing import Any

KUEUE_QUEUE_LABEL = "kueue.x-k8s.io/queue-name"

# Tekton PipelineRun spec.status value that holds the run in pending until
# the admission controller (Kueue) clears it. Empty string means "run
# immediately" which is the current/Tekton default.
PIPELINERUN_PENDING_STATUS = "PipelineRunPending"


def add_kueue_args(parser: argparse.ArgumentParser) -> None:
    """Add the shared ``--kueue-queue-name`` flag to a parser.

    When set, the orchestrator stamps every PipelineRun it creates with the
    Kueue queue label and ``spec.status: PipelineRunPending`` so Kueue can
    admit the workload before Tekton starts TaskRuns. Unset by default to keep
    existing (non-Kueue) clusters working: a pending run without a Kueue
    controller would never start.
    """
    parser.add_argument(
        "--kueue-queue-name",
        default=os.environ.get("KUEUE_QUEUE_NAME", None),
        metavar="QUEUE",
        help=(
            "Kueue LocalQueue to submit PipelineRuns to. When set, each "
            "PipelineRun is created with label kueue.x-k8s.io/queue-name and "
            "spec.status=PipelineRunPending so Kueue admits it before it runs. "
            "Unset (default) creates runs immediately. Also set via $KUEUE_QUEUE_NAME."
        ),
    )


def apply_kueue_scheduling(manifest: dict[str, Any], queue_name: str | None) -> dict[str, Any]:
    """Stamp a PipelineRun manifest for Kueue scheduling, if a queue is given.

    Mutates ``manifest`` in-place for efficiency and returns it. When
    ``queue_name`` is falsy (None/empty/whitespace), the manifest is returned
    unchanged so non-Kueue clusters are unaffected.
    """
    if not queue_name or not queue_name.strip():
        return manifest
    queue_name = queue_name.strip()

    labels = manifest.setdefault("metadata", {}).setdefault("labels", {})
    labels[KUEUE_QUEUE_LABEL] = queue_name

    spec = manifest.setdefault("spec", {})
    spec["status"] = PIPELINERUN_PENDING_STATUS

    return manifest
