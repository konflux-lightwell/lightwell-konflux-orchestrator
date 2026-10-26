"""
PipelineRun creation that reconciles by deterministic name and retries only when safe.

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

import logging
import random
import time
from typing import TYPE_CHECKING, NoReturn

import requests

from import_orchestrator.pipelinerun import IMPORT_IDENTITY_ANNOTATION

if TYPE_CHECKING:
    # Type-only: clients.kube imports this module, so a runtime import of the clients package would be circular.
    from import_orchestrator.clients.kube_api import KubeAPI
    from import_orchestrator.engine.errors import TriggerError

# engine.errors is imported inside functions only. The engine package imports clients, and clients imports this
# module, so a module-level import would create a cycle.

_LOGGER = logging.getLogger(__name__)
_RETRYABLE_HTTP_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_PIPELINERUN_CREATE_MAX_ATTEMPTS = 3
_PIPELINERUN_RETRY_BASE_DELAY_SECONDS = 0.5


# The seams below are looked up at call time, so tests can replace them with module-level patches.
def _sleep(delay: float) -> None:
    """Pause for ``delay`` seconds before the next creation attempt."""
    time.sleep(delay)


def _jitter(low: float, high: float) -> float:
    """Return a random float between ``low`` and ``high`` for backoff."""
    return random.uniform(low, high)


def api_error_detail(exc: requests.RequestException) -> str:
    """Extract a human-readable message from a failed Kubernetes API response.

    Kubernetes returns a Status object whose ``message`` field explains why a
    request was rejected (e.g. pipeline validation errors). Surface it so a 400
    is actionable instead of an opaque "Bad Request".
    """
    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)
    try:
        body = response.json()
    except (ValueError, AttributeError):
        body = None
    if isinstance(body, dict) and body.get("message"):
        return f"{exc}: {body['message']}"
    text = (getattr(response, "text", "") or "").strip()
    return f"{exc}: {text}" if text else str(exc)


def http_status(exc: requests.RequestException) -> int | None:
    """Return the HTTP status code attached to ``exc``, or None for transport errors."""
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return status_code if isinstance(status_code, int) else None


def _is_retryable_create_failure(exc: requests.RequestException) -> bool:
    """Return whether a failed POST may be retried after reconciliation.

    Timeouts and connection errors may hide a POST the server already processed,
    so the caller checks for the object before any retry. A 409 and the retryable
    429 and 5xx statuses get the same treatment.
    """
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    status = http_status(exc)
    return status == 409 or status in _RETRYABLE_HTTP_STATUS_CODES


def _is_generate_name_manifest(manifest: dict) -> bool:
    """Return whether the cluster, not the client, picks this PipelineRun's name."""
    metadata = manifest.get("metadata")
    return isinstance(metadata, dict) and "name" not in metadata and isinstance(metadata.get("generateName"), str)


def _manifest_identity(manifest: dict) -> tuple[str, str]:
    """Return the deterministic ``(name, identity)`` pair of a named PipelineRun manifest.

    Raises:
        TriggerError: If the manifest lacks a name or the import identity annotation.
    """
    from import_orchestrator.engine.errors import TriggerError

    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict):
        raise TriggerError("PipelineRun manifest metadata is missing")
    name = metadata.get("name")
    annotations = metadata.get("annotations")
    identity = annotations.get(IMPORT_IDENTITY_ANNOTATION) if isinstance(annotations, dict) else None
    if not isinstance(name, str) or not name or not isinstance(identity, str) or not identity:
        raise TriggerError("PipelineRun manifest must contain metadata.name and import identity")
    return name, identity


def _has_matching_identity(existing: dict, identity: str) -> bool:
    """Return whether an existing PipelineRun carries this import's identity annotation."""
    metadata = existing.get("metadata")
    annotations = metadata.get("annotations") if isinstance(metadata, dict) else None
    return isinstance(annotations, dict) and annotations.get(IMPORT_IDENTITY_ANNOTATION) == identity


def _lookup_failure_message(
    name: str,
    lookup_error: requests.RequestException,
    failure: requests.RequestException | None,
) -> str:
    """Describe a failed ownership GET, adding the POST failure that prompted it when there was one."""
    message = f"PipelineRun {name} may exist; ownership lookup failed: {api_error_detail(lookup_error)}"
    if failure is None:
        return message
    return f"{message}; create failure: {api_error_detail(failure)}"


def _reconcile_pipelinerun(
    api: KubeAPI,
    api_path: str,
    name: str,
    identity: str,
    failure: requests.RequestException | None = None,
) -> bool:
    """Check whether the named PipelineRun already belongs to this import.

    Returns True for an owned object and False only for a confirmed 404. A
    failed lookup raises PipelineRunReconciliationError, because the object may
    exist and retrying could create a duplicate. An object with a different
    identity raises a plain TriggerError: our POST did not create that name, so
    a new attempt with a new name is safe.

    Raises:
        PipelineRunReconciliationError: If the lookup itself fails.
        TriggerError: If the object belongs to another import.
    """
    from import_orchestrator.engine.errors import PipelineRunReconciliationError, TriggerError

    try:
        existing = api.get(api_path)
    except requests.RequestException as error:
        if http_status(error) == 404:
            return False
        raise PipelineRunReconciliationError(name, _lookup_failure_message(name, error, failure)) from error
    if _has_matching_identity(existing, identity):
        return True
    raise TriggerError(f"PipelineRun {name} exists with a different import identity") from failure


