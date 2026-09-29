import base64
import csv
import hashlib
import json
import zipfile
from io import StringIO
from pathlib import Path

import pytest
import yaml

from hack.sync_wheel_sbom import SyncWheelSbomError, sync_wheel_sbom, update_redhat_spdx_sbom

REPO_ROOT = Path(__file__).resolve().parents[1]
TASK_PATH = REPO_ROOT / "tekton/tasks/python-fromager-build-wheels/0.1/python-fromager-build-wheels.yaml"
HELPER_PATH = REPO_ROOT / "hack/sync_wheel_sbom.py"
PRUNE_HELPER_PATH = REPO_ROOT / "hack/prune_wheel_sboms.py"


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
    metadata = b"Metadata-Version: 2.1\nName: demo-pkg\nVersion: 1.0+rhlw.1\n"
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


def test_task_embeds_checked_in_helper_and_invokes_it():
    task = yaml.safe_load(TASK_PATH.read_text())
    clone_script = next(step["script"] for step in task["spec"]["steps"] if step["name"] == "clone-build-configs")
    verify_script = next(step["script"] for step in task["spec"]["steps"] if step["name"] == "verify-allowed-artifacts")
    lines = clone_script.splitlines()
    start = lines.index("cat > \"$DEST/scripts/sync_wheel_sbom.py\" <<'PY'") + 1
    end = lines.index("PY", start)

    assert "\n".join(lines[start:end]) + "\n" == HELPER_PATH.read_text()
    assert 'mkdir -p "$DEST/scripts"' in clone_script
    expected_invocation = (
        "subprocess.run([sys.executable, str(sync_script), str(primary_wheel), computed_version], check=True)"
    )
    assert expected_invocation in verify_script


def test_task_embeds_optional_sbom_allowlist_and_runs_before_oci_packaging():
    task = yaml.safe_load(TASK_PATH.read_text())
    clone_script = next(step["script"] for step in task["spec"]["steps"] if step["name"] == "clone-build-configs")
    verify_script = next(step["script"] for step in task["spec"]["steps"] if step["name"] == "verify-allowed-artifacts")
    lines = clone_script.splitlines()
    start = lines.index("cat > \"$DEST/scripts/prune_wheel_sboms.py\" <<'PY'") + 1
    end = lines.index("PY", start)
    embedded = "\n".join(lines[start:end]) + "\n"
    assert embedded == PRUNE_HELPER_PATH.read_text()
    assert "subprocess.run([sys.executable, str(prune_script), str(primary_wheel)], check=True)" in verify_script
    task_names = [step["name"] for step in task["spec"]["steps"]]
    assert task_names.index("verify-allowed-artifacts") < task_names.index("create-oci-artifact")


def test_rewrites_wheel_version_and_pypi_purl_with_encoded_plus(tmp_path):
    wheel = tmp_path / "demo_pkg-1.0+rhlw.1-py3-none-any.whl"
    sbom_member, _, _, _ = _make_wheel(wheel)

    sync_wheel_sbom(wheel, "1.0+rhlw.1")

    packages = {pkg["SPDXID"]: pkg for pkg in _read_json_member(wheel, sbom_member)["packages"]}
    wheel_pkg = packages["SPDXRef-wheel"]
    assert wheel_pkg["versionInfo"] == "1.0+rhlw.1"
    assert "pkg:pypi/demo-pkg@1.0%2Brhlw.1?" in _purl(wheel_pkg)
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
    wheel = tmp_path / "demo_pkg-1.0+rhlw.1-py3-none-any.whl"
    sbom_member, _, _, _ = _make_wheel(wheel)
    before = _read_json_member(wheel, sbom_member)["packages"][0]

    sync_wheel_sbom(wheel, "1.0+rhlw.1")

    upstream = {pkg["SPDXID"]: pkg for pkg in _read_json_member(wheel, sbom_member)["packages"]}["SPDXRef-upstream"]
    assert upstream == before


def test_rewrites_wheel_file_name_and_drops_stale_download_url(tmp_path):
    wheel = tmp_path / "demo_pkg-1.0+rhlw.1-py3-none-any.whl"
    sbom_member, _, _, _ = _make_wheel(wheel)

    sync_wheel_sbom(wheel, "1.0+rhlw.1")

    wheel_pkg = {pkg["SPDXID"]: pkg for pkg in _read_json_member(wheel, sbom_member)["packages"]}["SPDXRef-wheel"]
    purl = _purl(wheel_pkg)
    assert "?file_name=demo_pkg-1.0%2Brhlw.1-py3-none-any.whl" in purl
    assert "download_url=" not in purl
    assert "pkg:pypi/demo-pkg@1.0%2Brhlw.1" in purl


def test_updates_only_sbom_record_digest_and_size_for_changed_sbom(tmp_path):
    wheel = tmp_path / "demo_pkg-1.0+rhlw.1-py3-none-any.whl"
    sbom_member, record_member, code_member, metadata_member = _make_wheel(wheel)

    with zipfile.ZipFile(wheel) as whl:
        code_before = whl.read(code_member)
        metadata_before = whl.read(metadata_member)
        code_compression = whl.getinfo(code_member).compress_type
    record_before = _read_record(wheel, record_member)

    sync_wheel_sbom(wheel, "1.0+rhlw.1")

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
        update_redhat_spdx_sbom(json.dumps(sbom).encode(), "1.0+rhlw.1", "demo_pkg-1.0+rhlw.1-py3-none-any.whl")


def test_errors_when_upstream_package_missing():
    sbom = _sbom()
    sbom["packages"] = [pkg for pkg in sbom["packages"] if pkg["SPDXID"] != "SPDXRef-upstream"]

    with pytest.raises(SyncWheelSbomError, match="SPDXRef-upstream"):
        update_redhat_spdx_sbom(json.dumps(sbom).encode(), "1.0+rhlw.1", "demo_pkg-1.0+rhlw.1-py3-none-any.whl")


def test_errors_when_record_signature_present(tmp_path):
    wheel = tmp_path / "demo_pkg-1.0+rhlw.1-py3-none-any.whl"
    _make_wheel(wheel)
    with zipfile.ZipFile(wheel, "a") as whl:
        whl.writestr("demo_pkg-1.0.dist-info/RECORD.jws", b"signature")

    with pytest.raises(SyncWheelSbomError, match="signed wheel"):
        sync_wheel_sbom(wheel, "1.0+rhlw.1")


def test_errors_when_record_missing_sbom_entry(tmp_path):
    wheel = tmp_path / "demo_pkg-1.0+rhlw.1-py3-none-any.whl"
    _make_wheel(wheel, include_record_entry=False)

    with pytest.raises(SyncWheelSbomError, match="RECORD has no entry"):
        sync_wheel_sbom(wheel, "1.0+rhlw.1")
