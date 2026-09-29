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
