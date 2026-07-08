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

from import_orchestrator.database import ImportDatabase
from import_orchestrator.engine.pipelinerun import PipelineRunBuilder, TriggerError
from import_orchestrator.models import ImportStatus, OCIReference
from import_orchestrator.utils import extract_tag


class ImportTrigger:
    """Triggers PNC import PipelineRuns and manages the PENDING -> TRIGGERED transition.

    Handles triggering imports via PipelineRunBuilder, including retry logic
    and capacity management.
    """

    def __init__(
        self,
        db: ImportDatabase,
        builder: PipelineRunBuilder,
        max_parallel: int,
        max_retries: int,
    ):
        self.db = db
        self.builder = builder
        self.max_parallel = max_parallel
        self.max_retries = max_retries

    def trigger_import(self, oci_ref: OCIReference, tag: str) -> str | None:
        """Trigger an import via PipelineRunBuilder, returning the PipelineRun name.

        Returns:
            The PipelineRun name from the created PipelineRun,
            or None if the name could not be parsed.

        Raises:
            TriggerError: If the PipelineRun creation fails.
        """
        return self.builder.trigger(source_image=oci_ref.oci_ref, tag=tag)

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
        for oci_ref in candidates:
            if oci_ref.id is None:
                continue

            tag = extract_tag(oci_ref.oci_ref)
            triggered += self._trigger_single_import(oci_ref, tag)

        return triggered

    def _trigger_single_import(self, oci_ref: OCIReference, tag: str) -> int:
        """Attempt to trigger a single import. Returns 1 on success, 0 on failure."""
        assert oci_ref.id is not None

        try:
            pr_name = self.trigger_import(oci_ref, tag)
            new_retry_count = oci_ref.retry_count + 1 if oci_ref.status == ImportStatus.FAILED else 0

            self.db.update_status(
                oci_ref.id,
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
            self._handle_trigger_failure(oci_ref, tag, str(e))
            return 0

    def _handle_trigger_failure(
        self,
        oci_ref: OCIReference,
        tag: str,
        error_msg: str,
    ) -> None:
        """Record a trigger failure in the database with appropriate retry semantics."""
        assert oci_ref.id is not None

        # TriggerError failures are generally transient (network, credentials)
        # so we allow retries unless max_retries is already reached
        self.db.update_status(
            oci_ref.id,
            ImportStatus.FAILED,
            error_message=f"PipelineRun trigger failed: {error_msg}",
            retry_count=oci_ref.retry_count + 1,
        )

        print(
            f"  ERROR: Failed to trigger {tag}: {error_msg[:100]}",
            file=sys.stderr,
        )
