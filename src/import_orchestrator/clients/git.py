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
