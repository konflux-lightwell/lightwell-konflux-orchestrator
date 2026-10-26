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

# Keep the established logger name while the implementation lives outside the client adapter.
_LOGGER = logging.getLogger("import_orchestrator.clients.kube")
_RETRYABLE_HTTP_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_PIPELINERUN_CREATE_MAX_ATTEMPTS = 3
_PIPELINERUN_CREATE_DEADLINE_SECONDS = 60.0
_PIPELINERUN_RETRY_BASE_DELAY_SECONDS = 0.5


# The seams below are looked up at call time, so tests can replace them with module-level patches.
def _monotonic() -> float:
    """Return the monotonic clock reading that bounds PipelineRun creation."""
    return time.monotonic()


def _sleep(delay: float) -> None:
    """Pause for ``delay`` seconds before the next creation attempt."""
    time.sleep(delay)


def _jitter(low: float, high: float) -> float:
    """Return a random float between ``low`` and ``high`` for backoff."""
    return random.uniform(low, high)


def _api_error_detail(exc: requests.RequestException) -> str:
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


def _http_status(exc: requests.RequestException) -> int | None:
    """Return the HTTP status code attached to ``exc``, or None for transport errors."""
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return status_code if isinstance(status_code, int) else None


def _is_ambiguous_timeout(exc: requests.RequestException) -> bool:
    """Return whether a timeout may have hidden a request the server already processed."""
    timeout_types = (requests.ConnectTimeout, requests.ReadTimeout)
    causes = (getattr(exc, "__cause__", None), getattr(exc, "__context__", None))
    return isinstance(exc, timeout_types) or any(isinstance(cause, timeout_types) for cause in causes)


def _is_connection_failure(exc: requests.RequestException) -> bool:
    """Return whether a connection failure may be retried after reconciliation."""
    causes = (getattr(exc, "__cause__", None), getattr(exc, "__context__", None))
    return isinstance(exc, requests.ConnectionError) or any(
        isinstance(cause, requests.ConnectionError) for cause in causes
    )


def _is_retryable_create_failure(exc: requests.RequestException) -> bool:
    """Return whether a failed POST may be retried after reconciliation."""
    return (
        _is_ambiguous_timeout(exc)
        or _is_connection_failure(exc)
        or _http_status(exc) == 409
        or _http_status(exc) in _RETRYABLE_HTTP_STATUS_CODES
    )


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
    """Describe a failed reconciliation GET, including the POST failure that triggered it."""
    if failure is None:
        return f"failed to check PipelineRun {name}: {_api_error_detail(lookup_error)}"
    if _http_status(failure) == 409:
        return (
            f"PipelineRun {name} creation conflicted and reconciliation failed: "
            f"{_api_error_detail(lookup_error)}; original conflict: {_api_error_detail(failure)}"
        )
    return (
        f"PipelineRun {name} retry reconciliation failed: "
        f"{_api_error_detail(lookup_error)}; original failure: {_api_error_detail(failure)}"
    )


def _mismatch_message(name: str, failure: requests.RequestException | None) -> str:
    """Describe an existing PipelineRun owned by a different import."""
    if failure is None:
        return f"PipelineRun {name} exists but has a different import identity"
    if _http_status(failure) == 409:
        return f"PipelineRun {name} conflict resolved to a different import identity"
    return f"PipelineRun {name} retry reconciliation resolved to a different import identity"


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
        if _http_status(error) == 404:
            return False
        raise PipelineRunReconciliationError(name, _lookup_failure_message(name, error, failure)) from error
    if _has_matching_identity(existing, identity):
        return True
    raise TriggerError(_mismatch_message(name, failure)) from failure


def _create_failed_error(failure: requests.RequestException) -> TriggerError:
    """Build the TriggerError for a POST that must not be retried."""
    from import_orchestrator.engine.errors import TriggerError

    return TriggerError(f"failed to create PipelineRun: {_api_error_detail(failure)}")


