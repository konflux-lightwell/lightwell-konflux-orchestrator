# The sync helper tests below remain standalone unit coverage; the Task no longer uses it.
import base64
import csv
import hashlib
import json
import subprocess
import sys
import zipfile
from io import StringIO
from pathlib import Path

import pytest
import yaml

from hack.sync_wheel_sbom import SyncWheelSbomError, sync_wheel_sbom, update_redhat_spdx_sbom

REPO_ROOT = Path(__file__).resolve().parents[1]
TASK_PATH = REPO_ROOT / "tekton/tasks/python-fromager-build-wheels/0.1/python-fromager-build-wheels.yaml"


def _digest(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")


def _record(rows: list[list[str]]) -> bytes:
    out = StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerows(rows)
    return out.getvalue().encode()


def _sbom(upstream_version="1.0", wheel_version="1.0") -> dict:
    return {
        "spdxVersion": "SPDX-2.3",
        "packages": [
            {
                "SPDXID": "SPDXRef-upstream",
                "name": "demo-pkg",
                "versionInfo": upstream_version,
                "packageVerificationCode": {"packageVerificationCodeValue": "upstream-code"},
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": (
                            "pkg:pypi/demo-pkg@1.0?file_name=demo_pkg-1.0.tar.gz"
                            "&download_url=https%3A%2F%2Fexample.test%2Fsrc.tar.gz"
                        ),
                    }
                ],
            },
            {
                "SPDXID": "SPDXRef-wheel",
                "name": "demo-pkg",
                "versionInfo": wheel_version,
                "packageVerificationCode": {"packageVerificationCodeValue": "wheel-code"},
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": (
                            "pkg:pypi/demo-pkg@1.0?file_name=demo_pkg-1.0-py3-none-any.whl"
                            "&download_url=https%3A%2F%2Fexample.test%2Fwheel.whl"
                        ),
                    }
                ],
            },
        ],
    }


def _make_wheel(path: Path, sbom: dict | None = None, include_record_entry: bool = True):
    sbom_member = "demo_pkg-1.0.dist-info/sboms/redhat.spdx.json"
    record_member = "demo_pkg-1.0.dist-info/RECORD"
    metadata_member = "demo_pkg-1.0.dist-info/METADATA"
    code_member = "demo_pkg/__init__.py"
    sbom_bytes = json.dumps(sbom or _sbom()).encode()
    metadata = b"Metadata-Version: 2.1\nName: demo-pkg\nVersion: 1.0+vendor.1\n"
    code = b"VALUE = 'unchanged'\n"
    rows = [
        [code_member, _digest(code), str(len(code))],
        [metadata_member, _digest(metadata), str(len(metadata))],
        [record_member, "", ""],
    ]
    if include_record_entry:
        rows.insert(2, [sbom_member, _digest(sbom_bytes), str(len(sbom_bytes))])
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as whl:
        whl.writestr(code_member, code)
        whl.writestr(metadata_member, metadata)
        whl.writestr(sbom_member, sbom_bytes)
        whl.writestr(record_member, _record(rows))
    return sbom_member, record_member, code_member, metadata_member


def _read_json_member(wheel: Path, member: str) -> dict:
    with zipfile.ZipFile(wheel) as whl:
        return json.loads(whl.read(member))


def _read_record(wheel: Path, member: str) -> dict[str, list[str]]:
    with zipfile.ZipFile(wheel) as whl:
        return {row[0]: row for row in csv.reader(StringIO(whl.read(member).decode()))}


def _purl(package: dict) -> str:
    return package["externalRefs"][0]["referenceLocator"]


def test_task_uses_ordinary_parent_builder_image():
    task = yaml.safe_load(TASK_PATH.read_text())
    builder_step = next(step for step in task["spec"]["steps"] if step["name"] == "build-wheels")
    verification_step = next(step for step in task["spec"]["steps"] if step["name"] == "verify-allowed-artifacts")
    image_name = (
        "quay.io/redhat-user-workloads/calunga-tenant/plumbing-builder@sha256:"
        "d8355de03d57adef033743da1f1a3bb2f0ff038192e3220b155e9dd7dba5d37c"
    )
    assert builder_step["image"] == image_name
    assert {e["name"] for e in builder_step["env"]} >= {
        "PACKAGE",
        "VERSION",
        "COMPUTED_VERSION",
    }
    assert "VERSION_OVERRIDE" not in {e["name"] for e in builder_step["env"]}
    assert "expected only the curated Red Hat SPDX SBOM" in verification_step["script"]
    assert "remediated wheel PURL" in verification_step["script"]


