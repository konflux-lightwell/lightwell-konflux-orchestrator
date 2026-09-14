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

import pytest

from import_orchestrator.ecosystems.python_sdist import config
from import_orchestrator.engine.errors import TriggerError


class TestTargetConfigs:
    def test_mirror_target_present(self):
        assert "MIRROR" in config.TARGET_CONFIGS

    def test_scratch_target_present(self):
        assert "SCRATCH" in config.TARGET_CONFIGS

    def test_default_target_is_mirror(self):
        assert config.DEFAULT_TARGET == "MIRROR"

    def test_each_target_has_required_keys(self):
        for name, cfg in config.TARGET_CONFIGS.items():
            assert "app" in cfg, f"{name} missing 'app'"
            assert "component" in cfg, f"{name} missing 'component'"
            assert "service_account" in cfg, f"{name} missing 'service_account'"

    def test_scratch_skips_release(self):
        assert config.TARGET_CONFIGS["SCRATCH"].get("skip_release") is True

    def test_mirror_does_not_skip_release(self):
        assert not config.TARGET_CONFIGS["MIRROR"].get("skip_release")


class TestPipelineSourceIdentity:
    def test_reads_from_environment(self, monkeypatch):
        monkeypatch.setenv(
            "PIPELINE_GIT_URL",
            "https://github.com/konflux-lightwell/lightwell-konflux-orchestrator",
        )
        monkeypatch.setenv("PIPELINE_GIT_REVISION", "a" * 40)

        url, revision = config.pipeline_source_identity()

        assert url == "https://github.com/konflux-lightwell/lightwell-konflux-orchestrator"
        assert revision == "a" * 40

    def test_normalizes_environment_remote_url(self, monkeypatch):
        monkeypatch.setenv(
            "PIPELINE_GIT_URL",
            "git@github.com:konflux-lightwell/lightwell-konflux-orchestrator.git",
        )
        monkeypatch.setenv("PIPELINE_GIT_REVISION", "b" * 40)

        url, _ = config.pipeline_source_identity()

        assert url == "https://github.com/konflux-lightwell/lightwell-konflux-orchestrator"

    def test_rejects_non_sha_environment_revision(self, monkeypatch):
        monkeypatch.setenv("PIPELINE_GIT_URL", "https://github.com/x/y")
        monkeypatch.setenv("PIPELINE_GIT_REVISION", "main")

        with pytest.raises(TriggerError):
            config.pipeline_source_identity()

    def test_falls_back_to_git_when_env_partial(self, monkeypatch):
        monkeypatch.setenv("PIPELINE_GIT_URL", "https://github.com/x/y")
        monkeypatch.delenv("PIPELINE_GIT_REVISION", raising=False)
        monkeypatch.setattr(
            config,
            "_git_source_identity",
            lambda: ("https://github.com/from/git", "c" * 40),
        )

        url, revision = config.pipeline_source_identity()

        assert url == "https://github.com/from/git"
        assert revision == "c" * 40
