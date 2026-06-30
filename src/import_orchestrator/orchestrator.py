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
                self.db.update_status(oci_ref.id, ImportStatus.AWAITING_RELEASE)
                print(f"  Pipeline done, awaiting release: {tag}", file=sys.stderr)
            elif pr_status.is_failed:
                self.db.update_status(
                    oci_ref.id,
                    ImportStatus.FAILED,
                    completed_at=datetime.now(),
                    error_message="PipelineRun failed",
                )
                print(f"  ✗ Failed: {tag}", file=sys.stderr)

    def update_release_statuses(self) -> None:
        """For AWAITING_RELEASE imports, find the Release and check its status."""
        for oci_ref in self.db.get_by_status(ImportStatus.AWAITING_RELEASE):
            if oci_ref.id is None or not oci_ref.pipelinerun_name:
                continue

            tag = extract_tag(oci_ref.oci_ref)
            release_name = oci_ref.release_name
            snapshot_name = oci_ref.snapshot_name

            if not release_name:
                if not snapshot_name:
                    # First time seeing this entry — discover the snapshot by the build-pipelinerun label.
                    # Konflux Integration Service always sets this label, so no digest fallback needed.
                    snapshot_name = self.kube.find_snapshot_by_pipelinerun(oci_ref.pipelinerun_name)
                    if not snapshot_name:
                        print(f"  Waiting for snapshot for {tag}...", file=sys.stderr)
                        continue
                    # Cache snapshot_name; check for a release next poll to give Integration Service time
                    self.db.update_status(oci_ref.id, ImportStatus.AWAITING_RELEASE, snapshot_name=snapshot_name)
                    print(f"  Found snapshot {snapshot_name} for {tag}, checking for release next poll", file=sys.stderr)
                    continue

                # snapshot_name already cached — check auto-release status for logging only,
                # then find or create a release for our snapshot regardless
                release_name = self.kube.find_release_for_snapshot(snapshot_name)
                if not release_name:
                    release_plan = self.kube.find_release_plan_for_snapshot(snapshot_name)
                    if not release_plan:
                        print(f"  No ReleasePlan found for {snapshot_name} ({tag}), will retry", file=sys.stderr)
                        continue
                    print(f"  No release found for {snapshot_name}, creating via {release_plan} ({tag})...", file=sys.stderr)
                    release_name = self.kube.create_release(snapshot_name, release_plan)
                if not release_name:
                    print(f"  Failed to create release for {snapshot_name} ({tag}), will retry", file=sys.stderr)
                    continue
                self.db.update_status(oci_ref.id, ImportStatus.AWAITING_RELEASE, release_name=release_name)
                print(f"  Tracking release/{release_name} ({tag})", file=sys.stderr)

            release_status = self.kube.get_release_status(release_name)
            if release_status == "True":
                self.db.update_status(oci_ref.id, ImportStatus.SUCCESS, completed_at=datetime.now())
                print(f"  ✓ Released: {tag} (release/{release_name})", file=sys.stderr)
            elif release_status == "False":
                self.db.update_status(
                    oci_ref.id, ImportStatus.FAILED,
                    completed_at=datetime.now(),
                    error_message=f"Release {release_name} failed",
                )
                print(f"  ✗ Release failed: {tag} (release/{release_name})", file=sys.stderr)
            else:
                print(f"  Waiting for release/{release_name} ({tag})...", file=sys.stderr)

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
            f"awaiting_release={stats[ImportStatus.AWAITING_RELEASE.value]}, "
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
