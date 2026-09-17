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

from pathlib import Path

import pytest

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
