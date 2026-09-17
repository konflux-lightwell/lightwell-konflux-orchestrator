"""Release-only reconciliation; deliberately cannot create PipelineRuns."""

from __future__ import annotations

import re
import sys
import time

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
        while True:
            # Release-only owns no import trigger, but it must converge stale
            # running rows before attempting Snapshot/Release reconciliation.
            PipelineMonitor(self.db, self.kube).update_statuses()
            # Pending rows are eligible only when the import already produced a
            # completed, content-matching Snapshot.  They are not admission work
            # until a Release actually needs to be created.
            for item in self.db.get_by_status(ImportStatus.PENDING):
                self._process(item, dry_run)
            for status in (ImportStatus.SUCCESS, ImportStatus.AWAITING_RELEASE, ImportStatus.FAILED):
                for item in self.db.get_by_status(status):
                    self._process(item, dry_run)
            if poll_interval is None:
                return self._exit_status()
            active = self.db.get_by_status(ImportStatus.TRIGGERED) + self.db.get_by_status(ImportStatus.RUNNING)
            releasing = self.db.get_by_status(ImportStatus.AWAITING_RELEASE)
            if not active and not releasing:
                return self._exit_status()
            time.sleep(poll_interval)

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
        if not plan and not item.release_name:
            print(f"Release deferred for {item.ref}: no ReleasePlan", file=sys.stderr)
            return False
        return self.primitive.reconcile(item, plan=plan, dry_run=dry_run)