def _create_failed_error(failure: requests.RequestException) -> TriggerError:
    """Build the TriggerError for a POST that must not be retried."""
    from import_orchestrator.engine.errors import TriggerError

    return TriggerError(f"failed to create PipelineRun: {api_error_detail(failure)}")


def _retry_delay(attempt: int) -> float | None:
    """Return a full-jitter delay before the next POST, or None when the attempt budget is spent.

    The jitter ceiling doubles after each failed attempt, starting at the base delay.
    """
    if attempt >= _PIPELINERUN_CREATE_MAX_ATTEMPTS:
        return None
    ceiling = _PIPELINERUN_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
    return min(ceiling, max(0.0, _jitter(0.0, ceiling)))


def _raise_retry_budget_exhausted(name: str, failure: requests.RequestException) -> NoReturn:
    """Log and raise the PipelineRunReconciliationError that ends a retry sequence.

    The last POST failed with a retryable error, and that POST may still land after
    the final lookup returned 404. So the create result is unclear, not failed. The
    caller records the attempt as PENDING, and the monitor confirms absence before
    any new attempt. The last API failure is both the exception cause and part of the message.
    """
    from import_orchestrator.engine.errors import PipelineRunReconciliationError

    _LOGGER.warning(
        "PipelineRun creation terminated name=%s reason=retry budget exhausted attempts=%d",
        name,
        _PIPELINERUN_CREATE_MAX_ATTEMPTS,
    )
    raise PipelineRunReconciliationError(
        name,
        f"PipelineRun {name} create retry budget exhausted after {_PIPELINERUN_CREATE_MAX_ATTEMPTS} POST attempts; "
        f"the run may still be created: {api_error_detail(failure)}",
    ) from failure


def _post_manifest(
    api: KubeAPI,
    collection_path: str,
    manifest: dict,
) -> tuple[str | None, requests.RequestException | None]:
    """POST the manifest once and return ``(name, None)`` on success or ``(None, failure)``.

    A response without ``metadata.name`` returns ``(None, None)``.
    """
    try:
        result = api.create(collection_path, manifest)
    except requests.RequestException as error:
        return None, error
    try:
        return result["metadata"]["name"], None
    except KeyError:
        return None, None


def _create_once(api: KubeAPI, collection_path: str, manifest: dict) -> str | None:
    """Submit a generateName manifest once. Retrying it could create a second PipelineRun."""
    name, failure = _post_manifest(api, collection_path, manifest)
    if failure is not None:
        raise _create_failed_error(failure) from failure
    return name


def _create_or_reconcile_pipelinerun(
    api: KubeAPI,
    collection_path: str,
    api_path: str,
    manifest: dict,
    name: str,
    identity: str,
) -> str:
    """POST a named PipelineRun, reconciling by name after each retryable failure.

    Non-retryable failures raise at once. Retryable failures first check whether the
    object exists with this import's identity, then wait and POST again while the
    attempt budget allows.

    Returns:
        The PipelineRun name. A response without ``metadata.name`` still returns
        ``name``, because the manifest fixes it.
    """
    for attempt in range(1, _PIPELINERUN_CREATE_MAX_ATTEMPTS + 1):
        result, failure = _post_manifest(api, collection_path, manifest)
        if failure is None:
            return result or name
        if not _is_retryable_create_failure(failure):
            raise _create_failed_error(failure) from failure
        if _reconcile_pipelinerun(api, api_path, name, identity, failure):
            return name
        delay = _retry_delay(attempt)
        if delay is None:
            _raise_retry_budget_exhausted(name, failure)
        _LOGGER.warning(
            "Retrying PipelineRun creation name=%s retry=%d/%d delay=%.3fs",
            name,
            attempt,
            _PIPELINERUN_CREATE_MAX_ATTEMPTS - 1,
            delay,
        )
        _sleep(delay)
    raise AssertionError("PipelineRun creation retry loop terminated unexpectedly")


def create_pipelinerun(api: KubeAPI, namespace: str, manifest: dict) -> str | None:
    """Create a PipelineRun, reusing or retrying its deterministic name when safe.

    A manifest with ``metadata.generateName`` has no name to reconcile, so it gets
    one POST and no retries. It returns None when the response has no name. A
    manifest with ``metadata.name`` must also carry the import identity annotation,
    and it always returns that name.

    Raises:
        TriggerError: If the manifest is invalid, the API rejects the request,
            or the PipelineRun is confirmed to belong to another import.
        PipelineRunReconciliationError: If a PipelineRun may exist and either the
            ownership lookup failed or the retry budget ran out after a retryable
            POST failure.
    """
    collection_path = f"/apis/tekton.dev/v1/namespaces/{namespace}/pipelineruns"
    if _is_generate_name_manifest(manifest):
        return _create_once(api, collection_path, manifest)
    name, identity = _manifest_identity(manifest)
    api_path = f"{collection_path}/{name}"
    if _reconcile_pipelinerun(api, api_path, name, identity):
        return name
    return _create_or_reconcile_pipelinerun(api, collection_path, api_path, manifest, name, identity)
