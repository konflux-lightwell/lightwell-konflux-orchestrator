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
import time
from datetime import datetime
from pathlib import Path

from import_orchestrator.database import ImportDatabase
from import_orchestrator.engine.pipeline import PipelineMonitor
from import_orchestrator.engine.release import ReleaseMonitor
from import_orchestrator.kube import KubeClient
from import_orchestrator.models import ImportStatus, OCIReference
from import_orchestrator.utils import extract_tag, should_retry


class ImportOrchestrator:
    """Coordinates triggering, monitoring, and retrying PNC imports.

    Manages the lifecycle of OCI reference imports by:
    - Triggering PipelineRuns up to a configurable parallelism limit
    - Polling for completion and updating the database
    - Retrying transient failures
    """

    def __init__(
        self,
        db: ImportDatabase,
        kube: KubeClient,
        trigger_script: Path,
        max_parallel: int,
        poll_interval: int,
        max_retries: int,
    ):
        self.db = db
        self.kube = kube
        self.trigger_script = trigger_script
        self.max_parallel = max_parallel
        self.poll_interval = poll_interval
        self.max_retries = max_retries
        self._pipeline_monitor = PipelineMonitor(db, kube)
        self._release_monitor = ReleaseMonitor(db, kube, max_parallel)

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

    def update_pipelinerun_statuses(self) -> None:
        """Check status of all triggered/running imports and update the database."""
        self._pipeline_monitor.update_statuses()

    def update_release_statuses(self) -> None:
        """For AWAITING_RELEASE imports, find the Release and check its status."""
        self._release_monitor.update_statuses()

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

    def is_complete(self) -> bool:
        """Check if all imports are either successful or permanently failed."""
        stats = self.db.get_statistics()
        incomplete = (
            stats.get(ImportStatus.PENDING.value, 0)
            + stats.get(ImportStatus.TRIGGERED.value, 0)
            + stats.get(ImportStatus.RUNNING.value, 0)
            + stats.get(ImportStatus.AWAITING_RELEASE.value, 0)
        )
        retryable = len(self.db.get_retry_candidates(self.max_retries))
        return incomplete == 0 and retryable == 0

    def _print_statistics(self, stats: dict[str, int]) -> None:
        """Print current import statistics to stderr."""
        print(
            f"Status: pending={stats[ImportStatus.PENDING.value]}, "
            f"triggered={stats[ImportStatus.TRIGGERED.value]}, "
            f"running={stats[ImportStatus.RUNNING.value]}, "
            f"releasing={stats[ImportStatus.AWAITING_RELEASE.value]}, "
            f"success={stats[ImportStatus.SUCCESS.value]}, "
            f"failed={stats[ImportStatus.FAILED.value]}",
            file=sys.stderr,
        )

    def run_until_complete(self) -> int:
        """Main loop: trigger batches and monitor until all imports complete.

        Returns:
            Exit code: 0 if all imports succeeded, 1 if any failed.
        """
        iteration = 0

        while not self.is_complete():
            iteration += 1
            print(f"\n=== Iteration {iteration} ===", file=sys.stderr)

            self.update_pipelinerun_statuses()
            self.update_release_statuses()

            triggered = self.trigger_next_batch()
            if triggered > 0:
                print(f"Triggered {triggered} new import(s)", file=sys.stderr)

            self._print_statistics(self.db.get_statistics())

            if not self.is_complete():
                print(f"Sleeping {self.poll_interval}s...", file=sys.stderr)
                time.sleep(self.poll_interval)

        final_stats = self.db.get_statistics()
        print("\n=== Complete ===", file=sys.stderr)
        self._print_statistics(final_stats)

        return 0 if final_stats.get(ImportStatus.FAILED.value, 0) == 0 else 1
