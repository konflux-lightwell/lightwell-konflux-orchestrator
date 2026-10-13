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

import argparse
from typing import Protocol, runtime_checkable

DEFAULT_PIPELINERUN_ANNOTATIONS: dict[str, str] = {
    # Ensures each snapshot gets its own release even when a newer one arrives concurrently.
    "test.appstudio.openshift.io/ignore-supersession": "true",
}


def build_request_identity(
    *,
    namespace: str,
    application: str,
    component: str,
    pipeline: str,
    target: str,
    artifact: str,
    release_plan: str = "",
) -> dict[str, str]:
    """Assemble the ADR-0001 Request fingerprint from its resolved fields.

    The fingerprint *rule* -- which seven fields identify a Request and how they
    are normalized (``pipeline`` stripped of its trailing ``-``, ``release_plan``
    coerced to an empty string when unknown) -- is the same for every ecosystem;
    only the field *values* are ecosystem-specific. Centralized here so ecosystems
    supply values, not rules. The returned dict is passed straight to
    ``WorkflowDatabase.get_or_create_request``.
    """
    return {
        "namespace": namespace,
        "application": application,
        "component": component,
        "pipeline": pipeline.rstrip("-"),
        "release_plan": release_plan or "",
        "target": target,
        "artifact": artifact,
    }


@runtime_checkable
class Ecosystem(Protocol):
    """Contract each ecosystem implements so the generic core can run its pipeline.

    Only what the generic trigger/orchestrate core needs is on the contract.
    Discovery/parsing commands (e.g. fetch, import-manifest) are wired by the
    ecosystem's own ``register_cli`` and are not part of this interface.
    """

    name: str
    default_db_path: str
    pipelinerun_prefix: str
    namespace: str

    def build_pipelinerun(self, ref: str, args: argparse.Namespace) -> dict:
        """Build a PipelineRun manifest dict for one import item (by its ref)."""
        ...

    def request_identity(self, ref: str, args: argparse.Namespace) -> dict[str, str]:
        """Return the identifying fields of the Request for a ref (ADR-0001).

        Maps a ref plus the invocation args to the seven fingerprint fields a
        ``Request`` is created from: ``namespace``, ``application``, ``component``,
        ``pipeline``, ``release_plan``, ``target`` and ``artifact``. Fields not
        known at ingest (notably ``release_plan`` for ecosystems that discover the
        ReleasePlan from the snapshot) are returned as the empty string. This is
        the precursor to the ADR "recipes"; the returned dict is passed straight
        to ``WorkflowDatabase.get_or_create_request``.
        """
        ...

    def register_cli(self, subparsers: argparse._SubParsersAction) -> None:
        """Register this ecosystem's subparser and its command tree."""
        ...
