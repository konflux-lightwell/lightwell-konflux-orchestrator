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
import time

from import_orchestrator.database import ImportDatabase
from import_orchestrator.engine.pipeline import PipelineMonitor
from import_orchestrator.engine.release import ReleaseMonitor
from import_orchestrator.engine.trigger import ImportTrigger
from import_orchestrator.models import ImportStatus


class ImportOrchestrator:
    """Coordinates the import lifecycle by delegating to specialized components.

    This is a thin coordinator that manages the polling loop and delegates
    to ImportTrigger, PipelineMonitor, and ReleaseMonitor for the actual work.
    """

    def __init__(
        self,
        db: ImportDatabase,
        trigger: ImportTrigger,
        pipeline_monitor: PipelineMonitor,
        release_monitor: ReleaseMonitor,
        poll_interval: int,
        max_retries: int,
    ):
        self.db = db
        self.poll_interval = poll_interval
        self.max_retries = max_retries
        self._trigger = trigger
        self._pipeline_monitor = pipeline_monitor
        self._release_monitor = release_monitor

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
        return self._trigger.trigger_next_batch()

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
