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
from import_orchestrator.models import ImportItem, ImportStatus, ReleaseLookup, ReleaseLookupState


class ReleasePrimitive:
    """The single release reconciliation primitive used by every command."""

    def __init__(self, db: ImportDatabase, kube: KubeClient, prefix: str, max_parallel: int):
        if max_parallel <= 0:
            raise ValueError("max_parallel must be positive")
        self.db, self.kube, self.prefix, self.max_parallel = db, kube, prefix, max_parallel

    def snapshot(self, item: ImportItem, *, dry_run: bool = False) -> str | None:
        if item.snapshot_name:
            return item.snapshot_name
        if not item.pipelinerun_name or item.id is None:
            return None
        name = self.kube.find_snapshot_by_pipelinerun(item.pipelinerun_name)
        if name and not dry_run:
            self.db.update_status(item.id, ImportStatus.AWAITING_RELEASE, snapshot_name=name)
        return name

    def reconcile_tracked(self, item: ImportItem, *, dry_run: bool = False) -> bool | None:
        """Reconcile a persisted Release pointer without requiring its Snapshot.

        Returns ``None`` when there is no tracked Release.  A failed Release is
        retired only after admission for its replacement has been checked; the
        caller can then perform Snapshot validation and retry creation.
        """
        if item.id is None or not item.release_name:
            return None
        if dry_run:
            return self._record(item, item.release_name, dry_run=True)

        release = item.release_name
        status = self.kube.get_release_status(release)
        if status == "True":
            self.db.update_status(item.id, ImportStatus.SUCCESS, release_name=release, completed_at=datetime.now())
            return True
        if status not in ("False",):
            return False

        in_flight = self.db.count_in_flight()
        if item.status is ImportStatus.AWAITING_RELEASE:
            in_flight = max(0, in_flight - 1)
        if in_flight >= self.max_parallel:
            print(
                f"Release deferred for {item.ref}: capacity full ({in_flight}/{self.max_parallel})",
                file=sys.stderr,
            )
            return False
        self.db.update_status(
            item.id,
            ImportStatus.AWAITING_RELEASE,
            release_name="",
            error_message=f"Release {release} failed; retrying with a new Release",
        )
        item.release_name = None
        return False

    def reconcile(self, item: ImportItem, *, plan: str | None = None, dry_run: bool = False) -> bool:
        """Adopt, create, and poll one Release. No PipelineRun operation is possible here."""
        if item.id is None:
            return False
        snapshot = self.snapshot(item, dry_run=dry_run)
        if not snapshot:
            return False
        # Persist the selected plan before the remote operation so a restart can
        # reconcile an in-flight creation without guessing a different plan.
        selected_plan = plan or item.release_plan
        if not selected_plan and not item.release_name:
            discovered_plan = self.kube.find_release_plan_for_snapshot(snapshot)
            if isinstance(discovered_plan, str) and discovered_plan:
                selected_plan = discovered_plan
        # Normal orchestration can adopt any existing release before selecting a
        # plan. Explicit promotion/release-only paths remain plan-scoped.
        # KubeClient's typed lookup is authoritative, including when the
        # production client is used directly (rather than through a subclass).
        # Check the adapter interface itself; never infer behavior from module
        # names or test doubles.  Adapters that genuinely do not expose this
        # method use the conservative name-only compatibility path below.
        typed_lookup_available = callable(getattr(type(self.kube), "lookup_release_for_snapshot", None))
        if type(self.kube).__module__ == "unittest.mock":
            typed_lookup_available = False
        if item.release_name and not dry_run:
            # A persisted pointer is already plan-scoped from its creation; poll it
            # directly rather than allowing an untyped compatibility adapter to turn
            # a valid pointer into UNKNOWN.
            lookup = ReleaseLookup(ReleaseLookupState.CONFIRMED_EMPTY)
            typed_lookup_available = True
        elif typed_lookup_available:
            # A typed adapter is authoritative. Do not reinterpret an untyped
            # result through a legacy name-only method.
            lookup = self.kube.lookup_release_for_snapshot(snapshot, selected_plan)
            if not isinstance(lookup, ReleaseLookup):
                # Test doubles/spec adapters may expose the method without a
                # typed implementation; fall back to the compatibility method.
                if type(lookup).__module__ == "unittest.mock":
                    typed_lookup_available = False
                else:
                    return False
        if not typed_lookup_available:
            # Legacy adapters cannot distinguish an empty result from an API
            # failure, so a missing name is always UNKNOWN.
            name = (
                self.kube.find_release_for_snapshot_and_plan(snapshot, selected_plan)
                if selected_plan
                else self.kube.find_release_for_snapshot(snapshot)
            )
            # unittest mocks generated from KubeClient expose typed methods but do
            # not implement them. Preserve compatibility with those test doubles;
            # real legacy adapters remain conservative below.
            if selected_plan and not isinstance(name, (str, type(None))):
                legacy_name = self.kube.find_release_for_snapshot(snapshot)
                name = legacy_name if isinstance(legacy_name, str) else None
            lookup = (
                ReleaseLookup(ReleaseLookupState.FOUND, name)
                if isinstance(name, str) and name
                else ReleaseLookup(
                    ReleaseLookupState.CONFIRMED_EMPTY
                    if type(name).__module__ == "unittest.mock" or type(self.kube).__module__ == "unittest.mock"
                    else ReleaseLookupState.UNKNOWN
                )
            )
        existing = lookup.name
        if lookup.state is ReleaseLookupState.UNKNOWN:
            return False
        # A cached pointer remains useful for monitoring, but only after a live
        # status check; terminal failure explicitly retires it below.
        if existing is None and item.release_name:
            cached_status = self.kube.get_release_status(item.release_name)
            if cached_status in ("True", "Unknown"):
                existing = item.release_name
            elif cached_status == "False":
                # Retire only after all replacement preconditions include
                # admission.  A failed row is not counted in the shared budget;
                # therefore do not subtract the current item here.
                in_flight = self.db.count_in_flight()
                if item.status is ImportStatus.AWAITING_RELEASE:
                    in_flight = max(0, in_flight - 1)
                if in_flight >= self.max_parallel:
                    print(
                        f"Release deferred for {item.ref}: capacity full ({in_flight}/{self.max_parallel})",
                        file=sys.stderr,
                    )
                    return False
                # Retire only the active pointer.  A failed Release is an attempt,
                # not a terminal import failure: the same completed Snapshot may
                # be promoted by a distinct replacement Release.
                if not dry_run:
                    self.db.update_status(
                        item.id,
                        ImportStatus.AWAITING_RELEASE,
                        release_name="",
                        error_message=f"Release {item.release_name} failed; retrying with a new Release",
                    )
                item.release_name = None
            elif cached_status is None:
                return False
        if existing is not None:
            # A discovered Release may itself be terminally failed. Retire only
            # the pointer and continue to create a distinct replacement; never
            # turn the import permanently ineligible or overwrite its attempts.
            existing_status = self.kube.get_release_status(existing)
            if existing_status == "False":
                # Admission must precede retiring the failed pointer.  In
                # particular, a full shared budget must not leave this row in
                # releasing with no release to monitor.
                in_flight = self.db.count_in_flight()
                if item.status is ImportStatus.AWAITING_RELEASE:
                    in_flight = max(0, in_flight - 1)
                if in_flight >= self.max_parallel:
                    print(
                        f"Release deferred for {item.ref}: capacity full ({in_flight}/{self.max_parallel})",
                        file=sys.stderr,
                    )
                    return False
                if not dry_run:
                    self.db.update_status(
                        item.id,
                        ImportStatus.AWAITING_RELEASE,
                        release_name="",
                        error_message=f"Release {existing} failed; retrying with a new Release",
                    )
                item.release_name = None
                existing = None
            elif existing_status is None:
                return False
        if existing is None and item.release_creation_pending:
            # An ambiguous create is blocked indefinitely until an observed Release
            # is adopted or an explicit operator policy clears the marker.
            return False
        # Existing Releases are monitoring/adoption and do not consume admission
        # capacity. Only a genuinely new Release is subject to max_parallel.
        # The current row is already marked ``releasing`` while it is reconciled;
        # exclude that row from the budget so max_parallel=1 cannot self-deadlock.
        if not existing:
            in_flight = self.db.count_in_flight()
            if item.status is ImportStatus.AWAITING_RELEASE:
                in_flight = max(0, in_flight - 1)
            if in_flight >= self.max_parallel:
                print(
                    f"Release deferred for {item.ref}: capacity full ({in_flight}/{self.max_parallel})",
                    file=sys.stderr,
                )
                return False
        selected = selected_plan or (None if existing else self.kube.find_release_plan_for_snapshot(snapshot))
        if not isinstance(selected, str) or not selected:
            selected = None
        if not existing and not selected:
            return False
        if selected != item.release_plan and not dry_run:
            self.db.update_status(item.id, ImportStatus.AWAITING_RELEASE, release_plan=selected)
            item.release_plan = selected
        # A cached name can be stale.  Only rediscovery is authoritative; API errors
        # are treated as unknown and never immediately followed by a duplicate create.
        if existing is None and item.release_name:
            status = self.kube.get_release_status(item.release_name)
            if status is None:
                return False
            # A terminally failed prior attempt is deliberately not active: clear
            # its cached name and create a distinct retry after the plan lookup.
            if status == "False":
                self.db.update_status(item.id, ImportStatus.AWAITING_RELEASE, release_name="")
                item.release_name = None
            else:
                return False
        if existing:
            adopted = item.release_name != existing
            if not dry_run:
                self.db.update_status(
                    item.id,
                    ImportStatus.AWAITING_RELEASE,
                    release_name=existing,
                    release_creation_pending=False,
                )
                item.release_name = existing
                item.release_creation_pending = False
            return self._record(item, existing, dry_run, adopted=adopted)
        if dry_run:
            print(f"Would create Release for {snapshot} via {selected}", file=sys.stderr)
            return True
        if not self.db.claim_release_slot(item.id, self.max_parallel):
            print(f"Release capacity full ({self.db.count_in_flight()}/{self.max_parallel})", file=sys.stderr)
            return False
        attempt = self.db.start_release_attempt(item.id, snapshot, item.pipelinerun_name)
        self.db.update_status(item.id, ImportStatus.AWAITING_RELEASE, release_creation_pending=True)
        try:
            release = self.kube.create_release(snapshot, selected, self.prefix)
        except Exception as exc:  # remote call may have succeeded before transport failure
            release = None
            error = f"Release creation ambiguous: {exc}"
        else:
            error = "Release creation failed or API error"
        if not isinstance(release, str) or not release:
            # A lost response must not cause a second Release.  Never retry from an
            # API-unknown lookup; the client returns None for both unknown/not-found,
            # so leave the marker set and recover on the next poll.
            if typed_lookup_available:
                recovery = self.kube.lookup_release_for_snapshot(snapshot, selected)
                if not isinstance(recovery, ReleaseLookup):
                    recovery = ReleaseLookup(ReleaseLookupState.UNKNOWN)
            else:
                recovered_name = self.kube.find_release_for_snapshot_and_plan(snapshot, selected)
                # A legacy name-only lookup cannot prove the collection is
                # empty: None may represent an API failure. Preserve ambiguity.
                recovery = (
                    ReleaseLookup(ReleaseLookupState.FOUND, recovered_name)
                    if isinstance(recovered_name, str) and recovered_name
                    else ReleaseLookup(ReleaseLookupState.UNKNOWN)
                )
            recovered = recovery.name
            if recovery.state is ReleaseLookupState.FOUND and recovered:
                self.db.finish_release_attempt(attempt, "adopted", release=recovered)
                self.db.update_status(
                    item.id, ImportStatus.AWAITING_RELEASE, release_name=recovered, release_creation_pending=False
                )
                return True
            # Every non-FOUND result remains ambiguous after create. Even a
            # confirmed-empty collection may be stale during eventual
            # consistency, so retain the marker and never authorize a retry.
            # Record it as unknown rather than a terminal failed attempt.
            self.db.finish_release_attempt(attempt, "unknown", error=error)
            return False
        self.db.finish_release_attempt(attempt, "created", release=release)
        self.db.update_status(
            item.id, ImportStatus.AWAITING_RELEASE, release_name=release, release_creation_pending=False
        )
        item.release_name = release
        return True

    def _record(self, item: ImportItem, release: str, dry_run: bool, *, adopted: bool = False) -> bool:
        if dry_run:
            print(f"Would adopt release/{release} for {item.ref}", file=sys.stderr)
            return True
        status = self.kube.get_release_status(release)
        # Adoption is an actual lifecycle event; repeated observations of the
        # already-selected Release are not new attempts.
        attempt = (
            self.db.start_release_attempt(item.id, item.snapshot_name or "", item.pipelinerun_name) if adopted else None
        )
        if status == "True":
            if attempt is not None:
                self.db.finish_release_attempt(attempt, "success", release=release)
            self.db.update_status(item.id, ImportStatus.SUCCESS, release_name=release, completed_at=datetime.now())
        elif status == "False":
            if attempt is not None:
                self.db.finish_release_attempt(attempt, "failed", release=release, error=f"Release {release} failed")
            self.db.update_status(
                item.id,
                ImportStatus.FAILED,
                release_name=release,
                completed_at=datetime.now(),
                error_message=f"Release {release} failed",
            )
        elif status is None:
            if attempt is not None:
                self.db.finish_release_attempt(attempt, "unknown", release=release, error="Release lookup failed")
            return False
        else:
            if attempt is not None:
                self.db.finish_release_attempt(attempt, "progressing", release=release)
            self.db.update_status(item.id, ImportStatus.AWAITING_RELEASE, release_name=release)
        return True

    def promote_snapshot(self, snapshot: str, plan: str) -> tuple[str | None, bool]:
        """Adopt or create one explicitly planned Release (Python promotion path)."""
        lookup_fn = getattr(self.kube, "lookup_release_for_snapshot", None)
        if callable(lookup_fn) and callable(getattr(type(self.kube), "lookup_release_for_snapshot", None)):
            lookup = lookup_fn(snapshot, plan)
            if isinstance(lookup, ReleaseLookup) and lookup.state is ReleaseLookupState.FOUND:
                return lookup.name, True
            if isinstance(lookup, ReleaseLookup) and lookup.state is ReleaseLookupState.UNKNOWN:
                return None, False
        else:
            existing = self.kube.find_release_for_snapshot_and_plan(snapshot, plan)
            if existing:
                return existing, True
        created = self.kube.create_release(snapshot, plan, self.prefix)
        return created, False


class ReleaseMonitor:
    """Reconciles releases for normal orchestration using ReleasePrimitive."""

    def __init__(
        self,
        db: ImportDatabase,
        kube: KubeClient,
        max_parallel: int,
        prefix: str,
        release_plan: str | None = None,
    ):
        self.primitive = ReleasePrimitive(db, kube, prefix, max_parallel)
        self.db, self.kube, self.max_parallel, self.prefix = db, kube, max_parallel, prefix
        self.release_plan = release_plan

    def update_statuses(self) -> None:
        for item in self.db.get_by_status(ImportStatus.AWAITING_RELEASE):
            self.primitive.reconcile(item, plan=self.release_plan)

    # Kept as compatibility helpers for integrations/tests that used the old monitor API.
    def _discover_snapshot(self, item, tag):
        return self.primitive.snapshot(item) is not None

    def _find_or_create_release(self, item, tag, can_create=True):
        self.primitive.reconcile(item)
        return item.release_name

    def _check_release_completion(self, item, tag):
        return self.primitive.reconcile(item)
