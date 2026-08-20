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

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from import_orchestrator.clients import QuayClient
from import_orchestrator.database import ImportDatabase
from import_orchestrator.ecosystems.java.parser import parse_manifest
from import_orchestrator.engine import Ingest, IngestResult


@pytest.fixture
def db(tmp_path: Path):
    """Create a temporary database for testing."""
    db_path = tmp_path / "test.db"
    with ImportDatabase(db_path) as database:
        yield database


@pytest.fixture
def ingest(db: ImportDatabase):
    """Create an Ingest instance with a test database."""
    return Ingest(db)


class TestIngestResult:
    """Test the IngestResult dataclass."""

    def test_duplicates_property(self):
        result = IngestResult(total=10, newly_added=7)
        assert result.duplicates == 3

    def test_no_duplicates(self):
        result = IngestResult(total=5, newly_added=5)
        assert result.duplicates == 0

    def test_all_duplicates(self):
        result = IngestResult(total=8, newly_added=0)
        assert result.duplicates == 8


class TestFromScript:
    """Test the from_script method."""

    @patch("import_orchestrator.engine.ingest.subprocess.run")
    def test_stores_fetched_refs(self, mock_run, ingest: Ingest, tmp_path: Path):
        """Verify that OCI references from script stdout are stored in the database."""
        script = tmp_path / "fetch.sh"
        script.write_text("#!/bin/bash\necho 'ref1'\necho 'ref2'")

        mock_result = MagicMock()
        mock_result.stdout = "oci://example.com/foo:tag1\noci://example.com/bar:tag2\n"
        mock_run.return_value = mock_result

        result = ingest.from_script(script)

        assert result.total == 2
        assert result.newly_added == 2
        assert result.duplicates == 0
        mock_run.assert_called_once()

    @patch("import_orchestrator.engine.ingest.subprocess.run")
    def test_handles_duplicates(self, mock_run, ingest: Ingest, tmp_path: Path):
        """Verify that duplicate references are not re-added."""
        script = tmp_path / "fetch.sh"

        ingest.db.add_item("oci://example.com/foo:tag1")

        mock_result = MagicMock()
        mock_result.stdout = "oci://example.com/foo:tag1\noci://example.com/bar:tag2\n"
        mock_run.return_value = mock_result

        result = ingest.from_script(script)

        assert result.total == 2
        assert result.newly_added == 1
        assert result.duplicates == 1

    @patch("import_orchestrator.engine.ingest.subprocess.run")
    def test_empty_output_returns_zero(self, mock_run, ingest: Ingest, tmp_path: Path):
        """Verify that empty script output returns zero counts."""
        script = tmp_path / "fetch.sh"

        mock_result = MagicMock()
        mock_result.stdout = "\n\n  \n"
        mock_run.return_value = mock_result

        result = ingest.from_script(script)

        assert result.total == 0
        assert result.newly_added == 0

    @patch("import_orchestrator.engine.ingest.subprocess.run")
    def test_script_failure_raises(self, mock_run, ingest: Ingest, tmp_path: Path):
        """Verify that subprocess failures are propagated."""
        script = tmp_path / "fetch.sh"

        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd=[str(script)],
            stderr="script failed",
        )

        with pytest.raises(subprocess.CalledProcessError):
            ingest.from_script(script)


