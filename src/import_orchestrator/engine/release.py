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
from import_orchestrator.models import ImportItem, ImportStatus
from import_orchestrator.utils import extract_tag


class ReleaseMonitor:
    """Manages the release lifecycle for completed PipelineRuns.

    Discovers snapshots, finds or creates releases, and tracks release status
    to manage the AWAITING_RELEASE -> SUCCESS/FAILED transitions.
    """

    def __init__(self, db: ImportDatabase, kube: KubeClient, max_parallel: int, prefix: str):
        self.db = db
        self.kube = kube
        self.max_parallel = max_parallel
        self.prefix = prefix

    def update_statuses(self) -> None:
        """For AWAITING_RELEASE imports, find the Release and check its status."""
        releasing = self.db.get_by_status(ImportStatus.AWAITING_RELEASE)

        # Count how many releases are already actively tracked this cycle.
        # Only create new releases up to max_parallel to avoid flooding the release pipeline.
        active_releases = sum(1 for r in releasing if r.release_name)

        for item in releasing:
            if item.id is None or not item.pipelinerun_name:
                continue

            tag = extract_tag(item.ref)

            # Step 1: Discover snapshot if not yet cached
            if item.snapshot_name is None or item.snapshot_name == "":
                if self._discover_snapshot(item, tag):
                    continue  # Snapshot found and cached, check for release next poll
                else:
                    continue  # Snapshot not found, skip this cycle

            # Step 2: Find or create a release if not yet cached
            if item.release_name is None or item.release_name == "":
                can_create = active_releases < self.max_parallel
                release_name = self._find_or_create_release(item, tag, can_create)
                if release_name:
                    active_releases += 1
                    # Release just created/found, wait for next poll to check status
                    continue
                else:
                    continue  # Will retry next poll

            # Step 3: Check release completion status
            self._check_release_completion(item, tag)

    def _discover_snapshot(self, item: ImportItem, tag: str) -> bool:
        """Discover and cache the snapshot for a PipelineRun.

        Returns:
            True if snapshot was found and cached (caller should continue to next iteration),
            False if snapshot discovery failed (caller should skip this item this cycle).
        """
        assert item.id is not None
        assert item.pipelinerun_name is not None

        # Konflux Integration Service always sets the build-pipelinerun label
        snapshot_name = self.kube.find_snapshot_by_pipelinerun(item.pipelinerun_name)
        if not snapshot_name:
            print(f"  Waiting for snapshot for {tag}...", file=sys.stderr)
            return False

        # Cache snapshot_name; check for a release next poll to give Integration Service time
        self.db.update_status(item.id, ImportStatus.AWAITING_RELEASE, snapshot_name=snapshot_name)
        print(
            f"  Found snapshot {snapshot_name} for {tag}, checking for release next poll",
            file=sys.stderr,
        )
        return True

    def _find_or_create_release(self, item: ImportItem, tag: str, can_create: bool) -> str | None:
        """Find an existing release or create a new one for the snapshot.

        Args:
            item: The import item with a cached snapshot_name.
            tag: The extracted tag for logging.
            can_create: Whether we're allowed to create a new release (respects max_parallel).

        Returns:
            The release name if found or created, None if creation was deferred or failed.
        """
        assert item.id is not None
        assert item.snapshot_name is not None

        # Check if a release already exists for this snapshot
        release_name = self.kube.find_release_for_snapshot(item.snapshot_name)
        if release_name:
            self.db.update_status(item.id, ImportStatus.AWAITING_RELEASE, release_name=release_name)
            print(f"  Tracking release/{release_name} ({tag})", file=sys.stderr)
            return release_name

        # No existing release - need to create one
        if not can_create:
            active_count = sum(1 for r in self.db.get_by_status(ImportStatus.AWAITING_RELEASE) if r.release_name)
            print(
                f"  Release capacity full ({active_count}/{self.max_parallel}), deferring {tag}",
                file=sys.stderr,
            )
            return None

        # Find the ReleasePlan for this snapshot
        release_plan = self.kube.find_release_plan_for_snapshot(item.snapshot_name)
        if not release_plan:
            print(f"  No ReleasePlan found for {item.snapshot_name} ({tag}), will retry", file=sys.stderr)
            return None

        # Create the release
        print(
            f"  No release found for {item.snapshot_name}, creating via {release_plan} ({tag})...",
            file=sys.stderr,
        )
        release_name = self.kube.create_release(item.snapshot_name, release_plan, self.prefix)

        if not release_name:
            print(f"  Failed to create release for {item.snapshot_name} ({tag}), will retry", file=sys.stderr)
            return None

        self.db.update_status(item.id, ImportStatus.AWAITING_RELEASE, release_name=release_name)
        print(f"  Tracking release/{release_name} ({tag})", file=sys.stderr)
        return release_name

    def _check_release_completion(self, item: ImportItem, tag: str) -> None:
        """Poll release status and record completion or failure.

        Args:
            item: The import item with a cached release_name.
            tag: The extracted tag for logging.
        """
        assert item.id is not None
        assert item.release_name is not None

        release_status = self.kube.get_release_status(item.release_name)
        if release_status == "True":
            self.db.update_status(item.id, ImportStatus.SUCCESS, completed_at=datetime.now())
            print(f"  ✓ Released: {tag} (release/{item.release_name})", file=sys.stderr)
        elif release_status == "False":
            self.db.update_status(
                item.id,
                ImportStatus.FAILED,
                completed_at=datetime.now(),
                error_message=f"Release {item.release_name} failed",
            )
            print(f"  ✗ Release failed: {tag} (release/{item.release_name})", file=sys.stderr)
        else:
            print(f"  Waiting for release/{item.release_name} ({tag})...", file=sys.stderr)
