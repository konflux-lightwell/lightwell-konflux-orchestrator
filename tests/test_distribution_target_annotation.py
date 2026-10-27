"""Tests for the dev.lightwell.distribution-target annotation computed by the
python-fromager-build-wheels create-oci-artifact step.

Backport and Novel builds must both resolve to the umbrella "remediated"
distribution target expected by registry-lightwell-python-remediated-stage's
lightwell_allowed_distribution_targets policy; only Validated builds use
"validated". See LWLP-3930.
"""

import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WHEEL_TASK = ROOT / "tekton/tasks/python-fromager-build-wheels/0.1/python-fromager-build-wheels.yaml"


def _create_oci_artifact_header():
    task = yaml.safe_load(WHEEL_TASK.read_text())
    steps = {step["name"]: step for step in task["spec"]["steps"]}
    script = steps["create-oci-artifact"]["script"]
    # Only the DISTRIBUTION_TARGET computation runs here; stop before any real
    # registry/network commands (select-oci-auth) execute.
    marker = "select-oci-auth"
    assert marker in script
    return script[: script.index(marker)]


@pytest.mark.parametrize(
    "build_type,expected",
    [
        ("Backport", "remediated"),
        ("Novel", "remediated"),
        ("Validated", "validated"),
    ],
)
def test_distribution_target_collapses_backport_and_novel_to_remediated(build_type, expected):
    header = _create_oci_artifact_header()
    result = subprocess.run(
        ["bash", "-c", header + '\necho "$DISTRIBUTION_TARGET"'],
        env={"BUILD_TYPE": build_type, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_distribution_target_rejects_unknown_build_type():
    header = _create_oci_artifact_header()
    result = subprocess.run(
        ["bash", "-c", header],
        env={"BUILD_TYPE": "Remediated", "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "BUILD_TYPE must be Backport, Novel, or Validated" in result.stderr
