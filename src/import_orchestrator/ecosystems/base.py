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

    def register_cli(self, subparsers: argparse._SubParsersAction) -> None:
        """Register this ecosystem's subparser and its command tree."""
        ...
