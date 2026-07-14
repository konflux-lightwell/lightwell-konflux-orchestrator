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

import textwrap
from pathlib import Path

import pytest

from import_orchestrator.cli import main, make_parser
from import_orchestrator.database import ImportDatabase
from import_orchestrator.models import ImportStatus

SIMPLE_MANIFEST = textwrap.dedent("""\
    libraries:
      - output:
          artifact:
            tag: "quay.io/ns/repo:build-1"
            digest: "quay.io/ns/repo@sha256:aaa"
""")


class TestImportManifestArgParsing:
    """Test argument parsing for the 'import-manifest' subcommand."""

    def test_parses_file_argument(self):
        parser = make_parser()
        args = parser.parse_args(["import-manifest", "/tmp/consolidated.yaml"])
        assert args.file == Path("/tmp/consolidated.yaml")
        assert args.command == "import-manifest"

    def test_file_is_required(self):
        parser = make_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["import-manifest"])

    def test_inherits_top_level_db_flag(self):
        parser = make_parser()
        args = parser.parse_args(["--db", "/tmp/custom.db", "import-manifest", "manifest.yaml"])
        assert args.db == Path("/tmp/custom.db")

    def test_has_func_attribute(self):
        parser = make_parser()
        args = parser.parse_args(["import-manifest", "manifest.yaml"])
        assert hasattr(args, "func")
        assert callable(args.func)


class TestMainImportManifest:
    """Test the import-manifest subcommand through the full CLI entry point."""

    def test_missing_file_returns_2(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "import-manifest", "/nonexistent/manifest.yaml"],
        )
        exit_code = main()
        assert exit_code == 2
        captured = capsys.readouterr()
        assert "file not found" in captured.err

    def test_successful_import_returns_0(self, monkeypatch, tmp_path: Path):
        manifest = tmp_path / "consolidated.yaml"
        manifest.write_text(SIMPLE_MANIFEST)

        db_path = tmp_path / "test.db"
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--db", str(db_path), "import-manifest", str(manifest)],
        )

        exit_code = main()
        assert exit_code == 0

        with ImportDatabase(db_path) as db:
            pending = db.get_by_status(ImportStatus.PENDING)
            assert len(pending) == 1

    def test_works_with_reset_flag(self, monkeypatch, tmp_path: Path):
        db_path = tmp_path / "test.db"
        with ImportDatabase(db_path) as db:
            db.add_oci_reference("quay.io/ns/repo:old@sha256:old")

        manifest = tmp_path / "consolidated.yaml"
        manifest.write_text(SIMPLE_MANIFEST)

        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--db", str(db_path), "--reset", "import-manifest", str(manifest)],
        )

        exit_code = main()
        assert exit_code == 0

        with ImportDatabase(db_path) as db:
            pending = db.get_by_status(ImportStatus.PENDING)
            assert len(pending) == 1