class TestFromLines:
    """Test the from_lines method."""

    def test_ingests_valid_lines(self, ingest: Ingest):
        """Verify that valid OCI references are ingested from lines."""
        lines = [
            "oci://example.com/foo:tag1",
            "oci://example.com/bar:tag2",
            "oci://example.com/baz:tag3",
        ]

        result = ingest.from_lines(lines)

        assert result.total == 3
        assert result.newly_added == 3

    def test_skips_blank_lines(self, ingest: Ingest):
        """Verify that blank lines are ignored."""
        lines = [
            "oci://example.com/foo:tag1",
            "",
            "  ",
            "oci://example.com/bar:tag2",
        ]

        result = ingest.from_lines(lines)

        assert result.total == 2
        assert result.newly_added == 2

    def test_skips_comment_lines(self, ingest: Ingest):
        """Verify that comment lines (starting with #) are ignored."""
        lines = [
            "# This is a comment",
            "oci://example.com/foo:tag1",
            "  # Another comment",
            "oci://example.com/bar:tag2",
        ]

        result = ingest.from_lines(lines)

        assert result.total == 2
        assert result.newly_added == 2

    def test_handles_duplicates(self, ingest: Ingest):
        """Verify that duplicate references in lines are handled correctly."""
        ingest.db.add_item("oci://example.com/foo:tag1")

        lines = [
            "oci://example.com/foo:tag1",
            "oci://example.com/bar:tag2",
            "oci://example.com/baz:tag3",
        ]

        result = ingest.from_lines(lines)

        assert result.total == 3
        assert result.newly_added == 2
        assert result.duplicates == 1

    def test_empty_lines_returns_zero(self, ingest: Ingest):
        """Verify that an empty list returns zero counts."""
        result = ingest.from_lines([])

        assert result.total == 0
        assert result.newly_added == 0

    def test_strips_whitespace(self, ingest: Ingest):
        """Verify that leading/trailing whitespace is stripped from references."""
        lines = [
            "  oci://example.com/foo:tag1  ",
            "\toci://example.com/bar:tag2\t",
        ]

        result = ingest.from_lines(lines)

        assert result.total == 2
        assert result.newly_added == 2


class TestFromManifest:
    """Integration tests for parse_manifest + Ingest.from_lines (DB round-trip)."""

    def test_combines_tag_and_digest(self, ingest: Ingest, tmp_path: Path):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            "libraries:\n"
            "  - output:\n"
            "      artifact:\n"
            '        tag: "quay.io/ns/repo:build-100"\n'
            '        digest: "quay.io/ns/repo@sha256:abcdef"\n'
        )

        result = ingest.from_lines(parse_manifest(manifest))

        assert result.total == 1
        assert result.newly_added == 1

    def test_combined_ref_format(self, ingest: Ingest, tmp_path: Path, db: ImportDatabase):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            "libraries:\n"
            "  - output:\n"
            "      artifact:\n"
            '        tag: "quay.io/ns/repo:build-100"\n'
            '        digest: "quay.io/ns/repo@sha256:abcdef"\n'
        )

        ingest.from_lines(parse_manifest(manifest))

        from import_orchestrator.models import ImportStatus

        pending = db.get_by_status(ImportStatus.PENDING)
        assert len(pending) == 1
        assert pending[0].ref == "quay.io/ns/repo:build-100@sha256:abcdef"

    def test_digest_only(self, ingest: Ingest, tmp_path: Path, db: ImportDatabase):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            'libraries:\n  - output:\n      artifact:\n        digest: "quay.io/ns/repo@sha256:abcdef"\n'
        )

        result = ingest.from_lines(parse_manifest(manifest))

        assert result.total == 1
        assert result.newly_added == 1

        from import_orchestrator.models import ImportStatus

        pending = db.get_by_status(ImportStatus.PENDING)
        assert pending[0].ref == "quay.io/ns/repo@sha256:abcdef"

    def test_tag_only(self, ingest: Ingest, tmp_path: Path, db: ImportDatabase):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text('libraries:\n  - output:\n      artifact:\n        tag: "quay.io/ns/repo:build-100"\n')

        result = ingest.from_lines(parse_manifest(manifest))

        assert result.total == 1
        assert result.newly_added == 1

        from import_orchestrator.models import ImportStatus

        pending = db.get_by_status(ImportStatus.PENDING)
        assert pending[0].ref == "quay.io/ns/repo:build-100"

    def test_skips_entries_with_no_tag_or_digest(self, ingest: Ingest, tmp_path: Path):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text("libraries:\n  - output:\n      artifact: {}\n  - output:\n      other_field: value\n")

        result = ingest.from_lines(parse_manifest(manifest))

        assert result.total == 0
        assert result.newly_added == 0

    def test_empty_libraries_returns_zero(self, ingest: Ingest, tmp_path: Path):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text("libraries: []\n")

        result = ingest.from_lines(parse_manifest(manifest))

        assert result.total == 0
        assert result.newly_added == 0

    def test_no_libraries_key_returns_zero(self, ingest: Ingest, tmp_path: Path):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text("other_key: value\n")

        result = ingest.from_lines(parse_manifest(manifest))

        assert result.total == 0
        assert result.newly_added == 0

    def test_multiple_libraries(self, ingest: Ingest, tmp_path: Path):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            "libraries:\n"
            "  - output:\n"
            "      artifact:\n"
            '        tag: "quay.io/ns/repo:build-1"\n'
            '        digest: "quay.io/ns/repo@sha256:aaa"\n'
            "  - output:\n"
            "      artifact:\n"
            '        tag: "quay.io/ns/repo:build-2"\n'
            '        digest: "quay.io/ns/repo@sha256:bbb"\n'
            "  - output:\n"
            "      artifact:\n"
            '        digest: "quay.io/ns/repo@sha256:ccc"\n'
        )

        result = ingest.from_lines(parse_manifest(manifest))

        assert result.total == 3
        assert result.newly_added == 3

    def test_handles_duplicates(self, ingest: Ingest, tmp_path: Path):
        ingest.db.add_item("quay.io/ns/repo:build-1@sha256:aaa")

        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            "libraries:\n"
            "  - output:\n"
            "      artifact:\n"
            '        tag: "quay.io/ns/repo:build-1"\n'
            '        digest: "quay.io/ns/repo@sha256:aaa"\n'
            "  - output:\n"
            "      artifact:\n"
            '        tag: "quay.io/ns/repo:build-2"\n'
            '        digest: "quay.io/ns/repo@sha256:bbb"\n'
        )

        result = ingest.from_lines(parse_manifest(manifest))

        assert result.total == 2
        assert result.newly_added == 1
        assert result.duplicates == 1

    def test_digest_without_at_sign_raises(self, ingest: Ingest, tmp_path: Path):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            "libraries:\n"
            "  - output:\n"
            "      artifact:\n"
            '        tag: "quay.io/ns/repo:build-100"\n'
            '        digest: "sha256:abcdef"\n'
        )

        with pytest.raises(ValueError, match="Malformed digest reference"):
            parse_manifest(manifest)


