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

from import_orchestrator.quay import QuayClient


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
    """Test QuayClient initialization and config resolution."""

    def test_missing_token_raises(self, monkeypatch):
        monkeypatch.delenv("QUAY_TOKEN", raising=False)
        with pytest.raises(ValueError, match="QUAY_TOKEN is required"):
            QuayClient()

    def test_explicit_token(self, monkeypatch):
        monkeypatch.delenv("QUAY_TOKEN", raising=False)
        client = QuayClient(token="my-token")
        assert client.token == "my-token"

    def test_env_token(self, monkeypatch):
        monkeypatch.setenv("QUAY_TOKEN", "env-token")
        client = QuayClient()
        assert client.token == "env-token"

    def test_explicit_params_override_env(self, monkeypatch):
        monkeypatch.setenv("QUAY_TOKEN", "env-token")
        monkeypatch.setenv("QUAY_HOST", "https://env-host.io")
        monkeypatch.setenv("QUAY_NAMESPACE", "env-ns")
        monkeypatch.setenv("QUAY_REBUILD_REPO", "env-rebuild")
        monkeypatch.setenv("QUAY_REMEDIATED_REPO", "env-remediated")
        monkeypatch.setenv("QUAY_TIMEOUT", "30")

        client = QuayClient(
            token="explicit-token",
            host="https://explicit-host.io",
            namespace="explicit-ns",
            rebuild_repo="explicit-rebuild",
            remediated_repo="explicit-remediated",
            timeout=60,
        )

        assert client.token == "explicit-token"
        assert client.host == "https://explicit-host.io"
        assert client.namespace == "explicit-ns"
        assert client.rebuild_repo == "explicit-rebuild"
        assert client.remediated_repo == "explicit-remediated"
        assert client.timeout == 60

    def test_env_timeout(self, monkeypatch):
        monkeypatch.setenv("QUAY_TOKEN", "t")
        monkeypatch.setenv("QUAY_TIMEOUT", "30")
        client = QuayClient()
        assert client.timeout == "30"

    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("QUAY_HOST", raising=False)
        monkeypatch.delenv("QUAY_NAMESPACE", raising=False)
        monkeypatch.delenv("QUAY_REBUILD_REPO", raising=False)
        monkeypatch.delenv("QUAY_REMEDIATED_REPO", raising=False)
        monkeypatch.delenv("QUAY_TIMEOUT", raising=False)

        client = QuayClient(token="t")

        assert client.host == "https://quay.io"
        assert client.namespace == "light-castle"
        assert client.rebuild_repo == "rebuild-pnc"
        assert client.remediated_repo == "secure-pnc"
        assert client.timeout == 30


