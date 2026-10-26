"""Tests for AI attribution annotations on the SBOM attached by the wheel upload-sbom step."""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WHEEL_TASK_PATH = REPO_ROOT / "tekton/tasks/python-fromager-build-wheels/0.1/python-fromager-build-wheels.yaml"
ANNOTATION_HEREDOC_START = "python3 - \"$SBOM_FILE\" <<'PY'\n"


def _upload_sbom_step() -> dict:
    """Return the upload-sbom step from the wheel build task."""
    task = yaml.safe_load(WHEEL_TASK_PATH.read_text())
    return next(step for step in task["spec"]["steps"] if step["name"] == "upload-sbom")


def _annotation_snippet(script: str) -> str:
    """Return the Python body of the AI attribution heredoc inside the upload-sbom script."""
    return script.split(ANNOTATION_HEREDOC_START, 1)[1].split("\nPY", 1)[0]


def _run_annotation(sbom_path: Path, **env: str) -> subprocess.CompletedProcess:
    """Run only the annotation snippet against sbom_path with a minimal, explicit environment.

    Args:
        sbom_path: SBOM file the snippet annotates in place.
        **env: AI_* environment values for the run. Omitted names stay unset.

    Returns:
        The completed subprocess, with captured text output.
    """
    script = _upload_sbom_step()["script"]
    run_env = {"PATH": os.environ.get("PATH", ""), **env}
    return subprocess.run(
        [sys.executable, "-c", _annotation_snippet(script), str(sbom_path)],
        capture_output=True,
        text=True,
        env=run_env,
    )


@pytest.fixture
def sbom_file(tmp_path: Path) -> Path:
    """Write a small SPDX 2.3 document that already carries one document annotation.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Path to the SBOM JSON file.
    """
    path = tmp_path / "sbom.spdx.json"
    doc = {
        "spdxVersion": "SPDX-2.3",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "nltk",
        "packages": [{"SPDXID": "SPDXRef-wheel", "name": "nltk", "versionInfo": "3.10.0+rhlw.1"}],
        "annotations": [
            {
                "annotationType": "OTHER",
                "annotator": "Tool: fromager",
                "annotationDate": "2026-01-01T00:00:00Z",
                "comment": "existing note",
            }
        ],
    }
    path.write_text(json.dumps(doc, indent=2))
    return path


def test_assisted_build_appends_annotation_and_keeps_sbom_content(sbom_file: Path):
    """An AI-assisted build appends one OTHER annotation and leaves prior annotations and content intact.

    The annotation date must be a UTC timestamp in the format the SBOM spec expects,
    taken while the test runs.
    """
    original = json.loads(sbom_file.read_text())
    before = datetime.now(timezone.utc).replace(microsecond=0)

    result = _run_annotation(sbom_file, AI_ASSISTED="true", AI_MODELS="model-alpha,model-beta")

    after = datetime.now(timezone.utc)
    assert result.returncode == 0, result.stderr
    updated = json.loads(sbom_file.read_text())
    assert updated["packages"] == original["packages"]
    assert updated["spdxVersion"] == original["spdxVersion"]
    assert updated["annotations"][0] == original["annotations"][0]
    assert len(updated["annotations"]) == 2
    added = updated["annotations"][1]
    assert added["annotationType"] == "OTHER"
    assert added["annotator"] == "Organization: Red Hat Lightwell"
    assert added["comment"] == (
        "lightwell:aiAssisted=true; lightwell:aiModels=model-alpha,model-beta; lightwell:aiSource=Red Hat Lightwell"
    )
    parsed = datetime.strptime(added["annotationDate"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert before <= parsed <= after


def test_assisted_build_with_unknown_models_records_empty_model_list(sbom_file: Path):
    """An assisted build with no model IDs still writes lightwell:aiModels= with an empty value.

    This covers the "assisted, model unknown" case, so consumers can tell that attribution
    ran but named no model. Characterization: no spec requires this exact form.
    """
    result = _run_annotation(sbom_file, AI_ASSISTED="true", AI_MODELS="")

    assert result.returncode == 0, result.stderr
    comment = json.loads(sbom_file.read_text())["annotations"][1]["comment"]
    assert comment == "lightwell:aiAssisted=true; lightwell:aiModels=; lightwell:aiSource=Red Hat Lightwell"


@pytest.mark.parametrize(
    "env",
    [
        pytest.param({"AI_ASSISTED": "false", "AI_MODELS": "model-alpha"}, id="flag-false"),
        pytest.param({"AI_MODELS": "model-alpha"}, id="flag-absent"),
    ],
)
def test_not_assisted_build_leaves_sbom_byte_identical(sbom_file: Path, env: dict[str, str]):
    """A build that is not AI-assisted never changes the attached SBOM file."""
    original = sbom_file.read_bytes()

    result = _run_annotation(sbom_file, **env)

    assert result.returncode == 0, result.stderr
    assert sbom_file.read_bytes() == original


def test_upload_sbom_declares_ai_params_and_passes_them_to_the_step():
    """The task declares AI_ASSISTED and AI_MODELS with safe defaults and no AI_SOURCE.

    The upload-sbom step must map both params from $(params.*) and must not receive AI_SOURCE.
    """
    task = yaml.safe_load(WHEEL_TASK_PATH.read_text())
    params = {p["name"]: p for p in task["spec"]["params"]}
    env_vars = {e["name"]: e["value"] for e in _upload_sbom_step()["env"]}

    assert params["AI_ASSISTED"]["default"] == "false"
    assert params["AI_MODELS"]["default"] == ""
    assert "AI_SOURCE" not in params
    assert env_vars["AI_ASSISTED"] == "$(params.AI_ASSISTED)"
    assert env_vars["AI_MODELS"] == "$(params.AI_MODELS)"
    assert "AI_SOURCE" not in env_vars


def test_ai_annotation_runs_before_sbom_is_attached_and_hashed():
    """The annotation is written before cosign attaches the SBOM and before the digest line hashes it."""
    script = _upload_sbom_step()["script"]

    annotation_at = script.index(ANNOTATION_HEREDOC_START)
    attach_at = script.index("cosign attach sbom")
    digest_at = script.index('sha256sum "$SBOM_FILE"')
    assert annotation_at < attach_at
    assert annotation_at < digest_at
