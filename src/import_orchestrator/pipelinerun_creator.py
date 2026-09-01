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
from collections.abc import Callable

import requests

from import_orchestrator.clients.kube_api import KubeAPI
from import_orchestrator.pipelinerun import IMPORT_IDENTITY_ANNOTATION

# Keep the established logger name while the implementation lives outside the client adapter.
_LOGGER = logging.getLogger("import_orchestrator.clients.kube")
_RETRYABLE_HTTP_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_PIPELINERUN_CREATE_MAX_ATTEMPTS = 3
_PIPELINERUN_CREATE_DEADLINE_SECONDS = 60.0
_PIPELINERUN_RETRY_BASE_DELAY_SECONDS = 0.5


def _monotonic() -> float:
    return time.monotonic()


def _sleep(delay: float) -> None:
    time.sleep(delay)


def _jitter(low: float, high: float) -> float:
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
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return status_code if isinstance(status_code, int) else None


def _is_ambiguous_timeout(exc: requests.RequestException) -> bool:
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


def _manifest_identity(manifest: dict) -> tuple[str, str]:
    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("PipelineRun manifest metadata is missing")
    name = metadata.get("name")
    annotations = metadata.get("annotations")
    identity = annotations.get(IMPORT_IDENTITY_ANNOTATION) if isinstance(annotations, dict) else None
    if not isinstance(name, str) or not name or not isinstance(identity, str) or not identity:
        raise ValueError("PipelineRun manifest must contain metadata.name and import identity")
    return name, identity


def _has_matching_identity(existing: dict, identity: str) -> bool:
    metadata = existing.get("metadata")
    annotations = metadata.get("annotations") if isinstance(metadata, dict) else None
    return isinstance(annotations, dict) and annotations.get(IMPORT_IDENTITY_ANNOTATION) == identity


def _reconcile_pipelinerun(
    api: KubeAPI,
    api_path: str,
    name: str,
    identity: str,
    failure: requests.RequestException | None = None,
) -> bool:
    """Return true for an owned object and false only for a confirmed 404."""
    from import_orchestrator.engine.errors import PipelineRunReconciliationError

    try:
        existing = api.get(api_path)
    except requests.RequestException as error:
        if _http_status(error) == 404:
            return False
        if failure is None:
            raise PipelineRunReconciliationError(
                name,
                f"failed to check PipelineRun {name}: {_api_error_detail(error)}",
            ) from error
        if _http_status(failure) == 409:
            message = (
                f"PipelineRun {name} creation conflicted and reconciliation failed: "
                f"{_api_error_detail(error)}; original conflict: {_api_error_detail(failure)}"
            )
        else:
            message = (
                f"PipelineRun {name} retry reconciliation failed: "
                f"{_api_error_detail(error)}; original failure: {_api_error_detail(failure)}"
            )
        raise PipelineRunReconciliationError(name, message) from error
    if _has_matching_identity(existing, identity):
        return True
    if failure is None:
        error = ValueError(f"PipelineRun {name} exists but has a different import identity")
        raise PipelineRunReconciliationError(name, str(error)) from error
    is_conflict = _http_status(failure) == 409
    message = (
        f"PipelineRun {name} conflict resolved to a different import identity"
        if is_conflict
        else f"PipelineRun {name} retry reconciliation resolved to a different import identity"
    )
    raise PipelineRunReconciliationError(
        name,
        message,
    ) from failure


def _validated_manifest_identity(manifest: dict) -> tuple[str, str]:
    from import_orchestrator.engine.errors import TriggerError

    try:
        return _manifest_identity(manifest)
    except ValueError as e:
        raise TriggerError(str(e)) from e


def _retry_delay(
    attempt: int,
    deadline: float,
    *,
    clock: Callable[[], float],
    jitter: Callable[[float, float], float],
) -> float | None:
    """Return a bounded full-jitter delay, or none when another POST is unsafe."""
    if attempt >= _PIPELINERUN_CREATE_MAX_ATTEMPTS:
        return None
    remaining = deadline - clock()
    if remaining <= 0:
        return None
    ceiling = _PIPELINERUN_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
    delay = min(ceiling, max(0.0, jitter(0.0, ceiling)))
    return delay if delay < remaining else None


