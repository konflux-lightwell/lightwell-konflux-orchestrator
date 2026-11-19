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
from import_orchestrator.models import Fingerprint, RequestState
from import_orchestrator.utils import extract_tag


class PruneMonitor:
    """Deletes the cluster objects of successful Requests to reclaim namespace quota.

    Once a Request reaches terminal SUCCESS its PipelineRuns, Snapshot and Release are
    no longer needed on the cluster (KubeArchive retains them), so they are deleted.
    Objects are located by the fingerprint label, so one delete per kind also sweeps
    the integration-test PipelineRuns the phase model never records by name. Pruning is
    idempotent and resumable: each Request is pruned at most once (guarded by
    ``pruned_at``), and a partial failure leaves it unmarked so the next cycle retries.
    """

    def __init__(self, view: RequestView, kube: KubeClient):
        self.view = view
        self.db = view.db
        self.kube = kube

    def prune_completed(self) -> int:
        """Prune every successful, not-yet-pruned Request; return the count pruned."""
        pruned = 0
        for request in self.view.requests():
            assert request.id is not None
            if request.pruned_at is not None:
                continue
            if self.view.state(request) != RequestState.SUCCESS:
                continue

            tag = extract_tag(request.artifact)
            # Deleting by fingerprint label is safe against hitting another in-flight
            # Request's objects: the fingerprint omits source_repo, but within one run
            # the CLI args are fixed, so source_repo is a pure function of the artifact
            # (python) or a constant (javascript). Two Requests in this run with the
            # same artifact therefore resolve to the same source_repo and dedupe to one
            # Request -- so no two distinct in-run Requests can share a fingerprint.
            fingerprint = Fingerprint.from_request(request).hash()
            if self.kube.prune_request_resources(fingerprint):
                self.db.mark_pruned(request.id)
                pruned += 1
                print(f"  Pruned cluster resources for {tag}", file=sys.stderr)
            else:
                print(f"  Prune incomplete for {tag}; will retry on the next cycle or a later run", file=sys.stderr)
        return pruned

    def unpruned_successful_count(self) -> int:
        """Count successful Requests whose resources are not yet pruned.

        Lets the caller surface a single warning when a run ends with cluster
        resources still live despite the Request having succeeded (e.g. a persistent
        delete failure such as missing ``deletecollection`` RBAC), which otherwise
        only shows up as per-cycle error lines.
        """
        return sum(
            1
            for request in self.view.requests()
            if request.pruned_at is None and self.view.state(request) == RequestState.SUCCESS
        )
