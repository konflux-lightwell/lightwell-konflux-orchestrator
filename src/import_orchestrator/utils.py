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

import re
import subprocess


def extract_tag(oci_ref: str) -> str:
    """Extract tag from an OCI reference.

    Parses the tag portion from a reference like
    ``quay.io/repo:lw-BPRVHPONFDQAA@sha256:...`` and returns ``lw-BPRVHPONFDQAA``.

    Falls back to the last 40 characters of the reference if parsing fails.
    """
    match = re.search(r":([^@]+)@", oci_ref)
    if match:
        return match.group(1)
    digest_match = re.search(r"@sha256:([a-f0-9]+)", oci_ref)
    if digest_match:
        return digest_match.group(1)
    return oci_ref[-40:]


def should_retry(error: subprocess.CalledProcessError) -> bool:
    """Determine if a subprocess error is transient and eligible for retry.

    Permanent failures (validation errors, authentication errors, exit code 2) are
    not retried. Everything else is considered transient.
    """
    stderr = error.stderr.decode() if isinstance(error.stderr, bytes) else error.stderr or ""

    if "validation error" in stderr.lower():
        return False
    if "authentication" in stderr.lower():
        return False
    if error.returncode == 2:
        return False

    return True
