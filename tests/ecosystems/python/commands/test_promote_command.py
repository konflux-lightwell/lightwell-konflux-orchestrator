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

import argparse
from unittest.mock import MagicMock, patch

import pytest

from import_orchestrator.cli import make_parser
from import_orchestrator.constants import DEFAULT_POLL_INTERVAL
from import_orchestrator.ecosystems.python.commands.promote import (
    DEFAULT_TIMEOUT,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_TIMEOUT,
    promote,
)


class TestPromoteArgParsing:
    def test_snapshot_required(self):
        parser = make_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["python", "promote", "--release-plan", "remediated-build-prod"])

    def test_release_plan_required(self):
        """The plan is never inferred -- omitting it must be an error, not a default."""
        parser = make_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["python", "promote", "remediated-build-xyz12"])

    def test_snapshot_and_plan_parsed(self):
        parser = make_parser()
        args = parser.parse_args(
            ["python", "promote", "remediated-build-xyz12", "--release-plan", "remediated-build-prod"]
        )
        assert args.snapshot == "remediated-build-xyz12"
        assert args.release_plan == "remediated-build-prod"
        assert args.command == "promote"
        assert args.ecosystem.name == "python"

    def test_polling_defaults(self):
        parser = make_parser()
        args = parser.parse_args(
            ["python", "promote", "remediated-build-xyz12", "--release-plan", "remediated-build-prod"]
        )
        assert args.poll_interval == DEFAULT_POLL_INTERVAL
        assert args.timeout == DEFAULT_TIMEOUT
        assert args.output_json is None

    def test_polling_overrides(self):
        parser = make_parser()
        args = parser.parse_args(
            [
                "python",
                "promote",
                "remediated-build-xyz12",
                "--release-plan",
                "remediated-build-prod",
                "--poll-interval",
                "5",
                "--timeout",
                "60",
                "--output-json",
                "./tmp/result.json",
            ]
        )
        assert args.poll_interval == 5
        assert args.timeout == 60
        assert args.output_json == "./tmp/result.json"


def _run(kube, **overrides) -> tuple[int, dict]:
    """Drive promote() against a mocked KubeClient, returning (exit_code, payload).

    Every exit path emits a result payload, so a missing one is itself a failure.
    """
    args = argparse.Namespace(
        snapshot=overrides.get("snapshot", "remediated-build-xyz12"),
        release_plan=overrides.get("release_plan", "remediated-build-prod"),
        poll_interval=overrides.get("poll_interval", 0),
        timeout=overrides.get("timeout", DEFAULT_TIMEOUT),
        output_json=overrides.get("output_json", None),
        ecosystem=MagicMock(namespace="lightwell-python-tenant", pipelinerun_prefix="remediated-build-"),
    )

    with (
        patch("import_orchestrator.ecosystems.python.commands.promote.KubeClient", return_value=kube),
        patch("import_orchestrator.ecosystems.python.commands.promote.time.sleep"),
        patch("import_orchestrator.ecosystems.python.commands.promote._emit_result") as mock_emit,
    ):
        rc = promote(args)

    assert mock_emit.call_args is not None, "promote() returned without emitting a result payload"
    return rc, mock_emit.call_args[0][0]


class TestPromoteCommand:
    def test_creates_release_and_succeeds(self):
        kube = MagicMock()
        kube.find_release_for_snapshot_and_plan.return_value = None
        kube.create_release.return_value = "rel-prod-1"
        kube.get_release_status.return_value = "True"

        rc, payload = _run(kube)

        assert rc == EXIT_OK
        kube.create_release.assert_called_once_with(
            "remediated-build-xyz12", "remediated-build-prod", "remediated-build-"
        )
        assert payload == {
            "snapshot": "remediated-build-xyz12",
            "release_plan": "remediated-build-prod",
            "release_name": "rel-prod-1",
            "status": "True",
            "adopted": False,
        }

    def test_adopts_existing_release_without_creating_another(self):
        """Re-running after a timeout must attach to the in-flight release, not duplicate it."""
        kube = MagicMock()
        kube.find_release_for_snapshot_and_plan.return_value = "rel-live"
        kube.get_release_status.side_effect = ["Unknown", "True"]

        rc, payload = _run(kube)

        assert rc == EXIT_OK
        kube.create_release.assert_not_called()
        assert payload["release_name"] == "rel-live"
        assert payload["adopted"] is True

    def test_reports_failure_when_release_fails(self):
        kube = MagicMock()
        kube.find_release_for_snapshot_and_plan.return_value = None
        kube.create_release.return_value = "rel-bad"
        kube.get_release_status.return_value = "False"

        rc, payload = _run(kube)

        assert rc == EXIT_FAILED
        assert payload["status"] == "False"

    def test_reports_failure_when_create_fails(self):
        kube = MagicMock()
        kube.find_release_for_snapshot_and_plan.return_value = None
        kube.create_release.return_value = None

        rc, payload = _run(kube)

        assert rc == EXIT_FAILED
        assert payload["release_name"] is None
        assert payload["status"] == "CreateFailed"
        kube.get_release_status.assert_not_called()

    def test_times_out_while_still_progressing(self):
        """Timeout is distinct from failure: the release is still running, so exit 2."""
        kube = MagicMock()
        kube.find_release_for_snapshot_and_plan.return_value = None
        kube.create_release.return_value = "rel-slow"
        kube.get_release_status.return_value = "Unknown"

        rc, payload = _run(kube, timeout=0)

        assert rc == EXIT_TIMEOUT
        assert payload["status"] == "Timeout"
        assert payload["release_name"] == "rel-slow"

    def test_keeps_polling_while_status_unavailable(self):
        """A transient None from the API is not a verdict -- keep waiting."""
        kube = MagicMock()
        kube.find_release_for_snapshot_and_plan.return_value = None
        kube.create_release.return_value = "rel-flaky"
        kube.get_release_status.side_effect = [None, "Unknown", "True"]

        rc, payload = _run(kube)

        assert rc == EXIT_OK
        assert payload["status"] == "True"
        assert kube.get_release_status.call_count == 3

    def test_never_infers_the_release_plan(self):
        """Promotion must use the plan it was given, never the auto-release lookup."""
        kube = MagicMock()
        kube.find_release_for_snapshot_and_plan.return_value = None
        kube.create_release.return_value = "rel-prod-1"
        kube.get_release_status.return_value = "True"

        _run(kube)

        kube.find_release_plan_for_snapshot.assert_not_called()

    def test_lookup_is_scoped_to_the_named_plan(self):
        """The stage Release for this snapshot must not be what we look for."""
        kube = MagicMock()
        kube.find_release_for_snapshot_and_plan.return_value = None
        kube.create_release.return_value = "rel-prod-1"
        kube.get_release_status.return_value = "True"

        _run(kube)

        kube.find_release_for_snapshot_and_plan.assert_called_once_with(
            "remediated-build-xyz12", "remediated-build-prod"
        )
        kube.find_release_for_snapshot.assert_not_called()
