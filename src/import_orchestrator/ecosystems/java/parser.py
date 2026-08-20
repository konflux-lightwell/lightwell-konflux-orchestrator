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

import yaml


def parse_manifest(manifest_path: Path) -> list[str]:
    """Parse a PNC consolidated build manifest and return a list of OCI refs.

    Each library entry is expected to have ``output.artifact.tag`` and/or
    ``output.artifact.digest``. When both are present the tag and digest are
    combined into a ``repo:tag@sha256:hex`` reference so the destination image
    gets a meaningful tag. When only a digest is present it is used as-is.

    Args:
        manifest_path: Path to the consolidated YAML manifest.

    Returns:
        List of OCI reference strings (may be empty).

    Raises:
        ValueError: If a digest reference is malformed (missing '@').
    """
    data = yaml.safe_load(manifest_path.read_text())
    refs: list[str] = []
    for lib in data.get("libraries", []):
        artifact = lib.get("output", {}).get("artifact", {})
        tag_ref = artifact.get("tag", "")
        digest_ref = artifact.get("digest", "")

        if tag_ref and digest_ref:
            if "@" not in digest_ref:
                raise ValueError(f"Malformed digest reference (missing '@'): {digest_ref}")
            digest_suffix = digest_ref.split("@", 1)[1]
            refs.append(f"{tag_ref}@{digest_suffix}")
        elif digest_ref:
            refs.append(digest_ref)
        elif tag_ref:
            refs.append(tag_ref)

    return refs
