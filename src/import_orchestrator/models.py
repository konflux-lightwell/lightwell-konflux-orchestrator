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


class ImportStatus(str, Enum):
    """Status of an import item."""

    PENDING = "pending"
    TRIGGERED = "triggered"
    RUNNING = "running"
    AWAITING_RELEASE = "releasing"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class ImportItem:
    """An item to be imported, identified by an ecosystem-specific unique ref."""

    id: int | None
    ref: str
    status: ImportStatus
    pipelinerun_name: str | None = None
    snapshot_name: str | None = None
    release_name: str | None = None
    triggered_at: datetime | None = None
    completed_at: datetime | None = None
    last_checked_at: datetime | None = None
    error_message: str | None = None
    retry_count: int = 0


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
# These replace the flat ``ImportItem``: a ``Request`` is the intent to release
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
