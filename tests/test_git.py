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
from unittest.mock import patch

import pytest

from import_orchestrator.clients import GitClient, GitError, clone


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


class TestUpdate:
    def test_update_fast_forwards_to_remote_tip(self, repo: Path, tmp_path: Path):
        dest = tmp_path / "checkout"
        client = clone(str(repo), dest)
        before = client.head_revision()

        # Advance the upstream repository by one commit.
        (repo / "file.txt").write_text("world")
        _git(repo, "commit", "-aqm", "second")
        upstream_head = GitClient(repo).head_revision()

        client.update()

        assert client.head_revision() == upstream_head
        assert client.head_revision() != before
        assert (dest / "file.txt").read_text() == "world"

    def test_update_discards_local_and_untracked_changes(self, repo: Path, tmp_path: Path):
        dest = tmp_path / "checkout"
        client = clone(str(repo), dest)

        # A tracked edit and an untracked file should both be wiped.
        (dest / "file.txt").write_text("local edit")
        (dest / "untracked.txt").write_text("stray")

        client.update()

        assert (dest / "file.txt").read_text() == "hello"
        assert not (dest / "untracked.txt").exists()

    def test_update_non_repository_raises_git_error(self, tmp_path: Path):
        with pytest.raises(GitError):
            GitClient(tmp_path).update()


class TestClone:
    def test_clone_creates_working_checkout(self, repo: Path, tmp_path: Path):
        dest = tmp_path / "nested" / "checkout"
        client = clone(str(repo), dest)
        assert isinstance(client, GitClient)
        assert client.repo_path == dest
        # The clone carries the source commit and is a usable repository.
        assert len(client.head_revision()) == 40
        assert (dest / "file.txt").read_text() == "hello"

    def test_clone_failure_raises_git_error(self, tmp_path: Path):
        not_a_repo = tmp_path / "empty"
        not_a_repo.mkdir()
        with pytest.raises(GitError, match="git clone .* failed"):
            clone(str(not_a_repo), tmp_path / "dest")

    def test_clone_missing_git_binary_raises_git_error(self, tmp_path: Path):
        with patch("import_orchestrator.clients.git.subprocess.run", side_effect=OSError("no git")):
            with pytest.raises(GitError, match="unable to run git clone"):
                clone("https://example.com/x.git", tmp_path / "dest")
