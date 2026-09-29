#!/usr/bin/env python3
"""Synchronize a built wheel's embedded Red Hat SPDX SBOM with its remediated version.

Fromager can rewrite the primary wheel metadata/filename to a Lightwell computed
version (for example ``1.0+rhlw.1``) while the embedded Red Hat SBOM still
contains the upstream PyPI package version/PURL.  This utility updates only the
built wheel package entry (``SPDXRef-wheel``), rewrites the wheel in-place, and
updates the wheel ``RECORD`` entry for the changed SBOM.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import sys
import tempfile
import zipfile
from io import StringIO
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

WHEEL_SPDX_ID = "SPDXRef-wheel"
UPSTREAM_SPDX_ID = "SPDXRef-upstream"
SBOM_SUFFIX = ".dist-info/sboms/redhat.spdx.json"
RECORD_SUFFIX = ".dist-info/RECORD"


class SyncWheelSbomError(ValueError):
    """Raised when the wheel or SBOM cannot be safely synchronized."""


def _single_member(names: list[str], suffix: str, label: str) -> str:
    matches = [name for name in names if name.endswith(suffix)]
    if len(matches) != 1:
        raise SyncWheelSbomError(f"expected exactly one {label} in wheel, found {len(matches)}")
    return matches[0]


def _spdx_packages(sbom: dict) -> dict[str, dict]:
    packages = sbom.get("packages")
    if not isinstance(packages, list):
        raise SyncWheelSbomError("SPDX SBOM is missing a packages list")
    by_id: dict[str, dict] = {}
    for package in packages:
        if isinstance(package, dict) and isinstance(package.get("SPDXID"), str):
            by_id[package["SPDXID"]] = package
    return by_id


def _external_refs(package: dict) -> list[dict]:
    refs = package.get("externalRefs")
    if refs is None:
        refs = []
        package["externalRefs"] = refs
    if not isinstance(refs, list):
        raise SyncWheelSbomError(f"{package.get('SPDXID', '<unknown>')} externalRefs is not a list")
    return refs


def _pypi_purl_ref(package: dict) -> dict:
    matches = [
        ref
        for ref in _external_refs(package)
        if isinstance(ref, dict)
        and ref.get("referenceCategory") == "PACKAGE-MANAGER"
        and ref.get("referenceType") == "purl"
        and isinstance(ref.get("referenceLocator"), str)
        and ref["referenceLocator"].startswith("pkg:pypi/")
    ]
    if len(matches) != 1:
        raise SyncWheelSbomError(
            f"expected exactly one PyPI purl externalRef for {package.get('SPDXID', '<unknown>')}, found {len(matches)}"
        )
    return matches[0]


def _replace_wheel_purl_identity(purl: str, computed_version: str, wheel_filename: str) -> str:
    """Set the wheel version and filename, dropping any stale source download URL."""
    parts = urlsplit(purl)
    if parts.scheme != "pkg" or not parts.path.startswith("pypi/"):
        raise SyncWheelSbomError(f"not a PyPI package URL: {purl}")
    if "@" not in parts.path:
        raise SyncWheelSbomError(f"PyPI package URL has no version: {purl}")

    name, _old_version = parts.path.rsplit("@", 1)
    encoded_version = quote(computed_version, safe="")
    # PURL qualifiers are artifact-specific. Keep non-locating qualifiers, but
    # replace the old wheel filename and remove download_url because the output
    # image has no stable public wheel URL at build time. Source download
    # provenance remains on SPDXRef-upstream.
    qualifiers = []
    seen_filename = False
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key == "file_name":
            if not seen_filename:
                qualifiers.append((key, wheel_filename))
                seen_filename = True
            continue
        if key == "download_url":
            continue
        qualifiers.append((key, value))
    if not seen_filename:
        qualifiers.append(("file_name", wheel_filename))
    query = urlencode(qualifiers, doseq=True, quote_via=quote, safe="")
    return urlunsplit((parts.scheme, parts.netloc, f"{name}@{encoded_version}", query, parts.fragment))


def update_redhat_spdx_sbom(sbom_bytes: bytes, computed_version: str, wheel_filename: str) -> bytes:
    """Return SBOM JSON bytes with only SPDXRef-wheel rewritten."""
    try:
        sbom = json.loads(sbom_bytes)
    except json.JSONDecodeError as exc:
        raise SyncWheelSbomError(f"invalid SPDX JSON: {exc}") from exc
    if not isinstance(sbom, dict):
        raise SyncWheelSbomError("SPDX SBOM root must be a JSON object")

    packages = _spdx_packages(sbom)
    wheel = packages.get(WHEEL_SPDX_ID)
    if wheel is None:
        raise SyncWheelSbomError(f"SPDX SBOM is missing {WHEEL_SPDX_ID}")
    if UPSTREAM_SPDX_ID not in packages:
        raise SyncWheelSbomError(f"SPDX SBOM is missing {UPSTREAM_SPDX_ID}")

    wheel["versionInfo"] = computed_version
    purl_ref = _pypi_purl_ref(wheel)
    purl_ref["referenceLocator"] = _replace_wheel_purl_identity(
        purl_ref["referenceLocator"], computed_version, wheel_filename
    )

    return (json.dumps(sbom, indent=2) + "\n").encode()


def _record_digest_and_size(data: bytes) -> tuple[str, str]:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
    return f"sha256={digest}", str(len(data))


def update_record(record_bytes: bytes, member: str, member_bytes: bytes) -> bytes:
    text = record_bytes.decode()
    input_io = StringIO(text, newline="")
    rows = list(csv.reader(input_io))
    digest, size = _record_digest_and_size(member_bytes)
    updated = False
    for row in rows:
        if len(row) != 3:
            raise SyncWheelSbomError("wheel RECORD contains a row with an unexpected number of fields")
        if row[0] == member:
            row[1] = digest
            row[2] = size
            updated = True
            break
    if not updated:
        raise SyncWheelSbomError(f"wheel RECORD has no entry for {member}")

    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerows(rows)
    return output.getvalue().encode()


def sync_wheel_sbom(wheel_path: Path, computed_version: str) -> str:
    """Rewrite ``wheel_path`` in-place and return the SBOM member path."""
    if not computed_version:
        raise SyncWheelSbomError("computed version must be non-empty")
    if not wheel_path.is_file():
        raise SyncWheelSbomError(f"wheel does not exist: {wheel_path}")

    with zipfile.ZipFile(wheel_path, "r") as src:
        infos = src.infolist()
        names = [info.filename for info in infos]
        sbom_member = _single_member(names, SBOM_SUFFIX, "Red Hat SPDX SBOM")
        record_member = _single_member(names, RECORD_SUFFIX, "RECORD file")
        signatures = [name for name in names if name.endswith((".dist-info/RECORD.jws", ".dist-info/RECORD.p7s"))]
        if signatures:
            raise SyncWheelSbomError(
                "cannot update RECORD in a signed wheel without regenerating its signature: " + ", ".join(signatures)
            )
        updated_sbom = update_redhat_spdx_sbom(src.read(sbom_member), computed_version, wheel_path.name)
        updated_record = update_record(src.read(record_member), sbom_member, updated_sbom)

        with tempfile.NamedTemporaryFile(dir=wheel_path.parent, delete=False) as tmp_file:
            tmp_path = Path(tmp_file.name)
        try:
            with zipfile.ZipFile(tmp_path, "w") as dst:
                for info in infos:
                    data = src.read(info.filename)
                    if info.filename == sbom_member:
                        data = updated_sbom
                    elif info.filename == record_member:
                        data = updated_record
                    dst.writestr(info, data)
            tmp_path.replace(wheel_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
    return sbom_member


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path, help="Wheel file to rewrite in-place")
    parser.add_argument("computed_version", help="Computed Lightwell version to set on SPDXRef-wheel")
    args = parser.parse_args(argv)
    try:
        member = sync_wheel_sbom(args.wheel, args.computed_version)
    except (OSError, zipfile.BadZipFile, SyncWheelSbomError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Updated {member} in {args.wheel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