def test_rewrites_wheel_version_and_pypi_purl_with_encoded_plus(tmp_path):
    wheel = tmp_path / "demo_pkg-1.0+vendor.1-py3-none-any.whl"
    sbom_member, _, _, _ = _make_wheel(wheel)

    sync_wheel_sbom(wheel, "1.0+vendor.1")

    packages = {pkg["SPDXID"]: pkg for pkg in _read_json_member(wheel, sbom_member)["packages"]}
    wheel_pkg = packages["SPDXRef-wheel"]
    assert wheel_pkg["versionInfo"] == "1.0+vendor.1"
    assert "pkg:pypi/demo-pkg@1.0%2Bvendor.1?" in _purl(wheel_pkg)
    assert wheel_pkg["packageVerificationCode"] == {"packageVerificationCodeValue": "wheel-code"}


def test_percent_encodes_root_package_purl_version_without_reencoding_name_or_qualifiers():
    sbom = _sbom()
    wheel_pkg = next(pkg for pkg in sbom["packages"] if pkg["SPDXID"] == "SPDXRef-wheel")
    wheel_pkg["externalRefs"][0]["referenceLocator"] = "pkg:pypi/demo%2Fpkg@1.0?download_url=https%3A%2F%2Fx"

    updated = update_redhat_spdx_sbom(
        json.dumps(sbom).encode(), "1!2.0+local/dev", "demo_pkg-1!2.0+local/dev-py3-none-any.whl"
    )

    packages = {pkg["SPDXID"]: pkg for pkg in json.loads(updated)["packages"]}
    assert _purl(packages["SPDXRef-wheel"]) == (
        "pkg:pypi/demo%2Fpkg@1%212.0%2Blocal%2Fdev?file_name=demo_pkg-1%212.0%2Blocal%2Fdev-py3-none-any.whl"
    )


def test_preserves_upstream_version_and_purl(tmp_path):
    wheel = tmp_path / "demo_pkg-1.0+vendor.1-py3-none-any.whl"
    sbom_member, _, _, _ = _make_wheel(wheel)
    before = _read_json_member(wheel, sbom_member)["packages"][0]

    sync_wheel_sbom(wheel, "1.0+vendor.1")

    upstream = {pkg["SPDXID"]: pkg for pkg in _read_json_member(wheel, sbom_member)["packages"]}["SPDXRef-upstream"]
    assert upstream == before


def test_rewrites_wheel_file_name_and_drops_stale_download_url(tmp_path):
    wheel = tmp_path / "demo_pkg-1.0+vendor.1-py3-none-any.whl"
    sbom_member, _, _, _ = _make_wheel(wheel)

    sync_wheel_sbom(wheel, "1.0+vendor.1")

    wheel_pkg = {pkg["SPDXID"]: pkg for pkg in _read_json_member(wheel, sbom_member)["packages"]}["SPDXRef-wheel"]
    purl = _purl(wheel_pkg)
    assert "?file_name=demo_pkg-1.0%2Bvendor.1-py3-none-any.whl" in purl
    assert "download_url=" not in purl
    assert "pkg:pypi/demo-pkg@1.0%2Bvendor.1" in purl


def test_updates_only_sbom_record_digest_and_size_for_changed_sbom(tmp_path):
    wheel = tmp_path / "demo_pkg-1.0+vendor.1-py3-none-any.whl"
    sbom_member, record_member, code_member, metadata_member = _make_wheel(wheel)

    with zipfile.ZipFile(wheel) as whl:
        code_before = whl.read(code_member)
        metadata_before = whl.read(metadata_member)
        code_compression = whl.getinfo(code_member).compress_type
    record_before = _read_record(wheel, record_member)

    sync_wheel_sbom(wheel, "1.0+vendor.1")

    with zipfile.ZipFile(wheel) as whl:
        sbom_bytes = whl.read(sbom_member)
        assert whl.read(code_member) == code_before
        assert whl.read(metadata_member) == metadata_before
        assert whl.getinfo(code_member).compress_type == code_compression
    record_after = _read_record(wheel, record_member)
    assert record_after[sbom_member][1:] == [_digest(sbom_bytes), str(len(sbom_bytes))]
    assert record_after[record_member] == [record_member, "", ""]
    assert record_after[code_member] == record_before[code_member]
    assert record_after[metadata_member] == record_before[metadata_member]


