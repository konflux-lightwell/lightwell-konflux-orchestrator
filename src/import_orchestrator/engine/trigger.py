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
import sys
from collections.abc import Callable
from datetime import datetime

from import_orchestrator.clients.kube import KubeClient
from import_orchestrator.database import ImportDatabase
from import_orchestrator.engine.errors import TriggerError
from import_orchestrator.models import (
    ImportItem,
    ImportStatus,
    SnapshotLookup,
    SnapshotLookupState,
)
from import_orchestrator.utils import extract_tag


class ImportTrigger:
    """Triggers PNC import PipelineRuns and manages the PENDING -> TRIGGERED transition.

    The manifest for each import is built by the injected `build_pipelinerun` callable,
    which maps a ref string to a PipelineRun dict, so the trigger logic is ecosystem-neutral.
    """

    def __init__(
        self,
        db: ImportDatabase,
        kube: KubeClient,
        build_pipelinerun: Callable[[str], dict],
        max_parallel: int,
        max_retries: int,
        force_import: bool = False,
        expected_application: str | None = None,
        import_snapshot_resolver: bool = False,
    ):
        if max_parallel <= 0:
            raise ValueError("max_parallel must be positive")
        self.db = db
        self.kube = kube
        self.build_pipelinerun = build_pipelinerun
        self.max_parallel = max_parallel
        self.max_retries = max_retries
        self.force_import = force_import
        self.expected_application = expected_application
        self.import_snapshot_resolver = import_snapshot_resolver

    def trigger_import(self, item: ImportItem) -> str | None:
        """Build and submit a PipelineRun for the given import item.

        Returns:
            The generated PipelineRun name.

        Raises:
            TriggerError: If the manifest build or PipelineRun creation fails.
        """
        manifest = self.build_pipelinerun(item.ref)
        pr_name = self.kube.create_pipelinerun(manifest)
        if pr_name is None:
            raise TriggerError("PipelineRun creation failed (API returned no name)")
        return pr_name

    def trigger_next_batch(self) -> int:
        """Trigger imports up to the max_parallel limit, counting all in-flight stages.

        Returns:
            The number of imports successfully triggered.
        """
        pending = self.db.get_by_status(ImportStatus.PENDING)
        retry_candidates = self.db.get_retry_candidates(self.max_retries)
        candidates = pending + retry_candidates
        if not self.force_import:
            # Java imports must resolve through the source-matching import PLR;
            # component-only lookup is unsafe because Java components are shared.
            resolver = getattr(self.kube, "find_snapshot_for_import", None)
            if self.import_snapshot_resolver and callable(resolver):
                for item in candidates[:]:
                    if item.id is None:
                        continue
                    try:
                        lookup = resolver(item.ref, self.expected_application)
                    except TypeError:
                        lookup = resolver(item.ref)
                    if isinstance(lookup, str) and lookup:
                        lookup = SnapshotLookup(SnapshotLookupState.FOUND, lookup)
                    if isinstance(lookup, SnapshotLookup) and lookup.state is SnapshotLookupState.FOUND and lookup.name:
                        self.db.update_status(item.id, ImportStatus.AWAITING_RELEASE, snapshot_name=lookup.name)
                        candidates.remove(item)
                    elif isinstance(lookup, SnapshotLookup) and lookup.state is SnapshotLookupState.CONFIRMED_EMPTY:
                        # Java components are shared, so a component-digest miss
                        # cannot be used here. A confirmed *source* miss means
                        # this exact source has not been imported and must enter
                        # the normal fresh PipelineRun path below.
                        pass
                    else:
                        self.db.update_status(
                            item.id,
                            ImportStatus.PENDING,
                            error_message=(
                                "Import PipelineRun/Snapshot lookup unavailable; refusing to trigger an import"
                            ),
                        )
                        candidates.remove(item)
            # Snapshot reuse is admission, not an import: perform it before capacity
            # gating so a full import pool cannot cause a duplicate PipelineRun.
            finder = (
                None if self.import_snapshot_resolver else getattr(self.kube, "find_snapshot_by_component_digest", None)
            )
            for item in candidates[:]:
                digest = item.ref.rsplit("@", 1)[-1] if "@" in item.ref else ""
                # Only query reuse for a structurally valid canonical digest. A
                # malformed ref is not evidence that no matching Snapshot exists.
                if not callable(finder) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                    continue
                try:
                    lookup = finder(digest, self.expected_application)
                except TypeError:
                    lookup = finder(digest)
                if isinstance(lookup, str) and lookup:
                    # Explicit legacy contract: a returned name is FOUND.
                    lookup = SnapshotLookup(SnapshotLookupState.FOUND, lookup)
                elif lookup is None or not isinstance(lookup, SnapshotLookup):
                    # Legacy misses and malformed adapter responses are not proof
                    # of absence.  Fail closed rather than creating a duplicate.
                    lookup = SnapshotLookup(SnapshotLookupState.UNKNOWN)
                elif lookup.state is SnapshotLookupState.FOUND and not lookup.name:
                    lookup = SnapshotLookup(SnapshotLookupState.UNKNOWN)
                if item.id is None:
                    continue
                if lookup.state is SnapshotLookupState.FOUND and lookup.name:
                    self.db.update_status(item.id, ImportStatus.AWAITING_RELEASE, snapshot_name=lookup.name)
                    candidates.remove(item)
                elif lookup.state in (SnapshotLookupState.AMBIGUOUS, SnapshotLookupState.UNKNOWN):
                    reason = "ambiguous" if lookup.state is SnapshotLookupState.AMBIGUOUS else "unavailable"
                    self.db.update_status(
                        item.id,
                        ImportStatus.PENDING,
                        error_message=(
                            f"Snapshot lookup {reason}; refusing to trigger an import. "
                            "Retry after verifying the Snapshot API response."
                        ),
                    )
                    candidates.remove(item)

        in_flight = self.db.count_in_flight()
        available_slots = max(0, self.max_parallel - in_flight)
        if available_slots == 0:
            return 0
        candidates = candidates[:available_slots]

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

        try:
            pr_name = self.trigger_import(item)
            new_retry_count = item.retry_count + 1 if item.status == ImportStatus.FAILED else 0

            self.db.update_status(
                item.id,
                ImportStatus.TRIGGERED,
                pipelinerun_name=pr_name,
                snapshot_name="",  # clear cached snapshot/release from a prior attempt
                release_name="",
                triggered_at=datetime.now(),
                retry_count=new_retry_count,
                clear_error_message=True,
            )

            retry_indicator = f" (retry {new_retry_count})" if new_retry_count > 0 else ""
            print(f"  Triggered: {tag}{retry_indicator}", file=sys.stderr)
            return 1

        except TriggerError as e:
            self._handle_trigger_failure(item, tag, str(e))
            return 0

    def _handle_trigger_failure(
        self,
        item: ImportItem,
        tag: str,
        error_msg: str,
    ) -> None:
        """Record a trigger failure in the database with appropriate retry semantics."""
        assert item.id is not None

        self.db.update_status(
            item.id,
            ImportStatus.FAILED,
            error_message=f"PipelineRun trigger failed: {error_msg}",
            retry_count=item.retry_count + 1,
        )

        print(
            f"  ERROR: Failed to trigger {tag}: {error_msg[:100]}",
            file=sys.stderr,
        )