class TestFromQuay:
    """Test the from_quay method."""

    def test_ingests_refs_from_quay(self, ingest: Ingest):
        """Verify that OCI references from QuayClient are stored in the database."""
        mock_client = MagicMock(spec=QuayClient)
        mock_client.fetch_oci_references.return_value = [
            "quay.io/ns/repo:lw-build-1@sha256:aaa",
            "quay.io/ns/repo:lw-build-2@sha256:bbb",
        ]

        result = ingest.from_quay(mock_client)

        assert result.total == 2
        assert result.newly_added == 2
        mock_client.fetch_oci_references.assert_called_once_with()

    def test_handles_empty_response(self, ingest: Ingest):
        """Verify that an empty Quay response returns zero counts."""
        mock_client = MagicMock(spec=QuayClient)
        mock_client.fetch_oci_references.return_value = []

        result = ingest.from_quay(mock_client)

        assert result.total == 0
        assert result.newly_added == 0

    def test_handles_duplicates(self, ingest: Ingest):
        """Verify that refs already in the database are counted as duplicates."""
        ingest.db.add_item("quay.io/ns/repo:lw-build-1@sha256:aaa")

        mock_client = MagicMock(spec=QuayClient)
        mock_client.fetch_oci_references.return_value = [
            "quay.io/ns/repo:lw-build-1@sha256:aaa",
            "quay.io/ns/repo:lw-build-2@sha256:bbb",
        ]

        result = ingest.from_quay(mock_client)

        assert result.total == 2
        assert result.newly_added == 1
        assert result.duplicates == 1
