#!/usr/bin/env python3
"""Remove non-Red-Hat SBOMs from a wheel and keep RECORD consistent."""

from __future__ import annotations

import argparse
import csv
import sys
import tempfile
import zipfile
from io import StringIO
from pathlib import Path

SBOM_DIR_MARKER = ".dist-info/sboms/"
ALLOWED_SBOM_SUFFIX = ".dist-info/sboms/redhat.spdx.json"
RECORD_SUFFIX = ".dist-info/RECORD"


class PruneWheelSbomError(ValueError):
    """Raised when the wheel cannot safely be pruned."""


def _single(names: list[str], suffix: str, label: str) -> str:
    matches = [name for name in names if name.endswith(suffix)]
    if len(matches) != 1:
        raise PruneWheelSbomError(f"expected exactly one {label} in wheel, found {len(matches)}")
    return matches[0]


def _record_bytes(rows: list[list[str]]) -> bytes:
    output = StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(rows)
    return output.getvalue().encode()


def _update_record(record_data: bytes, removed: set[str]) -> bytes:
    try:
        rows = list(csv.reader(StringIO(record_data.decode(), newline="")))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise PruneWheelSbomError(f"wheel RECORD is invalid: {exc}") from exc

    present = set()
    kept = []
    for row in rows:
        if len(row) != 3:
            raise PruneWheelSbomError("wheel RECORD contains a row with an unexpected number of fields")
        name = row[0]
        if name in removed:
            present.add(name)
        else:
            kept.append(row)
    missing = removed - present
    if missing:
        raise PruneWheelSbomError("wheel RECORD has no entries for removed SBOM(s): " + ", ".join(sorted(missing)))
    return _record_bytes(kept)


def prune_wheel_sboms(wheel_path: Path) -> list[str]:
    """Remove SBOMs under dist-info/sboms except redhat.spdx.json, in place."""
    if not wheel_path.is_file():
        raise PruneWheelSbomError(f"wheel does not exist: {wheel_path}")

    with zipfile.ZipFile(wheel_path, "r") as src:
        infos = src.infolist()
        names = [info.filename for info in infos]
        sboms = [name for name in names if SBOM_DIR_MARKER in name and not name.endswith("/")]
        removed = {name for name in sboms if not name.endswith(ALLOWED_SBOM_SUFFIX)}
        if not removed:
            return []

        record_member = _single(names, RECORD_SUFFIX, "RECORD file")
        signatures = [name for name in names if name.endswith((".dist-info/RECORD.jws", ".dist-info/RECORD.p7s"))]
        if signatures:
            raise PruneWheelSbomError(
                "cannot update RECORD in a signed wheel without regenerating its signature: " + ", ".join(signatures)
            )
        updated_record = _update_record(src.read(record_member), removed)

        with tempfile.NamedTemporaryFile(dir=wheel_path.parent, delete=False) as tmp_file:
            tmp_path = Path(tmp_file.name)
        try:
            with zipfile.ZipFile(tmp_path, "w") as dst:
                for info in infos:
                    if info.filename in removed:
                        continue
                    data = updated_record if info.filename == record_member else src.read(info.filename)
                    dst.writestr(info, data)
            tmp_path.replace(wheel_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
    return sorted(removed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path, help="Wheel file to prune in-place")
    args = parser.parse_args(argv)
    try:
        removed = prune_wheel_sboms(args.wheel)
    except (OSError, zipfile.BadZipFile, PruneWheelSbomError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if removed:
        print("Removed non-Red-Hat SBOM(s): " + ", ".join(removed))
    else:
        print("No non-Red-Hat SBOMs to remove")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
