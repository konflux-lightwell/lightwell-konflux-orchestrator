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

from pathlib import Path
from typing import Any

import yaml

from import_orchestrator.engine.errors import TriggerError


def load_pipeline(pipeline_path: Path) -> dict[str, Any]:
    """Load a Tekton pipeline definition and return its ``spec`` block."""
    if not pipeline_path.exists():
        raise TriggerError(f"pipeline definition not found: {pipeline_path}")
    try:
        with open(pipeline_path) as f:
            pipeline = yaml.safe_load(f)
        return pipeline["spec"]
    except (yaml.YAMLError, KeyError, TypeError) as e:
        raise TriggerError(f"failed to load pipeline from {pipeline_path}: {e}") from e
