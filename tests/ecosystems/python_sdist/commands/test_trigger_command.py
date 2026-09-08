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

from unittest.mock import MagicMock, patch

import pytest

from import_orchestrator.cli import make_parser
from import_orchestrator.ecosystems.python_sdist.commands.trigger import run


class TestTriggerArgParsing:
    def test_ref_positional_stored(self):
        parser = make_parser()
        args = parser.parse_args(["python-sdist", "trigger", "foolib==0.4.0"])
        assert args.ref == "foolib==0.4.0"

    def test_missing_ref_fails(self):
        parser = make_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["python-sdist", "trigger"])

    def test_ecosystem_attached(self):
        parser = make_parser()
        args = parser.parse_args(["python-sdist", "trigger", "foolib==0.4.0"])
        assert args.ecosystem.name == "python-sdist"

    def test_source_registries_default(self):
        parser = make_parser()
        args = parser.parse_args(["python-sdist", "trigger", "foolib==0.4.0"])
        assert args.source_registries == "rhtl,pypi.org"

    def test_source_registries_override(self):
        parser = make_parser()
        args = parser.parse_args(["python-sdist", "trigger", "--source-registries", "pypi.org", "foolib==0.4.0"])
        assert args.source_registries == "pypi.org"


def _args(**overrides):
    base = dict(ref="foolib==0.4.0", ecosystem=MagicMock())
    base.update(overrides)
    mock = MagicMock()
    for k, v in base.items():
        setattr(mock, k, v)
    return mock


class TestTriggerRun:
    def test_delegates_to_run_trigger(self):
        with patch("import_orchestrator.ecosystems.python_sdist.commands.trigger.run_trigger") as mock_run:
            mock_run.return_value = 0
            args = _args(ref="foolib==0.4.0")
            ret = run(args)
            assert ret == 0
            mock_run.assert_called_once_with(args, "foolib==0.4.0")
