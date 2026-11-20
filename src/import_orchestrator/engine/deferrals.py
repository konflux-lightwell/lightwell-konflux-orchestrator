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

from import_orchestrator.constants import MAX_QUOTA_DEFERRALS


class QuotaDeferralCounter:
    """Track consecutive quota deferrals per key within one run.

    A quota deferral records no phase, so nothing in the database counts
    them; this in-memory counter is what bounds the wait. "Consecutive"
    is enforced by resetting a key's count whenever the corresponding
    create succeeds (capacity returned), so intermittent blips during a
    long run cannot accumulate to the cap.

    Keys are whatever the caller tracks a pending unit by (a request id
    for intake triggers, an attempt id for release creates); the counter
    does not care.
    """

    def __init__(self) -> None:
        self._counts: dict[int, int] = {}

    def increment(self, key: int) -> int:
        """Count one more deferral for ``key`` and return the new total."""
        count = self._counts.get(key, 0) + 1
        self._counts[key] = count
        return count

    def reset(self, key: int) -> None:
        """Forget ``key``'s deferrals -- its create succeeded, so any
        future quota episode starts counting from zero."""
        self._counts.pop(key, None)

    def at_cap(self, count: int) -> bool:
        """Whether ``count`` consecutive deferrals has reached the cap.

        Past the cap the caller stops deferring and records an ordinary,
        retry-bounded failure, so a quota condition that never clears
        within one run cannot spin the poll loop forever.
        """
        return count >= MAX_QUOTA_DEFERRALS
