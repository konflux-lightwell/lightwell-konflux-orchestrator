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

from import_orchestrator.ecosystems.java.config import (
    ARTIFACT_CONFIGS,
    JAVA_DEFAULT_DB_PATH,
    PIPELINERUN_PREFIX,
    RELEASE_PLAN,
    VERIFICATION_PUBLIC_KEY_SECRET,
    pipeline_definition_path,
)


def test_java_default_db_path():
    assert JAVA_DEFAULT_DB_PATH == "./java_import_state.db"


def test_pipelinerun_prefix():
    assert PIPELINERUN_PREFIX == "pnc-import-"


def test_release_plan():
    assert RELEASE_PLAN == "pnc-import-java-pulp-validated-prod"


def test_verification_public_key_secret():
    assert VERIFICATION_PUBLIC_KEY_SECRET == "verification-public-key"


def test_artifact_configs_has_required_keys():
    assert set(ARTIFACT_CONFIGS.keys()) == {"REBUILD", "REMEDIATED", "STAGE"}


def test_artifact_configs_entries_have_required_fields():
    required = {"app", "service_account", "source_repo", "dest_repo"}
    for artifact_type, config in ARTIFACT_CONFIGS.items():
        assert required <= set(config.keys()), f"{artifact_type} is missing fields"


def test_pipeline_definition_path_default():
    path = pipeline_definition_path()
    assert path.name == "pnc-import.yaml"
    assert "tekton" in path.parts


def test_pipeline_definition_path_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("TEKTON_PIPELINE_DIR", str(tmp_path))
    path = pipeline_definition_path()
    assert path == tmp_path / "pipelines" / "pnc-import" / "pnc-import.yaml"


def test_constants_shim_reexports():
    """constants.py must re-export Java values so existing callers keep working."""
    import import_orchestrator.constants as constants

    assert constants.PIPELINERUN_PREFIX == PIPELINERUN_PREFIX
    assert constants.RELEASE_PLAN == RELEASE_PLAN
    assert constants.VERIFICATION_PUBLIC_KEY_SECRET == VERIFICATION_PUBLIC_KEY_SECRET
    assert constants.ARTIFACT_CONFIGS == ARTIFACT_CONFIGS
