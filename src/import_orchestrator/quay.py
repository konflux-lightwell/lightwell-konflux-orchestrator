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

from __future__ import annotations

import os

import requests

_VALID_ARTIFACT_TYPES = ("REBUILD", "REMEDIATED")

_DEFAULT_HOST = "https://quay.io"
_DEFAULT_NAMESPACE = "light-castle"
_DEFAULT_REBUILD_REPO = "rebuild-pnc"
_DEFAULT_REMEDIATED_REPO = "secure-pnc"
_DEFAULT_TIMEOUT = 30

_PAGE_SIZE = 100


class QuayClient:
    """Client for fetching OCI tag references from the Quay.io API."""

    def __init__(
        self,
        token: str | None = None,
        host: str | None = None,
        namespace: str | None = None,
        rebuild_repo: str | None = None,
        remediated_repo: str | None = None,
        timeout: int | None = None,
    ):
        resolved_token = token or os.environ.get("QUAY_TOKEN")
        if not resolved_token:
            raise ValueError(
                "QUAY_TOKEN is required. Create a Quay API token, then either pass it "
                "as the 'token' parameter or export QUAY_TOKEN='your-token-here'."
            )
        self.token = resolved_token
        self.host = host or os.environ.get("QUAY_HOST", _DEFAULT_HOST)
        self.namespace = namespace or os.environ.get("QUAY_NAMESPACE", _DEFAULT_NAMESPACE)
        self.rebuild_repo = rebuild_repo or os.environ.get("QUAY_REBUILD_REPO", _DEFAULT_REBUILD_REPO)
        self.remediated_repo = remediated_repo or os.environ.get("QUAY_REMEDIATED_REPO", _DEFAULT_REMEDIATED_REPO)
        self.timeout = timeout or os.environ.get("QUAY_TIMEOUT", _DEFAULT_TIMEOUT)

    def fetch_oci_references(self, artifact_type: str = "REBUILD") -> list[str]:
        """Fetch OCI references from Quay for the given artifact type.

        Paginates through active tags prefixed with "lw-", builds OCI reference
        strings, and returns them sorted and deduplicated.

        Args:
            artifact_type: REBUILD or REMEDIATED. Determines which Quay repo to query.

        Returns:
            Sorted list of unique OCI reference strings.

        Raises:
            ValueError: If artifact_type is not REBUILD or REMEDIATED.
            requests.HTTPError: If the Quay API returns an error response.
        """
        if artifact_type not in _VALID_ARTIFACT_TYPES:
            raise ValueError(f"artifact_type must be one of: {', '.join(_VALID_ARTIFACT_TYPES)}. Got: {artifact_type}")

        repo = self.rebuild_repo if artifact_type == "REBUILD" else self.remediated_repo
        image_ref = f"{self.host.removeprefix('https://').removeprefix('http://')}/{self.namespace}/{repo}"

        tags = self._fetch_all_tags(repo)

        refs: set[str] = set()
        for tag in tags:
            name = tag.get("name", "")
            digest = tag.get("manifest_digest")
            if name.startswith("lw-") and digest:
                refs.add(f"{image_ref}:{name}@{digest}")

        return sorted(refs)

    def _fetch_all_tags(self, repo: str) -> list[dict]:
        """Paginate through the Quay tag listing API and return all tags."""
        all_tags: list[dict] = []
        page = 1

        while True:
            url = (
                f"{self.host}/api/v1/repository/{self.namespace}/{repo}/tag/"
                f"?onlyActiveTags=true&limit={_PAGE_SIZE}&page={page}"
            )
            response = requests.get(
                url,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/json",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()

            data = response.json()
            tags = data.get("tags", [])
            all_tags.extend(tags)

            if len(tags) < _PAGE_SIZE:
                break
            page += 1

        return all_tags
