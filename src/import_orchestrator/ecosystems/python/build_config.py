"""Build-config repository source supplied by orchestrator runtime configuration."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from import_orchestrator.engine.errors import TriggerError

DEFAULT_BUILD_CONFIGS_REVISION = "main"


def get_build_config_revision() -> str:
    """Return a valid build-config Git revision supplied by deployment config."""
    revision = os.environ.get("BUILD_CONFIGS_REVISION", DEFAULT_BUILD_CONFIGS_REVISION).strip()
    if not revision or revision.startswith("-"):
        raise TriggerError("BUILD_CONFIGS_REVISION must be a non-empty Git revision")
    return revision


def get_build_config_source() -> str:
    """Read and validate the configured build-config repository URL."""
    repo_url = os.environ.get("BUILD_CONFIGS_REPO_URL", "").strip()
    if not repo_url:
        raise TriggerError("BUILD_CONFIGS_REPO_URL must be configured in the orchestrator environment")
    parsed = urlsplit(repo_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise TriggerError("BUILD_CONFIGS_REPO_URL must be an HTTPS URL without embedded credentials")
    return repo_url
