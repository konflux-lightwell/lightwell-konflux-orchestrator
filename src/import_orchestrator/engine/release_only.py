"""Release-only reconciliation; deliberately cannot create PipelineRuns."""

from __future__ import annotations

import re
import sys
import time
from datetime import datetime

from import_orchestrator.clients import KubeClient
from import_orchestrator.database import ImportDatabase
from import_orchestrator.engine.pipeline import PipelineMonitor
from import_orchestrator.engine.release import ReleasePrimitive
from import_orchestrator.models import ImportItem, ImportStatus, SnapshotLookup, SnapshotLookupState

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class ReleaseOnly:
    def __init__(
        self,
        db: ImportDatabase,
        kube: KubeClient,
        prefix: str,
        capacity: int,
        release_plan: str | None = None,
        expected_application: str | None = None,
        import_snapshot_resolver: bool = False,
    ):
        self.db, self.kube = db, kube
        self.primitive = ReleasePrimitive(db, kube, prefix, capacity)
        self.release_plan = release_plan
        self.expected_application = expected_application
        self.import_snapshot_resolver = import_snapshot_resolver

    @staticmethod
    def _digest(ref: str) -> str | None:
        match = re.search(r"@(sha256:[0-9a-f]+)$", ref)
        return match.group(1) if match and _DIGEST.fullmatch(match.group(1)) else None

    def run(self, dry_run: bool = False, poll_interval: float | None = None) -> int:
        """Reconcile until complete and report whether unresolved work remains.

        Release-only never creates import PipelineRuns.  A zero exit status means
        every row is ``success`` after the pass/watch; pending, running,
        releasing, and failed rows are unresolved and return status 1.  This
        makes a one-pass invocation useful in CI while retaining the optional
        polling behavior for work that is expected to converge.
        """
        iteration = 0
        # A previously failed row is eligible for one release retry this run.
        # Already-progressing Releases count as the attempt for this run; if one
        # fails, record it and continue to other rows rather than retrying it.
        attempted_item_ids = {
            item.id
            for item in self.db.get_by_status(ImportStatus.AWAITING_RELEASE)
            if item.id is not None and item.release_name
        }
        while True:
            iteration += 1
            print(f"\n=== Iteration {iteration} (release-only) ===", file=sys.stderr)
            # Release-only owns no import trigger, but it must converge stale
            # running rows before attempting Snapshot/Release reconciliation.
            PipelineMonitor(self.db, self.kube).update_statuses()
            # Snapshot candidate sets before processing so a row that fails a
            # Release during this pass cannot be picked up again as FAILED in
            # the same pass. Only failures present when the command started get
            # one explicit replacement attempt.
            pending_items = self.db.get_by_status(ImportStatus.PENDING)
            awaiting_items = self.db.get_by_status(ImportStatus.AWAITING_RELEASE)
            failed_items = [
                item
                for item in self.db.get_by_status(ImportStatus.FAILED)
                if item.id is not None and item.id not in attempted_item_ids
            ]
            reconcile_items = awaiting_items + failed_items + self.db.get_by_status(ImportStatus.SUCCESS)
            # Drain/poll existing releases before admitting a new snapshot so
            # a terminal failure frees the single slot for the next queued item.
            attempted_this_iteration = False
            for item in reconcile_items + pending_items:
                if item.id in attempted_item_ids and item.status is ImportStatus.AWAITING_RELEASE and item.release_name:
                    self._poll_single_attempt(item)
                    continue
                if item.status is ImportStatus.PENDING and (
                    attempted_this_iteration or self.db.count_in_flight() >= self.primitive.max_parallel
                ):
                    continue
                previous_release = item.release_name
                self._process(item, dry_run)
                if item.id is not None and item.release_name and item.release_name != previous_release:
                    attempted_item_ids.add(item.id)
                    attempted_this_iteration = True
            # A failed release discovered in this iteration remains FAILED,
            # while the loop proceeds to the next queued Snapshot.
            stats = self.db.get_statistics()
            self._print_statistics(stats)
            if poll_interval is None:
                print("\n=== Complete (release-only pass) ===", file=sys.stderr)
                return self._exit_status()
            active = self.db.get_by_status(ImportStatus.TRIGGERED) + self.db.get_by_status(ImportStatus.RUNNING)
            releasing = self.db.get_by_status(ImportStatus.AWAITING_RELEASE)
            if not active and not releasing:
                print("\n=== Complete (release-only reconciliation) ===", file=sys.stderr)
                return self._exit_status()
            print(f"Sleeping {poll_interval}s...", file=sys.stderr)
            time.sleep(poll_interval)

    def _poll_single_attempt(self, item: ImportItem) -> None:
        """Poll an already-attempted Release without creating its replacement again."""
        if item.id is None or not item.release_name:
            return
        release = item.release_name
        status = self.kube.get_release_status(release)
        attempts = self.db.get_release_attempts(item.id)
        current_attempt = next((attempt for attempt in reversed(attempts) if attempt["release_name"] == release), None)
        if status == "True":
            if current_attempt and current_attempt["status"] in ("created", "progressing", "adopted"):
                self.db.finish_release_attempt(current_attempt["id"], "success", release=release)
            self.db.update_status(
                item.id,
                ImportStatus.SUCCESS,
                release_name=release,
                completed_at=datetime.now(),
                clear_error_message=True,
            )
            item.status = ImportStatus.SUCCESS
            return
        if status == "False":
            if current_attempt and current_attempt["status"] in ("created", "progressing", "adopted"):
                self.db.finish_release_attempt(
                    current_attempt["id"], "failed", release=release, error=f"Release {release} failed"
                )
            self.db.update_status(
                item.id,
                ImportStatus.FAILED,
                error_message=f"Release {release} failed; no automatic retry in this run",
                completed_at=datetime.now(),
            )
            item.status = ImportStatus.FAILED
            print(f"Release failed: {item.ref} (release/{release}); will proceed to next item", file=sys.stderr)
            return
        print(f"Waiting for release/{release} ({item.ref})...", file=sys.stderr)

    def _print_statistics(self, stats: dict[str, int]) -> None:
        """Use the same per-status progress line as full orchestration."""
        print(
            f"Status: pending={stats[ImportStatus.PENDING.value]}, "
            f"triggered={stats[ImportStatus.TRIGGERED.value]}, "
            f"running={stats[ImportStatus.RUNNING.value]}, "
            f"releasing={stats[ImportStatus.AWAITING_RELEASE.value]}, "
            f"success={stats[ImportStatus.SUCCESS.value]}, "
            f"failed={stats[ImportStatus.FAILED.value]}",
            file=sys.stderr,
        )

    def _exit_status(self) -> int:
        """Return zero only when no import row remains unresolved."""
        unresolved = (
            self.db.get_by_status(ImportStatus.PENDING)
            + self.db.get_by_status(ImportStatus.TRIGGERED)
            + self.db.get_by_status(ImportStatus.RUNNING)
            + self.db.get_by_status(ImportStatus.AWAITING_RELEASE)
            + self.db.get_by_status(ImportStatus.FAILED)
        )
        return 1 if unresolved else 0

    def _process(self, item: ImportItem, dry_run: bool) -> bool:
        # Release-only promotes a completed Snapshot directly.  Resolve it from
        # the completed import PipelineRun before validating its components: the
        # pipeline monitor intentionally only transitions the row to
        # AWAITING_RELEASE and does not know the Snapshot name.
        if not item.id:
            return False
        # A tracked Release is authoritative for lifecycle reconciliation.  Do
        # this before resolving the Snapshot: a completed Release must be able to
        # finish even when the Snapshot has since disappeared or changed digest.
        if item.release_name:
            tracked = self.primitive.reconcile_tracked(item, dry_run=dry_run)
            if tracked is True:
                return True
            # False means either an active Release could not be queried, or a
            # failed pointer was retired.  Only the latter is eligible for the
            # Snapshot validation and replacement path below.
            if item.release_name:
                return False

        snapshot = None
        source_resolver_used = False
        if item.status is ImportStatus.PENDING:
            # Java rows resolve through the source-matching completed import PLR.
            resolver = getattr(self.kube, "find_snapshot_for_import", None)
            if not self.import_snapshot_resolver:
                resolver = None
            if callable(resolver):
                try:
                    resolved = resolver(item.ref, self.expected_application)
                except TypeError:
                    resolved = resolver(item.ref)
                if (
                    isinstance(resolved, SnapshotLookup)
                    and resolved.state is SnapshotLookupState.FOUND
                    and resolved.name
                ):
                    # A source match is authoritative for Java: the Snapshot was
                    # produced by this import, so never replace it with a shared
                    # component-digest lookup.
                    snapshot = resolved.name
                    source_resolver_used = True
                else:
                    # CONFIRMED_EMPTY, AMBIGUOUS, and UNKNOWN all defer.  In
                    # particular, an empty source match must not fall through to
                    # the unsafe shared-component resolver.
                    state = resolved.state.value if isinstance(resolved, SnapshotLookup) else "unknown"
                    print(
                        f"Release deferred for {item.ref}: no verified source-matching Snapshot "
                        f"({state}); verify import PipelineRun/Snapshot and retry",
                        file=sys.stderr,
                    )
                    return False
            if source_resolver_used:
                item.snapshot_name = snapshot
                if not dry_run and item.id is not None:
                    self.db.update_status(item.id, ImportStatus.PENDING, snapshot_name=snapshot)
            if not source_resolver_used:
                # Legacy/non-Java behavior: match ImportTrigger's exact
                # canonical digest/application lookup, but never turn a
                # release-only miss into an import.
                digest = item.ref.rsplit("@", 1)[-1] if "@" in item.ref else ""
                finder = getattr(self.kube, "find_snapshot_by_component_digest", None)
                lookup = SnapshotLookup(SnapshotLookupState.UNKNOWN)
                if callable(finder) and _DIGEST.fullmatch(digest):
                    try:
                        value = finder(digest, self.expected_application)
                    except TypeError:
                        value = finder(digest)
                    if isinstance(value, str) and value:
                        lookup = SnapshotLookup(SnapshotLookupState.FOUND, value)
                    elif isinstance(value, SnapshotLookup):
                        if value.name or value.state is not SnapshotLookupState.FOUND:
                            lookup = value
                        else:
                            lookup = SnapshotLookup(SnapshotLookupState.UNKNOWN)
                if lookup.state is not SnapshotLookupState.FOUND or not lookup.name:
                    state = lookup.state.value
                    print(
                        f"Release deferred for {item.ref}: no verified matching Snapshot "
                        f"({state}); verify Snapshot API/application and retry",
                        file=sys.stderr,
                    )
                    return False
                snapshot = lookup.name
                item.snapshot_name = snapshot
                if not dry_run and item.id is not None:
                    self.db.update_status(item.id, ImportStatus.PENDING, snapshot_name=snapshot)
        else:
            snapshot = self.primitive.snapshot(item, dry_run=dry_run)
        if not snapshot:
            print(
                f"Release deferred for {item.ref}: Snapshot not found for PipelineRun "
                f"{item.pipelinerun_name or '<none>'}",
                file=sys.stderr,
            )
            return False
        # Keep the resolved value on the in-memory item as well.  This avoids a
        # second lookup in ReleasePrimitive and makes dry-run validation use the
        # same Snapshot that will be reconciled.
        item.snapshot_name = snapshot
        digest = self._digest(item.ref)
        snapshot_digests = self.kube.get_snapshot_component_digests(snapshot)
        if not digest or snapshot_digests is None or digest not in snapshot_digests:
            print(f"Release deferred for {item.ref}: Snapshot digest mismatch", file=sys.stderr)
            return False
        # Prefer the invocation plan, then the plan persisted with the item.
        # Monitoring an already persisted Release does not need either value;
        # a plan is required only if reconciliation must create a new Release.
        plan = self.release_plan or item.release_plan
        # Java release-only has no safe static default: resolve the unique plan
        # for this exact Snapshot only when neither CLI nor persisted state chose
        # one. This remains release-only; it never invokes import triggering.
        if not plan and not item.release_name:
            plan = self.kube.find_release_plan_for_snapshot(snapshot)
        if not plan and not item.release_name:
            print(f"Release deferred for {item.ref}: no ReleasePlan", file=sys.stderr)
            return False
        return self.primitive.reconcile(item, plan=plan, dry_run=dry_run)
