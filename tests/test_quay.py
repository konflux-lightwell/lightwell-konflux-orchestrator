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

from unittest.mock import MagicMock, patch

import pytest

from import_orchestrator.clients import QuayClient


def _make_response(tags, status_code=200):
    """Create a mock requests.Response with the given tags."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"tags": tags}
    resp.raise_for_status.return_value = None
    return resp


def _make_tag(name, digest="sha256:abc123"):
    """Create a tag dict matching the Quay API shape."""
    return {"name": name, "manifest_digest": digest}


class TestQuayClientInit:
    """Test QuayClient initialization and parameter assignment."""

    def test_stores_all_explicit_params(self):
        client = QuayClient(
            token="explicit-token",
            ref="host.io/namespace/repo",
            timeout=60,
        )

        assert client.ref == "host.io/namespace/repo"
        assert client.timeout == 60

    def test_defaults(self):
        client = QuayClient(token="t", ref="repo")

        assert client.timeout == 30


class TestFetchOciReferences:
    """Test the fetch_oci_references method."""

    @pytest.fixture
    def client(self):
        return QuayClient(token="test-token", ref="quay.io/light-castle/rebuild-pnc")

    @patch("import_orchestrator.clients.quay.requests.get")
    def test_single_page(self, mock_get, client):
        tags = [_make_tag("lw-build-1", "sha256:aaa"), _make_tag("lw-build-2", "sha256:bbb")]
        mock_get.return_value = _make_response(tags)

        refs = client.fetch_oci_references()

        assert refs == [
            "quay.io/light-castle/rebuild-pnc:lw-build-1@sha256:aaa",
            "quay.io/light-castle/rebuild-pnc:lw-build-2@sha256:bbb",
        ]
        mock_get.assert_called_once()

    @patch("import_orchestrator.clients.quay.requests.get")
    def test_pagination(self, mock_get, client):
        page1_tags = [_make_tag(f"lw-build-{i}", f"sha256:{i:03d}") for i in range(100)]
        page2_tags = [_make_tag("lw-build-100", "sha256:100")]

        mock_get.side_effect = [_make_response(page1_tags), _make_response(page2_tags)]

        refs = client.fetch_oci_references()

        assert len(refs) == 101
        assert mock_get.call_count == 2

    @patch("import_orchestrator.clients.quay.requests.get")
    def test_filters_non_lw_tags(self, mock_get, client):
        tags = [
            _make_tag("lw-build-1", "sha256:aaa"),
            _make_tag("latest", "sha256:bbb"),
            _make_tag("v1.0", "sha256:ccc"),
            _make_tag("lw-build-2", "sha256:ddd"),
        ]
        mock_get.return_value = _make_response(tags)

        refs = client.fetch_oci_references()

        assert len(refs) == 2
        assert all("lw-" in ref for ref in refs)

    @patch("import_orchestrator.clients.quay.requests.get")
    def test_filters_tags_without_digest(self, mock_get, client):
        tags = [
            _make_tag("lw-build-1", "sha256:aaa"),
            {"name": "lw-build-2", "manifest_digest": None},
            {"name": "lw-build-3"},
        ]
        mock_get.return_value = _make_response(tags)

        refs = client.fetch_oci_references()

        assert refs == ["quay.io/light-castle/rebuild-pnc:lw-build-1@sha256:aaa"]

    @patch("import_orchestrator.clients.quay.requests.get")
    def test_deduplicates(self, mock_get, client):
        tags = [
            _make_tag("lw-build-1", "sha256:aaa"),
            _make_tag("lw-build-1", "sha256:aaa"),
        ]
        mock_get.return_value = _make_response(tags)

        refs = client.fetch_oci_references()

        assert refs == ["quay.io/light-castle/rebuild-pnc:lw-build-1@sha256:aaa"]

    @patch("import_orchestrator.clients.quay.requests.get")
    def test_sorted_output(self, mock_get, client):
        tags = [
            _make_tag("lw-build-c", "sha256:ccc"),
            _make_tag("lw-build-a", "sha256:aaa"),
            _make_tag("lw-build-b", "sha256:bbb"),
        ]
        mock_get.return_value = _make_response(tags)

        refs = client.fetch_oci_references()

        assert refs == sorted(refs)

    @patch("import_orchestrator.clients.quay.requests.get")
    def test_empty_response(self, mock_get, client):
        mock_get.return_value = _make_response([])

        refs = client.fetch_oci_references()

        assert refs == []

    @patch("import_orchestrator.clients.quay.requests.get")
    def test_passes_timeout_to_request(self, mock_get):
        client = QuayClient(token="t", ref="quay.io/namespace/repo", timeout=30)
        mock_get.return_value = _make_response([_make_tag("lw-x", "sha256:abc")])

        client.fetch_oci_references()

        _, kwargs = mock_get.call_args
        assert kwargs["timeout"] == 30

    @patch("import_orchestrator.clients.quay.requests.get")
    def test_passes_default_timeout_to_request(self, mock_get):
        client = QuayClient(token="t", ref="quay.io/namespace/repo")
        mock_get.return_value = _make_response([_make_tag("lw-x", "sha256:abc")])

        client.fetch_oci_references()

        _, kwargs = mock_get.call_args
        assert kwargs["timeout"] == 30
