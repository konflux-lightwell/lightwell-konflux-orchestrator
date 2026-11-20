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

from unittest.mock import patch

import pytest

from import_orchestrator.cli import make_parser
from import_orchestrator.ecosystems.javascript.commands import orchestrate, run, trigger


@pytest.fixture(autouse=True)
def _no_target_env(monkeypatch):
    monkeypatch.delenv("LIGHTWELL_JAVASCRIPT_TARGET", raising=False)


def _parse_trigger(*argv):
    return make_parser().parse_args(["javascript", "trigger", *argv, "bluebird==3.7.2"])


class TestPublishVersionSuffixParsing:
    def test_defaults_to_none(self):
        assert _parse_trigger().publish_version_suffix is None

    def test_parsed(self):
        assert _parse_trigger("--publish-version-suffix", "lwci.0").publish_version_suffix == "lwci.0"


COMMANDS = [
    (["trigger", "bluebird==3.7.2"], trigger, "run_trigger"),
    (["run", "bluebird==3.7.2"], run, "run_single"),
    (["orchestrate"], orchestrate, "run_orchestrate"),
]


class TestPublishVersionSuffixTargetGate:
    @pytest.mark.parametrize(("argv", "module", "delegate"), COMMANDS)
    @pytest.mark.parametrize("target", ["VALIDATED", "VALIDATED_STAGE"])
    def test_rejected_outside_scratch(self, argv, module, delegate, target):
        args = make_parser().parse_args(
            ["javascript", argv[0], "--target", target, "--publish-version-suffix", "lwci.0", *argv[1:]]
        )
        with patch.object(module, delegate) as mock_delegate:
            assert args.func(args) == 2
        mock_delegate.assert_not_called()

    @pytest.mark.parametrize(("argv", "module", "delegate"), COMMANDS)
    def test_allowed_for_scratch(self, argv, module, delegate):
        args = make_parser().parse_args(
            ["javascript", argv[0], "--target", "SCRATCH", "--publish-version-suffix", "lwci.0", *argv[1:]]
        )
        with patch.object(module, delegate, return_value=0) as mock_delegate:
            assert args.func(args) == 0
        mock_delegate.assert_called_once()

    @pytest.mark.parametrize(("argv", "module", "delegate"), COMMANDS)
    def test_other_targets_unaffected_without_suffix(self, argv, module, delegate):
        args = make_parser().parse_args(["javascript", argv[0], "--target", "VALIDATED", *argv[1:]])
        with patch.object(module, delegate, return_value=0) as mock_delegate:
            assert args.func(args) == 0
        mock_delegate.assert_called_once()