def _raise_create_retry_termination(
    name: str,
    attempt: int,
    reason: str,
    failure: requests.RequestException | None,
) -> None:
    from import_orchestrator.engine.errors import TriggerError

    _LOGGER.warning("PipelineRun creation terminated name=%s reason=%s attempts=%d", name, reason, attempt)
    error = TriggerError(f"PipelineRun {name} creation {reason} after {attempt} POST attempts")
    if failure is None:
        raise error
    raise error from failure


def _retry_termination_reason(attempt: int) -> str:
    return "retry budget exhausted" if attempt >= _PIPELINERUN_CREATE_MAX_ATTEMPTS else "retry deadline exceeded"


def _resolve_create_deadline(deadline: float | None, clock: Callable[[], float]) -> float:
    return clock() + _PIPELINERUN_CREATE_DEADLINE_SECONDS if deadline is None else deadline


def _post_before_deadline(
    api: KubeAPI,
    collection_path: str,
    manifest: dict,
    name: str,
    attempt: int,
    deadline: float,
    clock: Callable[[], float],
    last_failure: requests.RequestException | None,
) -> tuple[str | None, requests.RequestException | None]:
    """Check the deadline, submit one POST, and return its name or failure."""
    if clock() >= deadline:
        _raise_create_retry_termination(name, attempt - 1, "retry deadline exceeded", last_failure)
    try:
        result = api.create(collection_path, manifest)
    except requests.RequestException as error:
        return None, error
    try:
        return result["metadata"]["name"], None
    except KeyError:
        return None, None


def _create_or_reconcile_pipelinerun(
    api: KubeAPI,
    collection_path: str,
    api_path: str,
    manifest: dict,
    name: str,
    identity: str,
    *,
    deadline: float | None = None,
    clock: Callable[[], float] = _monotonic,
    sleeper: Callable[[float], None] = _sleep,
    jitter: Callable[[float, float], float] = _jitter,
) -> str | None:
    deadline = _resolve_create_deadline(deadline, clock)
    last_failure: requests.RequestException | None = None
    for attempt in range(1, _PIPELINERUN_CREATE_MAX_ATTEMPTS + 1):
        result, failure = _post_before_deadline(
            api,
            collection_path,
            manifest,
            name,
            attempt,
            deadline,
            clock,
            last_failure,
        )
        if failure is None:
            return result
        last_failure = failure
        if not _is_retryable_create_failure(failure):
            from import_orchestrator.engine.errors import TriggerError

            raise TriggerError(f"failed to create PipelineRun: {_api_error_detail(failure)}") from failure
        if _reconcile_pipelinerun(api, api_path, name, identity, failure):
            return name
        delay = _retry_delay(attempt, deadline, clock=clock, jitter=jitter)
        if delay is None:
            _raise_create_retry_termination(name, attempt, _retry_termination_reason(attempt), failure)
        _LOGGER.warning(
            "Retrying PipelineRun creation name=%s retry=%d/%d delay=%.3fs",
            name,
            attempt,
            _PIPELINERUN_CREATE_MAX_ATTEMPTS - 1,
            delay,
        )
        sleeper(delay)
    raise AssertionError("PipelineRun creation retry loop terminated unexpectedly")


def create_pipelinerun(api: KubeAPI, namespace: str, manifest: dict) -> str | None:
    """Create or reuse an owned PipelineRun from a manifest dict."""
    name, identity = _validated_manifest_identity(manifest)
    deadline = _resolve_create_deadline(None, _monotonic)
    api_path = f"/apis/tekton.dev/v1/namespaces/{namespace}/pipelineruns/{name}"
    collection_path = f"/apis/tekton.dev/v1/namespaces/{namespace}/pipelineruns"
    if _reconcile_pipelinerun(api, api_path, name, identity):
        return name
    return _create_or_reconcile_pipelinerun(
        api,
        collection_path,
        api_path,
        manifest,
        name,
        identity,
        deadline=deadline,
    )
