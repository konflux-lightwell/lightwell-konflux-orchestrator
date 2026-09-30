"""Tests for Fromager PURL settings rendering and embedded SBOM verification."""

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WHEEL_TASK_PATH = REPO_ROOT / "tekton/tasks/python-fromager-build-wheels/0.1/python-fromager-build-wheels.yaml"


def _load_wheel_task():
    return yaml.safe_load(WHEEL_TASK_PATH.read_text())


def test_render_step_receives_computed_version_in_env():
    task = _load_wheel_task()
    render_step = next(step for step in task["spec"]["steps"] if step["name"] == "render-build-config-settings")
    env_vars = {e["name"]: e["value"] for e in render_step["env"]}
    assert env_vars["PACKAGE"] == "$(params.PACKAGE)"
    assert env_vars["VERSION"] == "$(params.VERSION)"
    assert env_vars["COMPUTED_VERSION"] == "$(params.COMPUTED_VERSION)"


@pytest.mark.parametrize(
    "initial_purl,package,upstream_version,computed_version,expected_upstream",
    [
        (None, "nltk", "3.10.0", "3.10.0+rhlw.1", "pkg:pypi/nltk@3.10.0"),
        (
            {"repository_url": "https://example.test"},
            "Demo_Pkg",
            "1.2.3",
            "1.2.3+rhlw.2",
            "pkg:pypi/demo-pkg@1.2.3",
        ),
        (None, "foo-bar", "1.0+upstream.1", "1.0+upstream.1+rhlw.1", "pkg:pypi/foo-bar@1.0%2Bupstream.1"),
    ],
)
def test_render_script_populates_fromager_purl_settings(
    tmp_path, monkeypatch, initial_purl, package, upstream_version, computed_version, expected_upstream
):
    task = _load_wheel_task()
    render_step = next(step for step in task["spec"]["steps"] if step["name"] == "render-build-config-settings")
    script = render_step["script"].split("python3 - <<'PY'\n", 1)[1].split("\nPY", 1)[0]

    settings_dir = tmp_path / "fromager-settings"
    settings_dir.mkdir()
    settings_file = settings_dir / f"{package}.yaml"

    cfg = {"changelog": {upstream_version: ["existing note"]}}
    if initial_purl is not None:
        cfg["purl"] = dict(initial_purl)
    settings_file.write_text(yaml.safe_dump(cfg))

    monkeypatch.setenv("PACKAGE", package)
    monkeypatch.setenv("VERSION", upstream_version)
    monkeypatch.setenv("COMPUTED_VERSION", computed_version)

    adapted_script = script.replace("/var/workdir", str(tmp_path))
    result = subprocess.run([sys.executable, "-c", adapted_script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    rendered = yaml.safe_load(settings_file.read_text())
    assert rendered["purl"]["version"] == computed_version
    assert rendered["purl"]["upstream"] == expected_upstream
    if initial_purl:
        for k, v in initial_purl.items():
            assert rendered["purl"][k] == v
    assert rendered["changelog"][upstream_version][0] == "existing note"
    assert any(computed_version in note or upstream_version in note for note in rendered["changelog"][upstream_version])


def _create_test_wheel(wheel_path: Path, package: str, version: str, sbom_version: str | None = None):
    metadata = f"Metadata-Version: 2.1\nName: {package}\nVersion: {version}\n".encode()
    with zipfile.ZipFile(wheel_path, "w") as z:
        z.writestr(f"{package}-{version}.dist-info/METADATA", metadata)
        if sbom_version is not None:
            sbom_data = {
                "spdxVersion": "SPDX-2.3",
                "packages": [
                    {
                        "SPDXID": "SPDXRef-wheel",
                        "name": package,
                        "versionInfo": sbom_version,
                    }
                ],
            }
            z.writestr(f"{package}-{version}.dist-info/sboms/redhat.spdx.json", json.dumps(sbom_data))


def test_verify_step_checks_primary_wheel_embedded_sbom(tmp_path, monkeypatch):
    task = _load_wheel_task()
    verify_step = next(step for step in task["spec"]["steps"] if step["name"] == "verify-allowed-artifacts")
    script = verify_step["script"]

    configs_dir = tmp_path / "sdists-repo/build-configs/scripts"
    configs_dir.mkdir(parents=True, exist_ok=True)
    (configs_dir / "get-allowed-artifacts.py").write_text('import sys; print("[]")\n')
    (tmp_path / "sdists-repo/build-configs/packages").mkdir(parents=True, exist_ok=True)

    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    package = "nltk"
    upstream_version = "3.10.0"
    computed_version = "3.10.0+rhlw.1"

    wheel_path = artifact_dir / f"{package}-{computed_version}-py3-none-any.whl"
    _create_test_wheel(wheel_path, package, computed_version, sbom_version=computed_version)

    monkeypatch.setenv("PACKAGE", package)
    monkeypatch.setenv("VERSION", upstream_version)
    monkeypatch.setenv("COMPUTED_VERSION", computed_version)

    adapted_script = script.replace("/var/workdir", str(tmp_path))
    result = subprocess.run([sys.executable, "-c", adapted_script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "Verified primary wheel SBOM version" in result.stdout

    # Now verify that a mismatch fails
    mismatched_wheel = artifact_dir / f"{package}-{computed_version}-py3-none-any.whl"
    _create_test_wheel(mismatched_wheel, package, computed_version, sbom_version=upstream_version)

    result_mismatch = subprocess.run([sys.executable, "-c", adapted_script], capture_output=True, text=True)
    assert result_mismatch.returncode != 0
    assert "FATAL: primary wheel" in result_mismatch.stdout or "FATAL: primary wheel" in result_mismatch.stderr
