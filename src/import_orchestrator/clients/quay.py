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

from urllib.parse import urlsplit

import requests

_DEFAULT_TIMEOUT = 30
_PAGE_SIZE = 100


class QuayClient:
    """Client for fetching OCI tag references from the Quay.io API."""

    def __init__(
        self,
        token: str,
        ref: str,
        timeout: int = _DEFAULT_TIMEOUT,
    ):
        self.token = token
        self.ref = ref
        self.timeout = timeout

    def fetch_oci_references(self) -> list[str]:
        """Fetch OCI references from Quay from a given repository.

        Paginates through active tags prefixed with "lw-", builds OCI reference
        strings, and returns them sorted and deduplicated.

        Returns:
            Sorted list of unique OCI reference strings.

        Raises:
            requests.HTTPError: If the Quay API returns an error response.
        """
        tags = self._fetch_all_tags()

        refs: set[str] = set()
        for tag in tags:
            name = tag.get("name", "")
            digest = tag.get("manifest_digest")
            if name.startswith("lw-") and digest:
                refs.add(f"{self.ref}:{name}@{digest}")

        return sorted(refs)

    def _fetch_all_tags(self) -> list[dict]:
        """Paginate through the Quay tag listing API and return all tags."""
        all_tags: list[dict] = []
        page = 1

        # Adding the scheme here is necessary for proper URL parsing
        url_parts = urlsplit(f"https://{self.ref}")
        host = url_parts.netloc
        repo = url_parts.path.lstrip("/")

        while True:
            url = f"https://{host}/api/v1/repository/{repo}/tag/?onlyActiveTags=true&limit={_PAGE_SIZE}&page={page}"
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
