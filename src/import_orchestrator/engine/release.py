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

from import_orchestrator.clients import KubeClient
from import_orchestrator.engine.request_view import RequestView
from import_orchestrator.models import AttemptPhase, ClusterResource, Phase, PhaseStatus
from import_orchestrator.utils import extract_tag


class ReleaseMonitor:
    """Drives the post-build phases of each Attempt: snapshot, integration tests
    and release.

    For every Request whose latest Attempt has a successful intake and no terminal
    release yet, this discovers the Snapshot, represents the integration-tests
    phase (not live-monitored in this iteration -- recorded SKIPPED pending the
    reconciliation work in LWLP-2512), then finds or creates the Release and tracks
    it to completion, advancing the AttemptPhase RUNNING -> SUCCESS/FAILED. No-op
    when ``skip_release`` is set (those targets produce no Release CR).
    """

    def __init__(
        self,
        view: RequestView,
        kube: KubeClient,
        max_parallel: int,
        prefix: str,
        release_plan: str | None = None,
    ):
        self.view = view
        self.db = view.db
        self.kube = kube
        self.max_parallel = max_parallel
        self.prefix = prefix
        self.skip_release = view.skip_release
        self.release_plan = release_plan

    def update_statuses(self) -> None:
        """For Requests past a successful intake, drive snapshot -> release."""
        if self.skip_release:
            return

        eligible: list[tuple[int, str, dict[Phase, AttemptPhase]]] = []
        for request in self.db.list_requests():
            assert request.id is not None
            phases = self.db.latest_phase_map(request.id)
            intake = phases.get(Phase.INTAKE)
            if intake is None or intake.status != PhaseStatus.SUCCESS or intake.attempt_id is None:
                continue
            release = phases.get(Phase.RELEASE)
            if release is not None and release.status in (PhaseStatus.SUCCESS, PhaseStatus.FAILED):
                continue
            eligible.append((intake.attempt_id, extract_tag(request.artifact), phases))

        # Cap newly created releases at max_parallel to avoid flooding the release
        # pipeline. This is a defense-in-depth backstop: the trigger already bounds
        # total in-flight work (running + awaiting-release) at max_parallel, so in a
        # normal run this cap is rarely the binding constraint. Adopting a release
        # that already exists on the cluster is not gated by it (adoption adds no new
        # cluster load).
        active_releases = sum(1 for _, _, phases in eligible if self._has_reference(phases.get(Phase.RELEASE)))

        for attempt_id, tag, phases in eligible:
            # Step 1: discover the snapshot if not already recorded.
            snapshot = phases.get(Phase.SNAPSHOT)
            if snapshot is None or snapshot.status != PhaseStatus.SUCCESS or not snapshot.reference_id:
                intake = phases[Phase.INTAKE]
                assert intake.reference_id is not None
                self._discover_snapshot(attempt_id, intake.reference_id, tag)
                continue

            # The integration-tests phase is represented but not monitored here
            # (SKIPPED); live detection is reconciliation work (LWLP-2512).
            if phases.get(Phase.INTEGRATION_TESTS) is None:
                self.db.upsert_phase(attempt_id, Phase.INTEGRATION_TESTS, PhaseStatus.SKIPPED)

            # Step 2: find or create a release if not yet tracked.
            release = phases.get(Phase.RELEASE)
            if not self._has_reference(release):
                if self._find_or_create_release(attempt_id, snapshot.reference_id, tag, active_releases):
                    active_releases += 1
                continue

            # Step 3: poll the tracked release for completion.
            assert release is not None and release.reference_id is not None
            self._check_release_completion(attempt_id, release.reference_id, tag)

    @staticmethod
    def _has_reference(phase: AttemptPhase | None) -> bool:
        return phase is not None and phase.reference_id is not None

    def _discover_snapshot(self, attempt_id: int, pipelinerun_name: str, tag: str) -> bool:
        """Discover and record the snapshot for an intake PipelineRun.

        Returns:
            True if the snapshot was found and the snapshot phase recorded SUCCESS.
        """
        snapshot_name = self.kube.find_snapshot_by_pipelinerun(pipelinerun_name)
        if not snapshot_name:
            print(f"  Waiting for snapshot for {tag}...", file=sys.stderr)
            return False

        self.db.upsert_phase(
            attempt_id,
            Phase.SNAPSHOT,
            PhaseStatus.SUCCESS,
            reference_type=ClusterResource.SNAPSHOT,
            reference_id=snapshot_name,
        )
        print(
            f"  Found snapshot {snapshot_name} for {tag}, checking for release next poll",
            file=sys.stderr,
        )
        return True

    def _find_or_create_release(self, attempt_id: int, snapshot_name: str, tag: str, active_count: int) -> str | None:
        """Find an existing release or create a new one for the snapshot, recording
        it on the release phase as RUNNING.

        ``active_count`` is how many releases are already tracked this cycle; a new
        one is only created while it stays under ``max_parallel`` (adopting an
        existing release is never gated by it).

        Returns:
            The release name if found or created, None if creation was deferred or failed.
        """
        # An existing release for this snapshot (e.g. from a prior run) is adopted.
        # When a specific ReleasePlan is targeted, scope the lookup to it so a
        # Release created against a *different* plan for the same snapshot (e.g. an
        # auto-release to validated-stage) is not mistaken for "already done".
        if self.release_plan is not None:
            release_name = self.kube.find_release_for_snapshot_and_plan(snapshot_name, self.release_plan)
        else:
            release_name = self.kube.find_release_for_snapshot(snapshot_name)
        if release_name:
            self._track_release(attempt_id, release_name, tag)
            return release_name

        if active_count >= self.max_parallel:
            print(
                f"  Release capacity full ({active_count}/{self.max_parallel}), deferring {tag}",
                file=sys.stderr,
            )
            return None

        # Use the explicitly targeted ReleasePlan if given; otherwise resolve one
        # from the snapshot's application (which fails closed when ambiguous).
        release_plan = self.release_plan or self.kube.find_release_plan_for_snapshot(snapshot_name)
        if not release_plan:
            print(f"  No ReleasePlan found for {snapshot_name} ({tag}), will retry", file=sys.stderr)
            return None

        print(
            f"  No release found for {snapshot_name}, creating via {release_plan} ({tag})...",
            file=sys.stderr,
        )
        release_name = self.kube.create_release(snapshot_name, release_plan, self.prefix)
        if not release_name:
            print(f"  Failed to create release for {snapshot_name} ({tag}), will retry", file=sys.stderr)
            return None

        self._track_release(attempt_id, release_name, tag)
        return release_name

    def _track_release(self, attempt_id: int, release_name: str, tag: str) -> None:
        """Record the release on the release phase as RUNNING and log it."""
        self.db.upsert_phase(
            attempt_id,
            Phase.RELEASE,
            PhaseStatus.RUNNING,
            reference_type=ClusterResource.RELEASE,
            reference_id=release_name,
        )
        print(f"  Tracking release/{release_name} ({tag})", file=sys.stderr)

    def _check_release_completion(self, attempt_id: int, release_name: str, tag: str) -> None:
        """Poll release status and advance the release phase to SUCCESS/FAILED."""
        release_status = self.kube.get_release_status(release_name)
        if release_status == "True":
            self.db.upsert_phase(
                attempt_id,
                Phase.RELEASE,
                PhaseStatus.SUCCESS,
                reference_type=ClusterResource.RELEASE,
                reference_id=release_name,
            )
            print(f"  ✓ Released: {tag} (release/{release_name})", file=sys.stderr)
        elif release_status == "False":
            self.db.upsert_phase(
                attempt_id,
                Phase.RELEASE,
                PhaseStatus.FAILED,
                reference_type=ClusterResource.RELEASE,
                reference_id=release_name,
                error_message=f"Release {release_name} failed",
            )
            print(f"  ✗ Release failed: {tag} (release/{release_name})", file=sys.stderr)
        else:
            print(f"  Waiting for release/{release_name} ({tag})...", file=sys.stderr)
