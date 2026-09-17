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

from collections.abc import Iterable
from dataclasses import dataclass

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


class Ingest:
    """Ingests items from external sources into the import database.

    Handles deduplication and provides counts of what was ingested.
    This is the single entry point for getting items into
    the system, regardless of the data source.
    """

    def __init__(self, db: ImportDatabase):
        self.db = db

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
        for ref in valid_refs:
            _, was_inserted = self.db.add_item(ref)
            if was_inserted:
                newly_added += 1

        return IngestResult(total=len(valid_refs), newly_added=newly_added)

    @staticmethod
    def _is_valid_reference(line: str) -> bool:
        """Return True if the line contains a valid OCI reference (not blank or a comment)."""
        stripped = line.strip()
        return stripped != "" and not stripped.startswith("#")