def _retry_delay(attempt: int, deadline: float) -> float | None:
    """Return a bounded full-jitter delay, or None when another POST is unsafe.

    The delay must end before the deadline. Otherwise the next POST would start too late.
    """
    if attempt >= _PIPELINERUN_CREATE_MAX_ATTEMPTS:
        return None
    remaining = deadline - _monotonic()
    if remaining <= 0:
        return None
    ceiling = _PIPELINERUN_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
    delay = min(ceiling, max(0.0, _jitter(0.0, ceiling)))
    return delay if delay < remaining else None


def _retry_termination_reason(attempt: int) -> str:
    """Name why retries stopped after ``attempt`` POSTs."""
    return "retry budget exhausted" if attempt >= _PIPELINERUN_CREATE_MAX_ATTEMPTS else "retry deadline exceeded"


def _raise_create_retry_termination(
    name: str,
    attempt: int,
    reason: str,
    failure: requests.RequestException | None,
) -> NoReturn:
    """Log and raise the TriggerError that ends a retry sequence.

    The last API failure, when there is one, is both the exception cause and part of the message.
    """
    from import_orchestrator.engine.errors import TriggerError

    _LOGGER.warning("PipelineRun creation terminated name=%s reason=%s attempts=%d", name, reason, attempt)
    detail = f": {_api_error_detail(failure)}" if failure is not None else ""
    raise TriggerError(f"PipelineRun {name} creation {reason} after {attempt} POST attempts{detail}") from failure


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


def _post_before_deadline(
    api: KubeAPI,
    collection_path: str,
    manifest: dict,
    name: str,
    attempt: int,
    deadline: float,
    last_failure: requests.RequestException | None,
) -> tuple[str | None, requests.RequestException | None]:
    """Check the deadline, then submit one POST.

    The deadline is checked right before the POST, so a long sleep cannot push a request past it.
    """
    if _monotonic() >= deadline:
        _raise_create_retry_termination(name, attempt - 1, "retry deadline exceeded", last_failure)
    return _post_manifest(api, collection_path, manifest)


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
    deadline: float,
) -> str | None:
    """POST a named PipelineRun, reconciling by name after each retryable failure.

    Non-retryable failures raise at once. Retryable failures first check whether the
    object exists with this import's identity, then wait and try again while the
    attempt budget and deadline allow.
    """
    last_failure: requests.RequestException | None = None
    for attempt in range(1, _PIPELINERUN_CREATE_MAX_ATTEMPTS + 1):
        result, failure = _post_before_deadline(
            api,
            collection_path,
            manifest,
            name,
            attempt,
            deadline,
            last_failure,
        )
        if failure is None:
            return result
        last_failure = failure
        if not _is_retryable_create_failure(failure):
            raise _create_failed_error(failure) from failure
        if _reconcile_pipelinerun(api, api_path, name, identity, failure):
            return name
        delay = _retry_delay(attempt, deadline)
        if delay is None:
            _raise_create_retry_termination(name, attempt, _retry_termination_reason(attempt), failure)
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
    one POST and no retries. A manifest with ``metadata.name`` must also carry the
    import identity annotation. Returns None only when the response has no name.

    Raises:
        TriggerError: If the manifest is invalid, the API rejects the request,
            or retries end without a confirmed PipelineRun.
        PipelineRunReconciliationError: If a PipelineRun may exist and the
            lookup that would prove it belongs to this import failed.
    """
    collection_path = f"/apis/tekton.dev/v1/namespaces/{namespace}/pipelineruns"
    if _is_generate_name_manifest(manifest):
        return _create_once(api, collection_path, manifest)
    name, identity = _manifest_identity(manifest)
    api_path = f"{collection_path}/{name}"
    deadline = _monotonic() + _PIPELINERUN_CREATE_DEADLINE_SECONDS
    if _reconcile_pipelinerun(api, api_path, name, identity):
        return name
    return _create_or_reconcile_pipelinerun(api, collection_path, api_path, manifest, name, identity, deadline)
