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

import pytest

from import_orchestrator.ecosystems.base import Ecosystem, ai_attribution_params


class _Dummy:
    name = "dummy"
    default_db_path = "./dummy.db"
    pipelinerun_prefix = "dummy-"
    namespace = "dummy-tenant"

    def build_pipelinerun(self, ref, args):
        return {"ref": ref}

    def request_identity(self, ref, args):
        return {
            "namespace": self.namespace,
            "application": "dummy-app",
            "component": "dummy-app",
            "pipeline": "dummy",
            "release_plan": "",
            "target": "stage",
            "artifact": ref,
        }

    def register_cli(self, subparsers):
        return None


def test_dummy_satisfies_ecosystem_protocol():
    assert isinstance(_Dummy(), Ecosystem)


class TestAiAttributionParams:
    """Ecosystem-independent AI attribution params for SLSA provenance."""

    @pytest.fixture(autouse=True)
    def _clear_ai_env(self, monkeypatch):
        """Start every test with AI_* env unset so the developer's shell cannot leak in."""
        monkeypatch.delenv("AI_ASSISTED", raising=False)
        monkeypatch.delenv("AI_MODELS", raising=False)

    @pytest.mark.parametrize(
        ("env", "expected"),
        [
            pytest.param({}, ("false", ""), id="env-absent"),
            pytest.param(
                {"AI_ASSISTED": "true", "AI_MODELS": " model-alpha , ,model-beta "},
                ("true", "model-alpha,model-beta"),
                id="assisted-with-models",
            ),
            pytest.param({"AI_ASSISTED": "TRUE"}, ("true", ""), id="assisted-model-unknown"),
            pytest.param(
                {"AI_ASSISTED": "yes", "AI_MODELS": "model-alpha"}, ("false", "model-alpha"), id="non-true-value"
            ),
        ],
    )
    def test_ai_params_follow_env(self, monkeypatch, env, expected):
        """AI_ASSISTED and AI_MODELS env vars become the AI_ASSISTED and AI_MODELS params."""
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        params = {p["name"]: p["value"] for p in ai_attribution_params()}
        assert (params["AI_ASSISTED"], params["AI_MODELS"]) == expected
