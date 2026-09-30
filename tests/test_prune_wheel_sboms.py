import base64
import csv
import hashlib
import json
import zipfile
from io import StringIO
from pathlib import Path

import pytest

from hack.prune_wheel_sboms import PruneWheelSbomError, prune_wheel_sboms


def _digest(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")


def _csv(rows: list[list[str]]) -> bytes:
    output = StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(rows)
    return output.getvalue().encode()


def _wheel(path: Path, *, extra_sboms: tuple[str, ...] = (), signed: bool = False):
    prefix = "demo_pkg-1.0.dist-info"
    allowed = f"{prefix}/sboms/redhat.spdx.json"
    other = [f"{prefix}/sboms/{name}" for name in extra_sboms]
    module = "demo_pkg/__init__.py"
    members = {allowed: json.dumps({"spdxVersion": "SPDX-2.3"}).encode(), module: b"VALUE = 1\n"}
    members[f"{prefix}/METADATA"] = b"Name: demo-pkg\nVersion: 1.0\n"
    members.update({name: b'{"upstream": true}' for name in other})
    if signed:
        members[f"{prefix}/RECORD.jws"] = b"signature"
    record = f"{prefix}/RECORD"
    rows = [[name, _digest(data), str(len(data))] for name, data in members.items()]
    rows.append([record, "", ""])
    members[record] = _csv(rows)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return allowed, other, record, module


def _record(path: Path, name: str) -> dict[str, list[str]]:
    with zipfile.ZipFile(path) as archive:
        rows = csv.reader(StringIO(archive.read(name).decode()))
        return {row[0]: row for row in rows}


def test_prunes_other_embedded_sboms_and_preserves_redhat_sbom_record(tmp_path):
    wheel = tmp_path / "demo_pkg-1.0-py3-none-any.whl"
    allowed, other, record, module = _wheel(
        wheel,
        extra_sboms=("virtualenv.cdx.json", "vendor.spdx.json", "manifest.json"),
    )
    with zipfile.ZipFile(wheel) as archive:
        allowed_before = archive.read(allowed)
        module_before = archive.read(module)
        record_before = _record(wheel, record)

    removed = prune_wheel_sboms(wheel)

    assert removed == sorted(other)
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert allowed in names
        assert not set(other) & set(names)
        assert archive.read(allowed) == allowed_before
        assert archive.read(module) == module_before
        updated_record_bytes = archive.read(record)
    record_after = _record(wheel, record)
    assert record_after[allowed] == record_before[allowed]
    assert record_after[module] == record_before[module]
    assert all(name not in record_after for name in other)
    assert record_after[record] == [record, "", ""]
    assert updated_record_bytes == _csv([row for row in record_before.values() if row[0] not in other])


def test_noop_when_wheel_contains_only_redhat_sbom(tmp_path):
    wheel = tmp_path / "demo_pkg-1.0-py3-none-any.whl"
    allowed, _, record, _ = _wheel(wheel)
    before = wheel.read_bytes()

    assert prune_wheel_sboms(wheel) == []
    assert wheel.read_bytes() == before
    with zipfile.ZipFile(wheel) as archive:
        assert allowed in archive.namelist()
        assert record in archive.namelist()


def test_signed_wheel_fails_closed_without_rewriting(tmp_path):
    wheel = tmp_path / "demo_pkg-1.0-py3-none-any.whl"
    _wheel(wheel, extra_sboms=("vendor.cdx.json",), signed=True)
    before = wheel.read_bytes()

    with pytest.raises(PruneWheelSbomError, match="signed wheel"):
        prune_wheel_sboms(wheel)
    assert wheel.read_bytes() == before


def test_missing_record_entry_for_extra_sbom_fails_closed(tmp_path):
    wheel = tmp_path / "demo_pkg-1.0-py3-none-any.whl"
    _allowed, other, record, _module = _wheel(wheel, extra_sboms=("vendor.cdx.json",))
    rewritten = tmp_path / "rewrite.whl"
    with zipfile.ZipFile(wheel) as source, zipfile.ZipFile(rewritten, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name == record:
                rows = list(csv.reader(StringIO(data.decode())))
                data = _csv([row for row in rows if row[0] not in other])
            target.writestr(name, data)
    rewritten.replace(wheel)
    before = wheel.read_bytes()

    with pytest.raises(PruneWheelSbomError, match="no entries for removed SBOM"):
        prune_wheel_sboms(wheel)
    assert wheel.read_bytes() == before


def test_task_embeds_exact_tested_helper_and_orders_policy():
    import yaml

    root = Path(__file__).resolve().parents[1]
    task = yaml.safe_load(
        (root / "tekton/tasks/python-fromager-build-wheels/0.1/python-fromager-build-wheels.yaml").read_text()
    )
    steps = task["spec"]["steps"]
    names = [step["name"] for step in steps]
    prune = next(step for step in steps if step["name"] == "prune-wheel-sboms")
    assert prune["script"] == (root / "hack/prune_wheel_sboms.py").read_text()
    assert prune["image"] == next(step["image"] for step in steps if step["name"] == "build-wheels")
    assert names.index("collect-build-files") + 1 == names.index("prune-wheel-sboms")
    assert names.index("prune-wheel-sboms") + 1 == names.index("verify-allowed-artifacts")
    assert names.index("verify-allowed-artifacts") < names.index("create-oci-artifact")


def _rewrite(path, mutate):
    with zipfile.ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    mutate(members)
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)


@pytest.mark.parametrize(
    "corruption",
    [
        "missing-sbom",
        "missing-record",
        "missing-self",
        "duplicate-row",
        "malformed-row",
        "malformed-csv",
        "stale-row",
        "digest",
        "size",
        "metadata-name",
        "metadata-version",
        "second-metadata",
        "second-record",
        "signature-jws",
        "signature-p7s",
        "duplicate-path",
    ],
)
def test_corrupt_wheels_fail_closed_even_without_extra_sboms(tmp_path, corruption):
    wheel = tmp_path / "demo_pkg-1.0-py3-none-any.whl"
    allowed, _, record, _ = _wheel(wheel)

    def mutate(members):
        rows = list(csv.reader(StringIO(members[record].decode())))
        if corruption == "missing-sbom":
            del members[allowed]
        elif corruption == "missing-record":
            del members[record]
        elif corruption == "missing-self":
            members[record] = _csv([row for row in rows if row[0] != record])
        elif corruption == "duplicate-row":
            members[record] = _csv(rows + [rows[0]])
        elif corruption == "malformed-row":
            members[record] = _csv(rows + [["bad"]])
        elif corruption == "malformed-csv":
            members[record] = b'"unterminated'
        elif corruption == "stale-row":
            members[record] = _csv(rows + [["absent", "", ""]])
        elif corruption in {"digest", "size"}:
            rows[0][1 if corruption == "digest" else 2] = "wrong"
            members[record] = _csv(rows)
        elif corruption.startswith("metadata-"):
            members[record.replace("RECORD", "METADATA")] = (
                b"Name: other\nVersion: 1.0\n" if corruption == "metadata-name" else b"Name: demo-pkg\nVersion: 2.0\n"
            )
        elif corruption.startswith("second-"):
            suffix = "METADATA" if corruption == "second-metadata" else "RECORD"
            members["other-1.0.dist-info/" + suffix] = members[record.replace("RECORD", suffix)]
        elif corruption.startswith("signature-"):
            members[record + "." + corruption.split("-")[1]] = b"signature"

    _rewrite(wheel, mutate)
    if corruption == "duplicate-path":
        with pytest.warns(UserWarning), zipfile.ZipFile(wheel, "a") as archive:
            archive.writestr(allowed, b"{}")
    before = wheel.read_bytes()
    with pytest.raises(PruneWheelSbomError):
        prune_wheel_sboms(wheel, "demo-pkg", "1.0")
    assert wheel.read_bytes() == before


def test_primary_selection_skips_dependency_before_opening_and_preserves_mode_and_vendor(tmp_path):
    from hack.prune_wheel_sboms import primary_wheels

    dependency = tmp_path / "dependency-1.0-py3-none-any.whl"
    dependency.write_bytes(b"not even a zip: must not open")
    primary = tmp_path / "demo_pkg-1.0-py3-none-any.whl"
    _, other, record, _ = _wheel(primary, extra_sboms=("other.json",))
    vendor = "demo_pkg/vendor/vendor-1.0.dist-info/sboms/other.json"

    def mutate(members):
        members[vendor] = b"vendor sbom"
        rows = list(csv.reader(StringIO(members[record].decode())))
        members[record] = _csv(rows + [[vendor, _digest(members[vendor]), str(len(members[vendor]))]])

    _rewrite(primary, mutate)
    primary.chmod(0o754)
    before = dependency.read_bytes()
    candidates = primary_wheels(tmp_path, "Demo_Pkg", "1.0")
    assert candidates == [primary]
    assert prune_wheel_sboms(primary, "Demo_Pkg", "1.0") == other
    assert primary.stat().st_mode & 0o777 == 0o754
    assert dependency.read_bytes() == before
    with zipfile.ZipFile(primary) as archive:
        assert archive.read(vendor) == b"vendor sbom"


@pytest.mark.parametrize("filename", [None, "demo_pkg-2.0-py3-none-any.whl"])
def test_no_matching_primary_fails_closed(tmp_path, filename):
    from hack.prune_wheel_sboms import primary_wheels

    if filename:
        (tmp_path / filename).write_bytes(b"do not open")
    with pytest.raises(PruneWheelSbomError):
        primary_wheels(tmp_path, "demo-pkg", "1.0")


def test_task_script_prunes_multiple_primary_wheels_not_dependencies_or_build_index(tmp_path):
    import os
    import subprocess
    import sys

    import yaml

    root = Path(__file__).resolve().parents[1]
    steps = yaml.safe_load(
        (root / "tekton/tasks/python-fromager-build-wheels/0.1/python-fromager-build-wheels.yaml").read_text()
    )["spec"]["steps"]
    script = next(step["script"] for step in steps if step["name"] == "prune-wheel-sboms")
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    wheels = [artifact / f"demo_pkg-1.0-{tag}.whl" for tag in ("py3-none-any", "cp311-cp311-linux_x86_64")]
    for wheel in wheels:
        _wheel(wheel, extra_sboms=("other.json",))
    dependency = artifact / "dependency-1.0-py3-none-any.whl"
    dependency.write_bytes(b"dependency remains unopened")
    build_index = artifact / "build-index.json"
    build_index.write_bytes(b"index referrer remains unchanged")
    before = {path: path.read_bytes() for path in (dependency, build_index)}
    env = dict(os.environ, PACKAGE="demo-pkg", COMPUTED_VERSION="1.0")
    result = subprocess.run(
        [sys.executable, "-c", script.replace("/var/workdir", str(tmp_path))], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            assert not any(name.endswith("/other.json") for name in archive.namelist())
    assert all(path.read_bytes() == data for path, data in before.items())
