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

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal


@dataclass
class PipelineRunStatus:
    """Status of a PipelineRun."""

    name: str
    status: Literal["True", "False", "Unknown"]

    @property
    def is_running(self) -> bool:
        return self.status == "Unknown"

    @property
    def is_successful(self) -> bool:
        return self.status == "True"

    @property
    def is_failed(self) -> bool:
        return self.status == "False"

    @staticmethod
    def from_str(name: str, status: str) -> PipelineRunStatus | None:
        if name and status in ("True", "False", "Unknown"):
            return PipelineRunStatus(name, status)
        return None


# ---------------------------------------------------------------------------
# Request / Attempt / AttemptPhase model (ADR-0001).
#
# These replace the former flat import-item model: a ``Request`` is the intent to release
# one artifact for one target; each run of the workflow is an ``Attempt``; and an
# ``Attempt`` accrues an ``AttemptPhase`` row for each of the four phases as the
# workflow reaches it (rows are created on demand, not pre-seeded). Retrying
# creates a new ``Attempt`` that copies phases that already succeeded, so a
# completed phase is not re-run and no history is overwritten. A skipped phase
# is not copied; the next attempt decides again whether that phase applies.
# See docs/design/adr-0001-reconciliation.md.
# ---------------------------------------------------------------------------


class ClusterResource(str, Enum):
    """The kind of cluster resource an :class:`AttemptPhase` points at."""

    PIPELINE_RUN = "pipelineRun"
    SNAPSHOT = "snapshot"
    RELEASE = "release"


class Phase(str, Enum):
    """One of the four phases of a release workflow (ADR-0001)."""

    INTAKE = "intake"
    SNAPSHOT = "snapshot"
    INTEGRATION_TESTS = "integration_tests"
    RELEASE = "release"


# The one cluster resource each phase may point at. Intake tracks a PipelineRun,
# release a Release, and so on.
PHASE_REFERENCE_TYPE = {
    Phase.INTAKE: ClusterResource.PIPELINE_RUN,
    Phase.SNAPSHOT: ClusterResource.SNAPSHOT,
    Phase.INTEGRATION_TESTS: ClusterResource.PIPELINE_RUN,
    Phase.RELEASE: ClusterResource.RELEASE,
}


class PhaseStatus(str, Enum):
    """Status of a single :class:`AttemptPhase`.

    ``SKIPPED`` marks a phase that does not apply to this attempt (e.g. no
    IntegrationTestScenario exists for the snapshot).
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"

    @property
    def carries_forward(self) -> bool:
        """Whether a phase in this status is copied onto a retry Attempt (ADR-0001).

        Only SUCCESS is copied, so a completed phase is not re-run. PENDING,
        RUNNING, FAILED, and SKIPPED are left behind.

        TODO: Consider carrying forward SKIPPED phases as it is mostly related to
        IntegrationTestScenario not being found.
        """
        return self is PhaseStatus.SUCCESS


@dataclass
class Request:
    """An intent to release a single artifact for a specific target.

    The combination of (namespace, application, component, pipeline,
    release_plan, target, artifact) is the request's fingerprint and is unique:
    re-importing the same artifact for the same target returns the existing
    Request rather than creating a duplicate.
    """

    id: int | None
    namespace: str
    application: str
    component: str
    pipeline: str
    release_plan: str
    artifact: str
    target: str
    created_at: datetime | None = None


@dataclass
class Attempt:
    """A single execution of the build/release workflow for a Request.

    A Request has one or more Attempts; a new Attempt is created on retry.
    """

    id: int | None
    request_id: int
    created_at: datetime | None = None


@dataclass
class AttemptPhase:
    """The state of one phase (intake/snapshot/integration tests/release)
    within an Attempt, including a reference to the cluster resource it tracks
    and any error message captured when the phase failed."""

    id: int | None
    attempt_id: int
    name: Phase
    status: PhaseStatus
    reference_type: ClusterResource | None = None
    reference_id: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        """Enforce the phase-model invariants (ADR-0001) on construction.

        Because every AttemptPhase validates itself here, the store can persist one
        without knowing any of these rules, and any backend reusing this model gets
        them for free:

        - ``reference_type`` and ``reference_id`` are a unit: both or neither.
        - the reference must match the phase (see :data:`PHASE_REFERENCE_TYPE`) --
          an intake phase may only point at a pipelineRun, etc.
        - ``error_message`` tracks the status: it is kept only while FAILED, so it
          is dropped for any other status.

        Raises:
            ValueError: if only one of reference_type/reference_id is supplied, or
                the reference type does not match the phase.
        """
        if (self.reference_type is None) != (self.reference_id is None):
            raise ValueError("reference_type and reference_id must be supplied together")
        expected = PHASE_REFERENCE_TYPE[self.name]
        if self.reference_type is not None and self.reference_type is not expected:
            raise ValueError(
                f"{self.name.value} phase must reference a {expected.value}, not a {self.reference_type.value}"
            )
        if self.status is not PhaseStatus.FAILED:
            self.error_message = None


class RequestState(str, Enum):
    """The overall state of a Request, derived from its latest Attempt's phases.

    This is a *reporting* projection of the per-phase model onto a single value
    (for progress statistics, trigger batching and completion checks); it is not
    persisted. The string values match the former flat status enum so statistics
    output and the ``run-single`` JSON payload stay backwards compatible after the
    Request/Attempt cutover.
    """

    PENDING = "pending"
    RUNNING = "running"
    AWAITING_RELEASE = "releasing"
    SUCCESS = "success"
    FAILED = "failed"


def derive_state(phases: dict[Phase, AttemptPhase], skip_release: bool) -> RequestState:
    """Reduce the latest Attempt's phase rows to a single :class:`RequestState`.

    ``phases`` maps each recorded :class:`Phase` to its :class:`AttemptPhase` for
    the request's latest attempt (as returned by
    :meth:`WorkflowDatabase.latest_phase_map`); an empty map means no attempt has
    started yet. ``skip_release`` is True for targets whose workflow ends at a
    successful build (no Release CR is produced), so a successful intake is the
    terminal success state.

    A FAILED phase anywhere in the latest attempt makes the whole request FAILED
    (the attempt is retried by starting a new Attempt, not by mutating this one).
    """
    intake = phases.get(Phase.INTAKE)
    if intake is None:
        return RequestState.PENDING
    if _any_failed(phases):
        return RequestState.FAILED
    if intake.status in (PhaseStatus.PENDING, PhaseStatus.RUNNING):
        return RequestState.RUNNING
    # Intake succeeded.
    if skip_release:
        return RequestState.SUCCESS
    release = phases.get(Phase.RELEASE)
    if release is not None and release.status == PhaseStatus.SUCCESS:
        return RequestState.SUCCESS
    # Intake done, release not yet successful: snapshot/integration/release in flight.
    return RequestState.AWAITING_RELEASE


def _any_failed(phases: dict[Phase, AttemptPhase]) -> bool:
    """True if any phase of the attempt is in the FAILED state."""
    return any(phase.status == PhaseStatus.FAILED for phase in phases.values())
