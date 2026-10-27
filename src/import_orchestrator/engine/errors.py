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


class TriggerError(Exception):
    """Non-recoverable error during the PipelineRun trigger process."""


class ReleaseError(Exception):
    """Non-recoverable error while creating a Release.

    Unlike a Snapshot or ReleasePlan not being ready yet (a transient
    condition worth polling again), a rejected create call -- a permissions
    gap, an invalid releasePlan reference -- will not resolve itself by
    retrying the identical request. Raising this lets the caller record a
    failed, retry-bounded release phase instead of looping forever with no
    state change (LWLP-3937).
    """