def test_errors_when_wheel_package_missing():
    sbom = _sbom()
    sbom["packages"] = [pkg for pkg in sbom["packages"] if pkg["SPDXID"] != "SPDXRef-wheel"]

    with pytest.raises(SyncWheelSbomError, match="SPDXRef-wheel"):
        update_redhat_spdx_sbom(json.dumps(sbom).encode(), "1.0+vendor.1", "demo_pkg-1.0+vendor.1-py3-none-any.whl")


def test_errors_when_upstream_package_missing():
    sbom = _sbom()
    sbom["packages"] = [pkg for pkg in sbom["packages"] if pkg["SPDXID"] != "SPDXRef-upstream"]

    with pytest.raises(SyncWheelSbomError, match="SPDXRef-upstream"):
        update_redhat_spdx_sbom(json.dumps(sbom).encode(), "1.0+vendor.1", "demo_pkg-1.0+vendor.1-py3-none-any.whl")


def test_errors_when_record_signature_present(tmp_path):
    wheel = tmp_path / "demo_pkg-1.0+vendor.1-py3-none-any.whl"
    _make_wheel(wheel)
    with zipfile.ZipFile(wheel, "a") as whl:
        whl.writestr("demo_pkg-1.0.dist-info/RECORD.jws", b"signature")

    with pytest.raises(SyncWheelSbomError, match="signed wheel"):
        sync_wheel_sbom(wheel, "1.0+vendor.1")


def test_errors_when_record_missing_sbom_entry(tmp_path):
    wheel = tmp_path / "demo_pkg-1.0+vendor.1-py3-none-any.whl"
    _make_wheel(wheel, include_record_entry=False)

    with pytest.raises(SyncWheelSbomError, match="RECORD has no entry"):
        sync_wheel_sbom(wheel, "1.0+vendor.1")


def test_task_uses_settings_instead_of_post_build_sync():
    steps = yaml.safe_load(TASK_PATH.read_text())["spec"]["steps"]
    names = [step["name"] for step in steps]
    assert "sync-wheel-sbom" not in names
    assert names.index("collect-build-files") + 1 == names.index("prune-wheel-sboms")
    assert names.index("prune-wheel-sboms") + 1 == names.index("verify-allowed-artifacts")
    assert names.index("verify-allowed-artifacts") < names.index("create-oci-artifact")
    assert "sync_wheel_sbom" not in TASK_PATH.read_text()
    builder = next(step for step in steps if step["name"] == "build-wheels")
    assert "PRUNE_WHEEL_SBOMS" not in {e["name"] for e in builder["env"]}


@pytest.mark.parametrize(
    "purl",
    [
        None,
        {},
        {
            "repository_url": "https://example.test",
            "type": "pypi",
            "name": "custom-name",
            "version": "old",
            "upstream": "old",
        },
    ],
)
@pytest.mark.parametrize("upstream_version", ["1.0", "1.0+source.1"])
def test_runtime_purl_settings_overlay(tmp_path, monkeypatch, purl, upstream_version):
    # Execute the exact renderer transform, not a duplicate implementation.
    step = next(
        step
        for step in yaml.safe_load(TASK_PATH.read_text())["spec"]["steps"]
        if step["name"] == "render-build-config-settings"
    )
    assert {e["name"]: e["value"] for e in step["env"]}["COMPUTED_VERSION"] == "$(params.COMPUTED_VERSION)"
    script = step["script"].split("python3 - <<'PY'\n", 1)[1].split("\nPY", 1)[0]
    settings_dir = tmp_path / "fromager-settings"
    settings_dir.mkdir()
    settings = settings_dir / "Demo_Pkg.yaml"
    cfg = {"build_options": {"build_ext_parallel": True}, "changelog": {upstream_version: ["existing"]}}
    if purl is not None:
        cfg["purl"] = purl
    settings.write_text(yaml.safe_dump(cfg))
    monkeypatch.setenv("PACKAGE", "Demo_Pkg")
    monkeypatch.setenv("VERSION", upstream_version)
    monkeypatch.setenv("COMPUTED_VERSION", "1.0+vendor.1")
    subprocess.run([sys.executable, "-c", script.replace("/var/workdir", str(tmp_path))], check=True)
    actual = yaml.safe_load(settings.read_text())
    assert actual["purl"]["version"] == "1.0+vendor.1"
    upstream_purl = actual["purl"]["upstream"]
    expected_version = upstream_version.replace("+", "%2B")
    assert upstream_purl == f"pkg:pypi/demo-pkg@{expected_version}"
    for key, value in (purl or {}).items():
        if key not in {"version", "upstream"}:
            assert actual["purl"][key] == value
    assert actual["build_options"] == cfg["build_options"]
    assert actual["changelog"][upstream_version][0] == "existing"


