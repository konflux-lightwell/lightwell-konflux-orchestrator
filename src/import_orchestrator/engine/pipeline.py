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

import sys
from datetime import datetime

from import_orchestrator.clients import KubeClient
from import_orchestrator.database import ImportDatabase
from import_orchestrator.models import ImportStatus
from import_orchestrator.utils import extract_tag


class PipelineMonitor:
    """Monitors PipelineRun status and updates the database accordingly.

    Polls Kubernetes for PipelineRun statuses and manages the
    TRIGGERED -> RUNNING -> AWAITING_RELEASE/FAILED transitions.
    """

    def __init__(self, db: ImportDatabase, kube: KubeClient):
        self.db = db
        self.kube = kube

    def update_statuses(self) -> None:
        """Check status of all triggered/running imports and update the database."""
        to_check = self.db.get_by_status(ImportStatus.TRIGGERED) + self.db.get_by_status(ImportStatus.RUNNING)

        for item in to_check:
            if not item.pipelinerun_name or item.id is None:
                continue

            pr_status = self.kube.get_pipelinerun_status(item.pipelinerun_name)

            if pr_status is None:
                continue

            tag = extract_tag(item.ref)

            if pr_status.is_running:
                if item.status == ImportStatus.TRIGGERED:
                    self.db.update_status(item.id, ImportStatus.RUNNING)
                    print(f"  Running: {tag}", file=sys.stderr)
            elif pr_status.is_successful:
                self.db.update_status(item.id, ImportStatus.AWAITING_RELEASE)
                print(f"  Pipeline done, awaiting release: {tag}", file=sys.stderr)
            elif pr_status.is_failed:
                self.db.update_status(
                    item.id,
                    ImportStatus.FAILED,
                    completed_at=datetime.now(),
                    error_message="PipelineRun failed",
                )
                print(f"  ✗ Failed: {tag}", file=sys.stderr)
