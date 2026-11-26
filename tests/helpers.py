"""Shared helpers for tests."""

from __future__ import annotations

from typing import TypeVar

from import_orchestrator.models import Attempt, AttemptPhase, Request

T = TypeVar("T")


def require(value: T | None) -> T:
    """Return ``value``, failing the test if it is None."""
    assert value is not None, "expected a value, got None"
    return value


def require_id(row: Request | Attempt | AttemptPhase) -> int:
    """Return the id of a persisted row, failing the test if it was never saved."""
    assert row.id is not None, f"{type(row).__name__} has not been persisted"
    return row.id
