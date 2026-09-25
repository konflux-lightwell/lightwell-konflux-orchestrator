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


class GitError(Exception):
    """The local ``git`` CLI failed or was unavailable."""


def clone(url: str, dest: Path, *, depth: int = 1) -> "GitClient":
    """Shallow-clone ``url`` into ``dest`` and return a client for it.

    The parent directory of ``dest`` is created if needed. Uses the local
    ``git`` CLI so it inherits the user's existing SSH/credential setup.

    Raises:
        GitError: If git is unavailable or the clone fails.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "clone", "--depth", str(depth), url, str(dest)],
            check=True,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise GitError(f"unable to run git clone: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        raise GitError(f"git clone {url} failed: {exc.stderr.strip()}") from exc
    return GitClient(dest)


class GitClient:
    """Thin wrapper over the local ``git`` CLI for a single repository.

    Each method runs one git command against ``repo_path`` and returns its raw
    stdout. Callers are responsible for interpreting, normalizing, or validating
    the returned values.
    """

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path

    def head_revision(self) -> str:
        """Return the full commit SHA of ``HEAD`` (``git rev-parse HEAD``)."""
        return self._run("rev-parse", "HEAD")

    def remote_url(self, name: str = "origin") -> str:
        """Return the configured URL for a remote (``git remote get-url``)."""
        return self._run("remote", "get-url", name)

    def _run(self, *args: str) -> str:
        """Run a git command against the repository and return stripped stdout.

        Raises:
            GitError: If git is unavailable or exits non-zero.
        """
        try:
            result = subprocess.run(
                ["git", "-C", str(self.repo_path), *args],
                check=True,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise GitError(f"unable to run git {' '.join(args)}: {exc}") from exc
        except subprocess.CalledProcessError as exc:
            raise GitError(f"git {' '.join(args)} failed: {exc.stderr.strip()}") from exc
        return result.stdout.strip()
