#!/usr/bin/env python3
"""Apply the Task's embedded-SBOM policy to primary wheels only."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import os
import stat
import sys
import tempfile
import zipfile
from email.parser import BytesParser
from io import StringIO
from pathlib import Path

from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version


class PruneWheelSbomError(ValueError):
    """Raised when the wheel cannot safely be pruned."""


def _single(names: list[str], suffix: str, label: str) -> str:
    matches = [name for name in names if name.endswith(suffix) and name.count("/") == 1]
    if len(matches) != 1:
        raise PruneWheelSbomError(f"expected exactly one {label} in wheel, found {len(matches)}")
    return matches[0]


def validate_record(wheel: zipfile.ZipFile, record: str, sboms: list[str]) -> list[list[str]]:
    """Reject ambiguous/stale rows and verify recorded hashes for present files."""
    names = wheel.namelist()
    if len(names) != len(set(names)):
        raise PruneWheelSbomError("duplicate archive paths")
    if any(name in names for name in (record + ".jws", record + ".p7s")):
        raise PruneWheelSbomError("cannot update RECORD in a signed wheel")
    try:
        rows = list(csv.reader(StringIO(wheel.read(record).decode(), newline=""), strict=True))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise PruneWheelSbomError(f"wheel RECORD is invalid: {exc}") from exc
    entries = {}
    for row in rows:
        if len(row) != 3 or not row[0] or row[0] in entries:
            raise PruneWheelSbomError("malformed or duplicate RECORD row")
        entries[row[0]] = row
    missing = set(sboms) - entries.keys()
    if missing:
        raise PruneWheelSbomError(
            "RECORD has no entries for removed SBOM or curated SBOM: " + ", ".join(sorted(missing))
        )
    files = {name for name in names if not name.endswith("/")}
    if set(entries) != files or entries.get(record) != [record, "", ""]:
        raise PruneWheelSbomError("RECORD must cover present wheel files and keep its empty self row")
    for name, digest, size in rows:
        if name == record:
            continue
        if not digest and not size and name not in sboms:
            continue  # Wheel format permits unrecorded hashes for non-SBOM files.
        data = wheel.read(name)
        try:
            algorithm, encoded = digest.split("=", 1)
            if algorithm in {"md5", "sha1"}:
                raise ValueError("weak hash")
            actual = base64.urlsafe_b64encode(hashlib.new(algorithm, data).digest()).decode().rstrip("=")
        except ValueError as exc:
            raise PruneWheelSbomError(f"invalid RECORD digest for {name}") from exc
        if encoded != actual or size != str(len(data)):
            raise PruneWheelSbomError(f"RECORD hash/size mismatch for {name}")
    return rows


def prune_wheel_sboms(wheel_path: Path, package: str | None = None, version: str | None = None) -> list[str]:
    """Atomically prune only the wheel's own dist-info, preserving content and mode."""
    with zipfile.ZipFile(wheel_path) as src:
        names = src.namelist()
        if len(names) != len(set(names)):
            raise PruneWheelSbomError("duplicate archive paths")
        metadata_member = _single(names, ".dist-info/METADATA", "METADATA file")
        metadata = BytesParser().parsebytes(src.read(metadata_member))
        if len(metadata.get_all("Name", [])) != 1 or len(metadata.get_all("Version", [])) != 1:
            raise PruneWheelSbomError("missing or ambiguous METADATA identity")
        filename_name, filename_version, _, _ = parse_wheel_filename(wheel_path.name)
        expected_name = canonicalize_name(package) if package is not None else filename_name
        expected_version = Version(version) if version is not None else filename_version
        if (
            canonicalize_name(metadata["Name"]) != expected_name
            or Version(metadata["Version"]) != expected_version
            or filename_name != expected_name
            or filename_version != expected_version
        ):
            raise PruneWheelSbomError("wheel METADATA/filename does not match PACKAGE and COMPUTED_VERSION")
        prefix = metadata_member.rsplit("/", 1)[0] + "/"
        record = _single(names, ".dist-info/RECORD", "RECORD file")
        if record != prefix + "RECORD":
            raise PruneWheelSbomError("METADATA and RECORD disagree on dist-info directory")
        sboms = [name for name in names if name.startswith(prefix + "sboms/") and not name.endswith("/")]
        allowed = prefix + "sboms/redhat.spdx.json"
        if allowed not in sboms:
            raise PruneWheelSbomError("expected exactly one curated Red Hat SBOM")
        rows = validate_record(src, record, sboms)
        removed = set(sboms) - {allowed}
        if not removed:
            return []
        output = StringIO(newline="")
        csv.writer(output, lineterminator="\n").writerows(row for row in rows if row[0] not in removed)
        mode = stat.S_IMODE(wheel_path.stat().st_mode)
        with tempfile.NamedTemporaryFile(dir=wheel_path.parent, delete=False) as tmp_file:
            tmp_path = Path(tmp_file.name)
        try:
            with zipfile.ZipFile(tmp_path, "w") as dst:
                dst.comment = src.comment
                for info in src.infolist():
                    if info.filename not in removed:
                        dst.writestr(info, output.getvalue().encode() if info.filename == record else src.read(info))
            tmp_path.chmod(mode)
            tmp_path.replace(wheel_path)
        finally:
            tmp_path.unlink(missing_ok=True)
    return sorted(removed)


def primary_wheels(directory: Path, package: str, version: str) -> list[Path]:
    """Select by parsed filename before opening any archive (dependencies untouched)."""
    candidates = []
    for path in sorted(directory.glob("*.whl")):
        name, wheel_version, _, _ = parse_wheel_filename(path.name)
        if name == canonicalize_name(package):
            if wheel_version != Version(version):
                raise PruneWheelSbomError(
                    f"primary wheel filename version does not match COMPUTED_VERSION: {path.name}"
                )
            candidates.append(path)
    if not candidates:
        raise PruneWheelSbomError(f"expected a primary wheel for {package}, found none")
    return candidates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path, nargs="?", help="Wheel file to prune in-place")
    args = parser.parse_args(argv)
    try:
        if args.wheel:
            print(prune_wheel_sboms(args.wheel))
        else:
            package, version = os.environ["PACKAGE"], os.environ["COMPUTED_VERSION"]
            for wheel in primary_wheels(Path("/var/workdir/artifact"), package, version):
                print(f"{wheel.name}: removed {prune_wheel_sboms(wheel, package, version)}")
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
