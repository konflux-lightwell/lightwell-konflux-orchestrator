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
from import_orchestrator.models import AttemptPhase, Phase, PhaseStatus, Request
from import_orchestrator.utils import extract_tag


class PipelineMonitor:
    """Monitors intake PipelineRun status and advances the intake phase.

    Polls Kubernetes for the intake PipelineRun of each Request's latest Attempt
    and advances its intake :class:`AttemptPhase` RUNNING -> SUCCESS/FAILED. When
    the run succeeds, the release monitor takes over (via the snapshot phase);
    when ``skip_release`` is set the successful intake is the terminal state and
    nothing further happens.
    """

    def __init__(self, view: RequestView, kube: KubeClient):
        self.view = view
        self.db = view.db
        self.kube = kube
        self.skip_release = view.skip_release

    def update_statuses(self) -> None:
        """Check the intake PipelineRun of every in-progress Request and update it.

        A PENDING intake was submitted but its create result was unclear. Once the
        run is seen, it becomes RUNNING. If the run is confirmed absent, the intake
        FAILS so the request can be retried. Any other lookup outcome leaves it PENDING.
        """
        for request in self.db.list_requests():
            assert request.id is not None
            intake = self.db.latest_phase_map(request.id).get(Phase.INTAKE)
            if intake is None or intake.attempt_id is None:
                continue
            if intake.status not in (PhaseStatus.PENDING, PhaseStatus.RUNNING):
                continue
            if intake.reference_id is None:
                continue

            pr_status = self.kube.get_pipelinerun_status(intake.reference_id)
            if pr_status is None:
                self._handle_unseen_run(request, intake)
                continue

            tag = extract_tag(request.artifact)

            if pr_status.is_running:
                self._record_seen_run(intake)  # confirms an unclear create; no-op for a RUNNING intake
                continue  # still in flight
            elif pr_status.is_successful:
                self.db.upsert_phase(
                    intake.attempt_id,
                    Phase.INTAKE,
                    PhaseStatus.SUCCESS,
                    reference_type=intake.reference_type,
                    reference_id=intake.reference_id,
                )
                if self.skip_release:
                    print(f"  ✓ Build done (scratch, no release): {tag}", file=sys.stderr)
                else:
                    print(f"  Pipeline done, awaiting release: {tag}", file=sys.stderr)
            elif pr_status.is_failed:
                detail = self.kube.get_pipelinerun_failure_detail(intake.reference_id)
                self.db.upsert_phase(
                    intake.attempt_id,
                    Phase.INTAKE,
                    PhaseStatus.FAILED,
                    reference_type=intake.reference_type,
                    reference_id=intake.reference_id,
                    error_message=detail or "PipelineRun failed",
                )
                print(f"  ✗ Failed: {tag}", file=sys.stderr)
                if detail:
                    print(f"    {detail}", file=sys.stderr)

    def _record_seen_run(self, intake: AttemptPhase) -> None:
        """Mark a PENDING intake RUNNING once its PipelineRun is seen. A RUNNING intake is already confirmed."""
        if intake.status != PhaseStatus.PENDING:
            return
        self.db.upsert_phase(
            intake.attempt_id,
            Phase.INTAKE,
            PhaseStatus.RUNNING,
            reference_type=intake.reference_type,
            reference_id=intake.reference_id,
        )

    def _handle_unseen_run(self, request: Request, intake: AttemptPhase) -> None:
        """Fail a PENDING intake whose PipelineRun is confirmed absent. Other unseen runs wait for the next poll."""
        name = intake.reference_id
        if intake.status == PhaseStatus.PENDING and name is not None and self.kube.pipelinerun_absent(name):
            self._fail_unsubmitted_intake(request, intake)

    def _fail_unsubmitted_intake(self, request: Request, intake: AttemptPhase) -> None:
        """Fail a PENDING intake whose PipelineRun is confirmed absent, so the request can be retried."""
        name = intake.reference_id
        self.db.upsert_phase(
            intake.attempt_id,
            Phase.INTAKE,
            PhaseStatus.FAILED,
            reference_type=intake.reference_type,
            reference_id=name,
            error_message=f"PipelineRun {name} was not created (create result was unclear and the run was not found)",
        )
        print(f"  ✗ Not created: {extract_tag(request.artifact)}", file=sys.stderr)
