"""
Copyright (C) 2026 Lightwell

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

         http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import textwrap

import pytest

from import_orchestrator.ecosystems.java.parser import parse_manifest


def _write_manifest(tmp_path, content):
    p = tmp_path / "manifest.yaml"
    p.write_text(textwrap.dedent(content))
    return p


def test_tag_and_digest_combined(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        """\
        libraries:
          - output:
              artifact:
                tag: "quay.io/ns/repo:build-1"
                digest: "quay.io/ns/repo@sha256:aaa"
        """,
    )
    assert parse_manifest(manifest) == ["quay.io/ns/repo:build-1@sha256:aaa"]


def test_digest_only(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        """\
        libraries:
          - output:
              artifact:
                digest: "quay.io/ns/repo@sha256:bbb"
        """,
    )
    assert parse_manifest(manifest) == ["quay.io/ns/repo@sha256:bbb"]


def test_tag_only(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        """\
        libraries:
          - output:
              artifact:
                tag: "quay.io/ns/repo:build-2"
        """,
    )
    assert parse_manifest(manifest) == ["quay.io/ns/repo:build-2"]


def test_no_artifact_skipped(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        """\
        libraries:
          - output: {}
        """,
    )
    assert parse_manifest(manifest) == []


def test_empty_libraries(tmp_path):
    manifest = _write_manifest(tmp_path, "libraries: []\n")
    assert parse_manifest(manifest) == []


def test_malformed_digest_raises(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        """\
        libraries:
          - output:
              artifact:
                tag: "quay.io/ns/repo:build-1"
                digest: "BAD_DIGEST_NO_AT"
        """,
    )
    with pytest.raises(ValueError, match="Malformed digest"):
        parse_manifest(manifest)


def test_multiple_libraries(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        """\
        libraries:
          - output:
              artifact:
                tag: "quay.io/ns/repo:build-1"
                digest: "quay.io/ns/repo@sha256:aaa"
          - output:
              artifact:
                digest: "quay.io/ns/other@sha256:bbb"
          - output: {}
        """,
    )
    assert parse_manifest(manifest) == [
        "quay.io/ns/repo:build-1@sha256:aaa",
        "quay.io/ns/other@sha256:bbb",
    ]
