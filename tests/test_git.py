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
from pathlib import Path

import pytest

from import_orchestrator.clients import GitClient, GitError


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Initialize a git repository with one commit and an origin remote."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "remote", "add", "origin", "https://github.com/acme/widgets.git")
    (tmp_path / "file.txt").write_text("hello")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "initial")
    return tmp_path


class TestGitClient:
    def test_head_revision_is_full_sha(self, repo: Path):
        revision = GitClient(repo).head_revision()
        assert len(revision) == 40
        assert all(char in "0123456789abcdef" for char in revision)

    def test_remote_url_returns_origin(self, repo: Path):
        assert GitClient(repo).remote_url("origin") == "https://github.com/acme/widgets.git"

    def test_missing_remote_raises_git_error(self, repo: Path):
        with pytest.raises(GitError):
            GitClient(repo).remote_url("upstream")

    def test_non_repository_raises_git_error(self, tmp_path: Path):
        with pytest.raises(GitError):
            GitClient(tmp_path).head_revision()