def _run_verification(tmp_path, monkeypatch):
    steps = yaml.safe_load(TASK_PATH.read_text())["spec"]["steps"]
    script = next(step["script"] for step in steps if step["name"] == "verify-allowed-artifacts")
    assert "sync_wheel_sbom(" not in script
    assert "prune_wheel_sboms" not in script
    assert ".unlink(" not in script
    configs = tmp_path / "sdists-repo/build-configs/scripts"
    configs.mkdir(parents=True, exist_ok=True)
    (configs / "get-allowed-artifacts.py").write_text("print('[\"demo-pkg\"]')\n")
    monkeypatch.setenv("PACKAGE", "demo-pkg")
    monkeypatch.setenv("VERSION", "1.0")
    monkeypatch.setenv("COMPUTED_VERSION", "1.0+vendor.1")
    return subprocess.run(
        [sys.executable, "-c", script.replace("/var/workdir", str(tmp_path))],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "corruption",
    [
        None,
        "digest",
        "size",
        "extra-sbom",
        "version",
        "purl-version",
        "file_name",
        "download_url",
        "upstream-version",
        "upstream-purl-version",
        "upstream-purl-name",
        "upstream-purl-type",
    ],
)
def test_task_verification_is_read_only_and_checks_identity_record_and_allowlist(tmp_path, monkeypatch, corruption):
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    primary = artifact / "demo_pkg-1.0+vendor.1-py3-none-any.whl"
    sbom_member, record_member, _, _ = _make_wheel(primary)
    sync_wheel_sbom(primary, "1.0+vendor.1")
    with zipfile.ZipFile(primary) as wheel:
        members = {name: wheel.read(name) for name in wheel.namelist()}
    if corruption in {"digest", "size"}:
        rows = list(csv.reader(StringIO(members[record_member].decode())))
        for row in rows:
            if row[0] == sbom_member:
                row[1 if corruption == "digest" else 2] = "wrong"
        output = StringIO()
        csv.writer(output).writerows(rows)
        members[record_member] = output.getvalue().encode()
    elif corruption == "extra-sbom":
        members[sbom_member.replace("redhat.spdx.json", "other.json")] = b"{}"
    elif corruption:
        sbom = json.loads(members[sbom_member])
        spdx_id = "SPDXRef-upstream" if corruption.startswith("upstream-") else "SPDXRef-wheel"
        package = next(p for p in sbom["packages"] if p["SPDXID"] == spdx_id)
        if corruption in {"version", "upstream-version"}:
            package["versionInfo"] = "wrong"
        else:
            ref = package["externalRefs"][0]
            if corruption == "upstream-purl-version":
                ref["referenceLocator"] = ref["referenceLocator"].replace("@1.0?", "@2.0?")
            elif corruption == "upstream-purl-name":
                ref["referenceLocator"] = ref["referenceLocator"].replace("demo-pkg@", "other-pkg@")
            elif corruption == "upstream-purl-type":
                ref["referenceLocator"] = ref["referenceLocator"].replace("pkg:pypi/", "pkg:npm/")
            elif corruption == "purl-version":
                ref["referenceLocator"] = ref["referenceLocator"].replace("1.0%2Bvendor.1?", "1.0?")
            elif corruption == "file_name":
                ref["referenceLocator"] = ref["referenceLocator"].replace("file_name=", "wrong_name=")
            else:
                ref["referenceLocator"] += "&download_url=https%3A%2F%2Fstale.example"
        members[sbom_member] = json.dumps(sbom).encode()
    with zipfile.ZipFile(primary, "w") as wheel:
        for name, data in members.items():
            wheel.writestr(name, data)
    before = primary.read_bytes()
    result = _run_verification(tmp_path, monkeypatch)
    assert primary.read_bytes() == before
    assert result.returncode == (0 if corruption is None else 1), result.stderr
