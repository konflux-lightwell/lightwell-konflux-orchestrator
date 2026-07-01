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

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from import_orchestrator.database import ImportDatabase
from import_orchestrator.models import ImportStatus, OCIReference
from import_orchestrator.utils import extract_tag, should_retry


class ImportTrigger:
    """Executes trigger scripts and manages the PENDING -> TRIGGERED transition.

    Handles triggering imports via external scripts, including retry logic
    and capacity management.
    """

    def __init__(
        self,
        db: ImportDatabase,
        trigger_script: Path,
        max_parallel: int,
        max_retries: int,
    ):
        self.db = db
        self.trigger_script = trigger_script
        self.max_parallel = max_parallel
        self.max_retries = max_retries

    def trigger_import(self, oci_ref: OCIReference) -> str | None:
        """Trigger an import via the trigger script, returning the PipelineRun name.

        Returns:
            The PipelineRun name extracted from the trigger script output,
            or None if the name could not be parsed.

        Raises:
            subprocess.CalledProcessError: If the trigger script exits with a non-zero code.
        """
        result = subprocess.run(
            [str(self.trigger_script), oci_ref.oci_ref],
            capture_output=True,
            check=True,
            text=True,
        )

        combined_output = result.stdout + result.stderr
        match = re.search(r"pipelinerun\.tekton\.dev/(\S+)\s+created", combined_output)

        if match:
            return match.group(1)

        print(
            f"WARNING: Could not extract PipelineRun name from trigger output for {oci_ref.oci_ref}",
            file=sys.stderr,
        )
        return None

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
            pr_name = self.trigger_import(oci_ref)
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

        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.decode() if isinstance(e.stderr, bytes) else str(e.stderr)
            self._handle_trigger_failure(oci_ref, tag, e, error_msg)
            return 0

    def _handle_trigger_failure(
        self,
        oci_ref: OCIReference,
        tag: str,
        error: subprocess.CalledProcessError,
        error_msg: str,
    ) -> None:
        """Record a trigger failure in the database with appropriate retry semantics."""
        assert oci_ref.id is not None

        if should_retry(error):
            self.db.update_status(
                oci_ref.id,
                ImportStatus.FAILED,
                error_message=f"Trigger script failed (will retry): {error_msg}",
                retry_count=oci_ref.retry_count + 1,
            )
        else:
            self.db.update_status(
                oci_ref.id,
                ImportStatus.FAILED,
                error_message=f"Trigger script failed (permanent): {error_msg}",
                retry_count=self.max_retries,
            )

        print(
            f"  ERROR: Failed to trigger {tag}: {error_msg[:100]}",
            file=sys.stderr,
        )
