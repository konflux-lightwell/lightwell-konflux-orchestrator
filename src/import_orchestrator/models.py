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
    """Status of an OCI reference import."""

    PENDING = "pending"
    TRIGGERED = "triggered"
    RUNNING = "running"
    AWAITING_RELEASE = "releasing"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class OCIReference:
    """An OCI reference to be imported."""

    id: int | None
    oci_ref: str
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