class TestFetchOciReferences:
    """Test the fetch_oci_references method."""

    @pytest.fixture
    def client(self):
        return QuayClient(token="test-token")

    @patch("import_orchestrator.quay.requests.get")
    def test_single_page(self, mock_get, client):
        tags = [_make_tag("lw-build-1", "sha256:aaa"), _make_tag("lw-build-2", "sha256:bbb")]
        mock_get.return_value = _make_response(tags)

        refs = client.fetch_oci_references("REBUILD")

        assert refs == [
            "quay.io/light-castle/rebuild-pnc:lw-build-1@sha256:aaa",
            "quay.io/light-castle/rebuild-pnc:lw-build-2@sha256:bbb",
        ]
        mock_get.assert_called_once()

    @patch("import_orchestrator.quay.requests.get")
    def test_pagination(self, mock_get, client):
        page1_tags = [_make_tag(f"lw-build-{i}", f"sha256:{i:03d}") for i in range(100)]
        page2_tags = [_make_tag("lw-build-100", "sha256:100")]

        mock_get.side_effect = [_make_response(page1_tags), _make_response(page2_tags)]

        refs = client.fetch_oci_references("REBUILD")

        assert len(refs) == 101
        assert mock_get.call_count == 2

    @patch("import_orchestrator.quay.requests.get")
    def test_filters_non_lw_tags(self, mock_get, client):
        tags = [
            _make_tag("lw-build-1", "sha256:aaa"),
            _make_tag("latest", "sha256:bbb"),
            _make_tag("v1.0", "sha256:ccc"),
            _make_tag("lw-build-2", "sha256:ddd"),
        ]
        mock_get.return_value = _make_response(tags)

        refs = client.fetch_oci_references("REBUILD")

        assert len(refs) == 2
        assert all("lw-" in ref for ref in refs)

    @patch("import_orchestrator.quay.requests.get")
    def test_filters_tags_without_digest(self, mock_get, client):
        tags = [
            _make_tag("lw-build-1", "sha256:aaa"),
            {"name": "lw-build-2", "manifest_digest": None},
            {"name": "lw-build-3"},
        ]
        mock_get.return_value = _make_response(tags)

        refs = client.fetch_oci_references("REBUILD")

        assert refs == ["quay.io/light-castle/rebuild-pnc:lw-build-1@sha256:aaa"]

    @patch("import_orchestrator.quay.requests.get")
    def test_deduplicates(self, mock_get, client):
        tags = [
            _make_tag("lw-build-1", "sha256:aaa"),
            _make_tag("lw-build-1", "sha256:aaa"),
        ]
        mock_get.return_value = _make_response(tags)

        refs = client.fetch_oci_references("REBUILD")

        assert refs == ["quay.io/light-castle/rebuild-pnc:lw-build-1@sha256:aaa"]

    @patch("import_orchestrator.quay.requests.get")
    def test_sorted_output(self, mock_get, client):
        tags = [
            _make_tag("lw-build-c", "sha256:ccc"),
            _make_tag("lw-build-a", "sha256:aaa"),
            _make_tag("lw-build-b", "sha256:bbb"),
        ]
        mock_get.return_value = _make_response(tags)

        refs = client.fetch_oci_references("REBUILD")

        assert refs == sorted(refs)

    @patch("import_orchestrator.quay.requests.get")
    def test_remediated_uses_correct_repo(self, mock_get, client):
        tags = [_make_tag("lw-build-1", "sha256:aaa")]
        mock_get.return_value = _make_response(tags)

        refs = client.fetch_oci_references("REMEDIATED")

        assert refs == ["quay.io/light-castle/secure-pnc:lw-build-1@sha256:aaa"]
        url = mock_get.call_args[0][0]
        assert "secure-pnc" in url

    def test_invalid_artifact_type(self, client):
        with pytest.raises(ValueError, match="artifact_type must be one of"):
            client.fetch_oci_references("INVALID")

    @patch("import_orchestrator.quay.requests.get")
    def test_empty_response(self, mock_get, client):
        mock_get.return_value = _make_response([])

        refs = client.fetch_oci_references("REBUILD")

        assert refs == []

    @patch("import_orchestrator.quay.requests.get")
    def test_passes_timeout_to_request(self, mock_get):
        client = QuayClient(token="t", timeout=30)
        mock_get.return_value = _make_response([_make_tag("lw-x", "sha256:abc")])

        client.fetch_oci_references("REBUILD")

        _, kwargs = mock_get.call_args
        assert kwargs["timeout"] == 30

    @patch("import_orchestrator.quay.requests.get")
    def test_passes_default_timeout_to_request(self, mock_get):
        client = QuayClient(token="t")
        mock_get.return_value = _make_response([_make_tag("lw-x", "sha256:abc")])

        client.fetch_oci_references("REBUILD")

        _, kwargs = mock_get.call_args
        assert kwargs["timeout"] == 30

    @patch("import_orchestrator.quay.requests.get")
    def test_strips_https_from_host(self, mock_get):
        client = QuayClient(token="t", host="https://my-quay.example.com")
        mock_get.return_value = _make_response([_make_tag("lw-x", "sha256:abc")])

        refs = client.fetch_oci_references("REBUILD")

        assert refs == ["my-quay.example.com/light-castle/rebuild-pnc:lw-x@sha256:abc"]
