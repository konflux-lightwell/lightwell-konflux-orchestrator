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
from import_orchestrator.kube import KubeClient
from import_orchestrator.models import ImportStatus, OCIReference
from import_orchestrator.utils import extract_tag, should_retry


class ImportOrchestrator:
    """Coordinates fetching, triggering, monitoring, and retrying PNC imports.

    Manages the lifecycle of OCI reference imports by:
    - Fetching references via an external script
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

    def fetch_and_store_oci_refs(self, fetch_script: Path) -> tuple[int, int]:
        """Run the fetch script and store discovered OCI references in the database.

        Returns:
            Tuple of (total_fetched, newly_added) counts.
        """
        try:
            result = subprocess.run(
                [str(fetch_script)],
                capture_output=True,
                check=True,
                text=True,
            )

            oci_refs = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]

            if not oci_refs:
                print("WARNING: No OCI references returned from fetch script", file=sys.stderr)
                return 0, 0

            newly_added = 0
            for oci_ref in oci_refs:
                _, was_inserted = self.db.add_oci_reference(oci_ref)
                if was_inserted:
                    newly_added += 1

            return len(oci_refs), newly_added

        except subprocess.CalledProcessError as e:
            print(f"ERROR: Fetch script failed: {e.stderr}", file=sys.stderr)
            raise

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
        to_check = self.db.get_by_status(ImportStatus.TRIGGERED) + self.db.get_by_status(ImportStatus.RUNNING)

        for oci_ref in to_check:
            if not oci_ref.pipelinerun_name or oci_ref.id is None:
                continue

            pr_status = self.kube.get_pipelinerun_status(oci_ref.pipelinerun_name)

            if pr_status is None:
                continue

            tag = extract_tag(oci_ref.oci_ref)

            if pr_status.is_running:
                if oci_ref.status == ImportStatus.TRIGGERED:
                    self.db.update_status(oci_ref.id, ImportStatus.RUNNING)
                    print(f"  Running: {tag}", file=sys.stderr)
            elif pr_status.is_successful:
                self.db.update_status(
                    oci_ref.id,
                    ImportStatus.SUCCESS,
                    completed_at=datetime.now(),
                )
                print(f"  ✓ Success: {tag}", file=sys.stderr)
            elif pr_status.is_failed:
                self.db.update_status(
                    oci_ref.id,
                    ImportStatus.FAILED,
                    completed_at=datetime.now(),
                    error_message="PipelineRun failed",
                )
                print(f"  ✗ Failed: {tag}", file=sys.stderr)

    def trigger_next_batch(self) -> int:
        """Trigger imports up to the max_parallel limit.

        Returns:
            The number of imports successfully triggered.
        """
        running_count = self.kube.count_running_imports()
        available_slots = max(0, self.max_parallel - running_count)

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
        )
        retryable = len(self.db.get_retry_candidates(self.max_retries))
        return incomplete == 0 and retryable == 0

    def _print_statistics(self, stats: dict[str, int]) -> None:
        """Print current import statistics to stderr."""
        print(
            f"Status: pending={stats[ImportStatus.PENDING.value]}, "
            f"triggered={stats[ImportStatus.TRIGGERED.value]}, "
            f"running={stats[ImportStatus.RUNNING.value]}, "
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
