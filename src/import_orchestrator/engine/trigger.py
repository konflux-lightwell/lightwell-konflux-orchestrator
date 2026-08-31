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
from collections.abc import Callable
from datetime import datetime

from import_orchestrator.clients.kube import KubeClient
from import_orchestrator.database import ImportDatabase
from import_orchestrator.engine.errors import PipelineRunReconciliationError, PipelineRunRetryableError, TriggerError
from import_orchestrator.models import ImportItem, ImportStatus
from import_orchestrator.utils import extract_tag


def _attempt_for_item(item: ImportItem) -> int:
    return item.retry_count + 1 if item.status == ImportStatus.FAILED else 0


def _failure_persistence(item: ImportItem, error: TriggerError, max_retries: int) -> tuple[str | None, int]:
    if isinstance(error, PipelineRunRetryableError):
        return None, item.retry_count + 1
    if isinstance(error, PipelineRunReconciliationError):
        return error.name, max_retries
    return None, max_retries


class ImportTrigger:
    """Triggers PNC import PipelineRuns and manages the PENDING -> TRIGGERED transition.

    The manifest for each import is built by the injected `build_pipelinerun` callable,
    which maps a ref string to a PipelineRun dict, so the trigger logic is ecosystem-neutral.
    """

    def __init__(
        self,
        db: ImportDatabase,
        kube: KubeClient,
        build_pipelinerun: Callable[[str, int], dict],
        max_parallel: int,
        max_retries: int,
    ):
        self.db = db
        self.kube = kube
        self.build_pipelinerun = build_pipelinerun
        self.max_parallel = max_parallel
        self.max_retries = max_retries

    def trigger_import(self, item: ImportItem, attempt: int = 0) -> str | None:
        """Build and submit a PipelineRun for the given import item.

        Returns:
            The generated PipelineRun name.

        Raises:
            TriggerError: If the manifest build or PipelineRun creation fails.
        """
        manifest = self.build_pipelinerun(item.ref, attempt)
        pr_name = self.kube.create_pipelinerun(manifest)
        if pr_name is None:
            raise TriggerError("PipelineRun creation failed (API returned no name)")
        return pr_name

    def trigger_next_batch(self) -> int:
        """Trigger imports up to the max_parallel limit, counting all in-flight stages.

        Returns:
            The number of imports successfully triggered.
        """
        in_flight = self.db.count_in_flight()
        available_slots = max(0, self.max_parallel - in_flight)

        if available_slots == 0:
            return 0

        pending = self.db.get_by_status(ImportStatus.PENDING)
        retry_candidates = self.db.get_retry_candidates(self.max_retries)
        candidates = (pending + retry_candidates)[:available_slots]

        triggered = 0
        for item in candidates:
            if item.id is None:
                continue
            triggered += self._trigger_single_import(item)

        return triggered

    def _trigger_single_import(self, item: ImportItem) -> int:
        """Attempt to trigger a single import. Returns 1 on success, 0 on failure."""
        assert item.id is not None

        tag = extract_tag(item.ref)  # used only for log messages
        attempt = _attempt_for_item(item)

        try:
            pr_name = self.trigger_import(item, attempt)
            new_retry_count = item.retry_count + 1 if item.status == ImportStatus.FAILED else 0

            self.db.update_status(
                item.id,
                ImportStatus.TRIGGERED,
                pipelinerun_name=pr_name,
                snapshot_name="",  # clear cached snapshot/release from a prior attempt
                release_name="",
                triggered_at=datetime.now(),
                retry_count=new_retry_count,
            )

            retry_indicator = f" (retry {new_retry_count})" if new_retry_count > 0 else ""
            print(f"  Triggered: {tag}{retry_indicator}", file=sys.stderr)
            return 1

        except TriggerError as e:
            self._handle_trigger_failure(item, tag, e)
            return 0

    def _handle_trigger_failure(
        self,
        item: ImportItem,
        tag: str,
        error: TriggerError,
    ) -> None:
        """Record a trigger failure in the database with appropriate retry semantics."""
        assert item.id is not None
        pipelinerun_name, retry_count = _failure_persistence(item, error, self.max_retries)

        self.db.update_status(
            item.id,
            ImportStatus.FAILED,
            pipelinerun_name=pipelinerun_name,
            error_message=f"PipelineRun trigger failed: {error}",
            retry_count=retry_count,
        )

        print(
            f"  ERROR: Failed to trigger {tag}: {str(error)[:100]}",
            file=sys.stderr,
        )
