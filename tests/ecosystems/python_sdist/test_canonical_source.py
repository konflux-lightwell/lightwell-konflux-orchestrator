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

import shutil
import subprocess
from pathlib import Path

import pytest

from import_orchestrator.ecosystems.python_sdist import canonical_source
from import_orchestrator.ecosystems.python_sdist.canonical_source import (
    _clone_build_configs,
    _run_get_canonical_source,
    resolve_canonical_source,
)
from import_orchestrator.engine.errors import TriggerError


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def build_configs_repo(tmp_path: Path) -> Path:
    """A real local git repo standing in for calunga-python, on a non-default branch."""
    repo = tmp_path / "upstream"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "release")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "marker.txt").write_text("release-branch-content")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def _stub_clone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Replace the real git clone with a no-op that hands back a fake checkout dir.

    Uses a dedicated subdirectory (not tmp_path itself) because
    resolve_canonical_source removes the returned clone dir once it's done.
    """
    monkeypatch.setenv("BUILD_CONFIGS_REPO_URL", "https://example.test/calunga-python")
    clone_dir = tmp_path / "calunga-python-clone"
    clone_dir.mkdir()
    monkeypatch.setattr(canonical_source, "_clone_build_configs", lambda repo_url, revision: clone_dir)


def _stub_script(monkeypatch: pytest.MonkeyPatch, result: dict) -> None:
    """Replace the calunga-python script invocation with a canned result dict."""
    monkeypatch.setattr(
        canonical_source,
        "_run_get_canonical_source",
        lambda clone_dir, package, version: result,
    )


class TestResolveCanonicalSource:
    def test_resolve_canonical_source_git(self, monkeypatch, tmp_path):
        _stub_clone(monkeypatch, tmp_path)
        _stub_script(
            monkeypatch,
            {
                "type": "git",
                "upstream_url": "https://github.com/mkdocstrings/griffe",
                "ref": "2.3.2",
            },
        )

        result = resolve_canonical_source("griffe", "2.3.2")

        assert result == {
            "type": "git",
            "upstream_url": "https://github.com/mkdocstrings/griffe",
            "ref": "2.3.2",
        }

    def test_resolve_canonical_source_defaults_to_pypi_sdist(self, monkeypatch, tmp_path):
        _stub_clone(monkeypatch, tmp_path)
        _stub_script(monkeypatch, {"type": "pypi-sdist"})

        assert resolve_canonical_source("sniffio", "1.3.1") == {"type": "pypi-sdist"}

    def test_resolve_canonical_source_raises_on_malformed_git_entry(self, monkeypatch, tmp_path):
        _stub_clone(monkeypatch, tmp_path)
        _stub_script(monkeypatch, {"type": "git"})

        with pytest.raises(TriggerError):
            resolve_canonical_source("broken-pkg", "1.0.0")

    def test_resolve_canonical_source_raises_when_ref_missing(self, monkeypatch, tmp_path):
        _stub_clone(monkeypatch, tmp_path)
        _stub_script(
            monkeypatch,
            {"type": "git", "upstream_url": "https://github.com/acme/widgets"},
        )

        with pytest.raises(TriggerError):
            resolve_canonical_source("broken-pkg", "1.0.0")

    def test_resolve_canonical_source_raises_on_missing_type(self, monkeypatch, tmp_path):
        _stub_clone(monkeypatch, tmp_path)
        _stub_script(monkeypatch, {})

        with pytest.raises(TriggerError):
            resolve_canonical_source("broken-pkg", "1.0.0")

    def test_resolve_canonical_source_raises_on_unrecognized_type(self, monkeypatch, tmp_path):
        _stub_clone(monkeypatch, tmp_path)
        _stub_script(monkeypatch, {"type": "wheel"})

        with pytest.raises(TriggerError):
            resolve_canonical_source("broken-pkg", "1.0.0")

    def test_resolve_canonical_source_never_leaks_internal_url(self, monkeypatch, tmp_path):
        _stub_clone(monkeypatch, tmp_path)
        _stub_script(
            monkeypatch,
            {
                "type": "git",
                "upstream_url": "https://github.com/mkdocstrings/griffe",
                "ref": "2.3.2",
                "internal_url": "https://internal.example.test/mirror/griffe",
            },
        )

        result = resolve_canonical_source("griffe", "2.3.2")

        assert "internal_url" not in result

    def test_resolve_canonical_source_requires_build_configs_repo_url(self, monkeypatch, tmp_path):
        monkeypatch.delenv("BUILD_CONFIGS_REPO_URL", raising=False)

        with pytest.raises(TriggerError):
            resolve_canonical_source("griffe", "2.3.2")


class TestCloneBuildConfigs:
    """Exercises the real git clone, not a mock, against a local repo."""

    def test_clones_the_requested_revision(self, build_configs_repo: Path):
        clone_dir = _clone_build_configs(str(build_configs_repo), "release")
        try:
            assert (clone_dir / "marker.txt").read_text() == "release-branch-content"
        finally:
            shutil.rmtree(clone_dir, ignore_errors=True)

    def test_unknown_revision_raises_trigger_error(self, build_configs_repo: Path):
        with pytest.raises(TriggerError):
            _clone_build_configs(str(build_configs_repo), "no-such-branch")

    def test_unreachable_repo_raises_trigger_error(self, tmp_path: Path):
        with pytest.raises(TriggerError):
            _clone_build_configs(str(tmp_path / "does-not-exist"), "main")


class TestRunGetCanonicalSource:
    """Exercises the real subprocess call against a tiny stand-in script."""

    def _write_script(self, clone_dir: Path, body: str) -> None:
        script = clone_dir / "scripts" / "get-canonical-source.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(body)

    def test_parses_script_stdout_as_json(self, tmp_path: Path):
        self._write_script(
            tmp_path,
            "import json, sys\nprint(json.dumps({'type': 'git', 'upstream_url': 'https://x', 'ref': 'v1'}))\n",
        )

        result = _run_get_canonical_source(tmp_path, "pkg", "1.0.0")

        assert result == {"type": "git", "upstream_url": "https://x", "ref": "v1"}

    def test_nonzero_exit_raises_trigger_error(self, tmp_path: Path):
        self._write_script(
            tmp_path,
            "import sys\nsys.stderr.write('boom')\nsys.exit(1)\n",
        )

        with pytest.raises(TriggerError):
            _run_get_canonical_source(tmp_path, "pkg", "1.0.0")

    def test_invalid_json_raises_trigger_error(self, tmp_path: Path):
        self._write_script(tmp_path, "print('not json')\n")

        with pytest.raises(TriggerError):
            _run_get_canonical_source(tmp_path, "pkg", "1.0.0")

    def test_missing_script_raises_trigger_error(self, tmp_path: Path):
        # No script written at all -- python3 exits non-zero (file not found).
        with pytest.raises(TriggerError):
            _run_get_canonical_source(tmp_path, "pkg", "1.0.0")

    def test_passes_package_version_and_packages_dir(self, tmp_path: Path):
        self._write_script(
            tmp_path,
            "import json, sys\nprint(json.dumps({'argv': sys.argv[1:]}))\n",
        )

        result = _run_get_canonical_source(tmp_path, "griffe", "2.3.2")

        assert result["argv"] == ["griffe", "2.3.2", "--packages-dir", str(tmp_path / "packages")]
