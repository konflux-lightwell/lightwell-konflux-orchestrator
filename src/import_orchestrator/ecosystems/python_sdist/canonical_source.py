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

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from import_orchestrator.ecosystems.python.build_config import (
    get_build_config_revision,
    get_build_config_source,
)
from import_orchestrator.engine.errors import TriggerError

# ASSUMPTION (unverified against the real calunga-python repo — flag for human review
# before this ships): calunga-python's package build-configs live under a top-level
# "packages/" directory in the repo, i.e. `<clone_dir>/packages`. Confirm against
# `calunga-python/scripts/get-canonical-source.py`'s own `--packages-dir` argument
# description/default before relying on this in production.
_PACKAGES_SUBDIR = "packages"

# ASSUMPTION: the script lives at this fixed path within calunga-python. Also unverified.
_SCRIPT_RELATIVE_PATH = Path("scripts") / "get-canonical-source.py"

# The only two vocabulary values canonical_source.type may take. Anything else -- a
# missing/None type, or an unrecognized string like "wheel" -- is treated as a
# malformed/drifted calunga-python result and fails closed via TriggerError, rather
# than silently passing an invented value through to callers (Task 9's ecosystem.py
# branches on type == "git" and would otherwise silently route anything else, e.g.
# "wheel", down the sdist path as if it were ordinary pypi-sdist).
_VALID_SOURCE_TYPES = ("git", "pypi-sdist")


def resolve_canonical_source(package: str, version: str) -> dict:
    """Resolve calunga-python's canonical-source verdict for a package/version.

    Shallow-clones the configured build-config repository (calunga-python) and
    runs its ``get-canonical-source.py`` script to determine whether this
    package/version should be ingested from PyPI sdist or from an upstream Git
    checkout.

    Returns:
        ``{"type": "pypi-sdist"}`` or ``{"type": "git", "upstream_url": str, "ref": str}``.
        Never includes ``internal_url`` or any other field calunga-python's script
        may emit, even if present in its output.

    Raises:
        TriggerError: If ``BUILD_CONFIGS_REPO_URL``/``BUILD_CONFIGS_REVISION`` are
            misconfigured, the clone or script invocation fails, the script's
            output is not valid JSON, the script reports a ``type`` other than
            ``"git"``/``"pypi-sdist"`` (including a missing/``None`` type), or it
            reports ``type: "git"`` without both ``upstream_url`` and ``ref``.
            Fails closed rather than silently falling back to sdist or passing an
            unrecognized type through.
    """
    repo_url = get_build_config_source()
    revision = get_build_config_revision()

    clone_dir = _clone_build_configs(repo_url, revision)
    try:
        result = _run_get_canonical_source(clone_dir, package, version)
    finally:
        shutil.rmtree(clone_dir, ignore_errors=True)

    source_type = result.get("type")
    if source_type not in _VALID_SOURCE_TYPES:
        raise TriggerError(
            f"calunga-python build-config for {package}=={version} reported an "
            f"unrecognized canonical_source type {source_type!r} (expected one of "
            f"{_VALID_SOURCE_TYPES}): {result!r}"
        )

    if source_type == "git":
        upstream_url = result.get("upstream_url")
        ref = result.get("ref")
        if not upstream_url or not ref:
            raise TriggerError(
                f"calunga-python build-config for {package}=={version} declares "
                f"type 'git' but is missing upstream_url and/or ref: {result!r}"
            )
        return {"type": "git", "upstream_url": upstream_url, "ref": ref}

    return {"type": source_type}


def _clone_build_configs(repo_url: str, revision: str) -> Path:
    """Shallow-clone the build-config repository at ``repo_url``@``revision``.

    Returns the path to a fresh temporary directory containing the clone. Callers
    are responsible for removing it once done.

    Note: `import_orchestrator.clients.git.clone()`/`GitClient` don't support
    cloning a specific ref, only the remote's default branch, so this clones
    directly via `git clone --branch <revision>` instead of reusing them.

    Caveat: `--branch` resolves branch and tag names reliably, but will not
    fetch an arbitrary bare commit SHA that isn't itself the tip of some ref,
    even though `get_build_config_revision()` is documented elsewhere as
    accepting "a valid Git revision" (which could be a SHA). In practice
    `BUILD_CONFIGS_REVISION` defaults to "main" and is expected to stay
    branch/tag-shaped; revisit this if it's ever pinned to a bare SHA.

    Raises:
        TriggerError: If git is unavailable or the clone fails (e.g. unknown
            revision, unreachable repo).
    """
    dest = Path(tempfile.mkdtemp(prefix="calunga-python-"))
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", revision, repo_url, str(dest)],
            check=True,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise TriggerError(f"unable to run git clone for build-config repo {repo_url}: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise TriggerError(f"failed to clone build-config repo {repo_url}@{revision}: {exc.stderr.strip()}") from exc
    return dest


def _run_get_canonical_source(clone_dir: Path, package: str, version: str) -> dict:
    """Run calunga-python's get-canonical-source.py and parse its JSON output.

    Raises:
        TriggerError: If the script is unavailable, exits non-zero, or its
            stdout is not valid JSON.
    """
    script = clone_dir / _SCRIPT_RELATIVE_PATH
    packages_dir = clone_dir / _PACKAGES_SUBDIR
    try:
        result = subprocess.run(
            ["python3", str(script), package, version, "--packages-dir", str(packages_dir)],
            check=True,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise TriggerError(f"unable to run calunga-python's get-canonical-source.py: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        raise TriggerError(
            f"calunga-python get-canonical-source.py failed for {package}=={version}: {exc.stderr.strip()}"
        ) from exc

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise TriggerError(
            f"calunga-python get-canonical-source.py produced invalid JSON for {package}=={version}: {exc}"
        ) from exc
