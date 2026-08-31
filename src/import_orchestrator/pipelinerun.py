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

import hashlib
import json
import re

IMPORT_IDENTITY_ANNOTATION = "lightwell.redhat.com/import-identity"
_MAX_KUBERNETES_NAME_LENGTH = 253


def build_execution_spec_fingerprint(execution_spec: object) -> str:
    """Hash a canonical, non-secret representation of an execution spec."""
    canonical_spec = json.dumps(execution_spec, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_spec.encode("utf-8")).hexdigest()


def build_import_identity(ecosystem: str, **parts: object) -> str:
    """Serialize the immutable fields that identify one logical import."""
    return json.dumps({"ecosystem": ecosystem, **parts}, sort_keys=True, separators=(",", ":"))


def build_pipelinerun_name(prefix: str, identity: str) -> str:
    """Return a deterministic DNS-compatible PipelineRun name."""
    normalized_prefix = re.sub(r"[^a-z0-9-]+", "-", prefix.lower()).strip("-") or "pipelinerun"
    identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    prefix_length = _MAX_KUBERNETES_NAME_LENGTH - len(identity_hash) - 1
    normalized_prefix = normalized_prefix[:prefix_length].rstrip("-")
    return f"{normalized_prefix}-{identity_hash}"
