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

import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from import_orchestrator.clients import QuayClient
from import_orchestrator.database import ImportDatabase


@dataclass(frozen=True)
class IngestResult:
    """Outcome of an OCI reference ingestion operation."""

    total: int
    newly_added: int

    @property
    def duplicates(self) -> int:
        """Return the count of references that were already in the database."""
        return self.total - self.newly_added


class OciIngest:
    """Ingests OCI references from external sources into the import database.

    Handles deduplication and provides counts of what was ingested.
    This is the single entry point for getting OCI references into
    the system, regardless of the data source.
    """

    def __init__(self, db: ImportDatabase):
        self.db = db

    def from_script(self, script_path: Path) -> IngestResult:
        """Run an external script and ingest its stdout lines as OCI references.

        The script is expected to print one OCI reference per line to stdout.
        Blank lines are ignored.

        Args:
            script_path: Path to the executable script.

        Returns:
            IngestResult with counts of total and newly_added references.

        Raises:
            subprocess.CalledProcessError: If the script exits non-zero.
        """
        try:
            result = subprocess.run(
                [str(script_path)],
                capture_output=True,
                check=True,
                text=True,
            )

            lines = result.stdout.strip().split("\n")

            if not lines:
                print("WARNING: No OCI references returned from fetch script", file=sys.stderr)
                return IngestResult(total=0, newly_added=0)

            return self.from_lines(lines)

        except subprocess.CalledProcessError as e:
            print(f"ERROR: Fetch script failed: {e.stderr}", file=sys.stderr)
            raise

    def from_quay(self, client: QuayClient) -> IngestResult:
        """Fetch OCI references from Quay and ingest them into the database.

        Args:
            client: A configured QuayClient instance.

        Returns:
            IngestResult with counts of total and newly_added references.
        """
        refs = client.fetch_oci_references()
        if not refs:
            print("WARNING: No OCI references returned from Quay", file=sys.stderr)
            return IngestResult(total=0, newly_added=0)
        return self.from_lines(refs)

    def from_manifest(self, manifest_path: Path) -> IngestResult:
        """Ingest OCI references from a consolidated build manifest (YAML).

        Each library entry is expected to have ``output.artifact.tag`` and/or
        ``output.artifact.digest``.  When both are present the tag and digest are
        combined into a ``repo:tag@sha256:hex`` reference so the destination image
        gets a meaningful tag.  When only a digest is present it is used as-is.

        Args:
            manifest_path: Path to the consolidated YAML manifest.

        Returns:
            IngestResult with counts of total valid references and newly_added ones.
        """
        import yaml  # already a project dependency

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
                ref = f"{tag_ref}@{digest_suffix}"
            elif digest_ref:
                ref = digest_ref
            elif tag_ref:
                ref = tag_ref
            else:
                continue

            refs.append(ref)

        if not refs:
            print("WARNING: No OCI references found in manifest", file=sys.stderr)
            return IngestResult(total=0, newly_added=0)

        return self.from_lines(refs)

    def from_lines(self, lines: Iterable[str]) -> IngestResult:
        """Ingest OCI references from an iterable of strings.

        Blank lines and comment lines (starting with #) are skipped.
        Duplicates already in the database are silently ignored.

        Args:
            lines: An iterable of strings, each potentially containing an OCI reference.

        Returns:
            IngestResult with counts of total valid references and newly_added ones.
        """
        valid_refs = [line.strip() for line in lines if self._is_valid_reference(line)]

        newly_added = 0
        for oci_ref in valid_refs:
            _, was_inserted = self.db.add_oci_reference(oci_ref)
            if was_inserted:
                newly_added += 1

        return IngestResult(total=len(valid_refs), newly_added=newly_added)

    @staticmethod
    def _is_valid_reference(line: str) -> bool:
        """Return True if the line contains a valid OCI reference (not blank or a comment)."""
        stripped = line.strip()
        return stripped != "" and not stripped.startswith("#")
