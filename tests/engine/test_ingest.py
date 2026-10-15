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

from import_orchestrator.database import WorkflowDatabase
from import_orchestrator.ecosystems.java.parser import parse_manifest
from import_orchestrator.engine import Ingest, IngestResult


def _identity(ref: str) -> dict[str, str]:
    """Map a ref to the seven Request fingerprint fields (one per artifact)."""
    return {
        "namespace": "ns",
        "application": "app",
        "component": "comp",
        "pipeline": "import-pnc",
        "release_plan": "rp",
        "target": "remediated",
        "artifact": ref,
    }


@pytest.fixture
def db(tmp_path: Path):
    """Create a temporary workflow database for testing."""
    db_path = tmp_path / "test.db"
    with WorkflowDatabase(db_path) as database:
        yield database


@pytest.fixture
def ingest(db: WorkflowDatabase):
    """Create an Ingest instance with a test database."""
    return Ingest(db, build_identity=_identity)


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
        """Verify that valid OCI references are ingested from lines as Requests."""
        lines = [
            "oci://example.com/foo:tag1",
            "oci://example.com/bar:tag2",
            "oci://example.com/baz:tag3",
        ]

        result = ingest.from_lines(lines)

        assert result.total == 3
        assert result.newly_added == 3
        assert ingest.db.count_requests() == 3

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
        """Verify that duplicate references (same fingerprint) are handled correctly."""
        ingest.db.get_or_create_request(**_identity("oci://example.com/foo:tag1"))

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
        artifacts = {r.artifact for r in ingest.db.list_requests()}
        assert artifacts == {"oci://example.com/foo:tag1", "oci://example.com/bar:tag2"}


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

    def test_combined_ref_format(self, ingest: Ingest, tmp_path: Path, db: WorkflowDatabase):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            "libraries:\n"
            "  - output:\n"
            "      artifact:\n"
            '        tag: "quay.io/ns/repo:build-100"\n'
            '        digest: "quay.io/ns/repo@sha256:abcdef"\n'
        )

        ingest.from_lines(parse_manifest(manifest))

        requests = db.list_requests()
        assert len(requests) == 1
        assert requests[0].artifact == "quay.io/ns/repo:build-100@sha256:abcdef"

    def test_digest_only(self, ingest: Ingest, tmp_path: Path, db: WorkflowDatabase):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            'libraries:\n  - output:\n      artifact:\n        digest: "quay.io/ns/repo@sha256:abcdef"\n'
        )

        result = ingest.from_lines(parse_manifest(manifest))

        assert result.total == 1
        assert result.newly_added == 1
        assert db.list_requests()[0].artifact == "quay.io/ns/repo@sha256:abcdef"

    def test_tag_only(self, ingest: Ingest, tmp_path: Path, db: WorkflowDatabase):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text('libraries:\n  - output:\n      artifact:\n        tag: "quay.io/ns/repo:build-100"\n')

        result = ingest.from_lines(parse_manifest(manifest))

        assert result.total == 1
        assert result.newly_added == 1
        assert db.list_requests()[0].artifact == "quay.io/ns/repo:build-100"

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
        ingest.db.get_or_create_request(**_identity("quay.io/ns/repo:build-1@sha256:aaa"))

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
