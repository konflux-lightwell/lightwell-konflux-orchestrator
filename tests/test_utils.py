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

from import_orchestrator.utils import extract_tag


class TestExtractTag:
    def test_standard_oci_reference(self):
        ref = "quay.io/redhat-user-workloads/lightwell-poc-tenant/pnc-import/pnc-import:lw-BPRVHPONFDQAA@sha256:abc123"
        assert extract_tag(ref) == "lw-BPRVHPONFDQAA"

    def test_tag_with_dots_and_dashes(self):
        ref = "quay.io/repo:v1.2.3-rc1@sha256:def456"
        assert extract_tag(ref) == "v1.2.3-rc1"

    def test_no_tag_separator_falls_back_to_last_40_chars(self):
        ref = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJ"
        result = extract_tag(ref)
        assert result == ref[-40:]

    def test_short_string_without_tag_returns_entire_string(self):
        ref = "short"
        assert extract_tag(ref) == "short"

    def test_reference_without_digest(self):
        # No @ sign, so regex won't match
        ref = "quay.io/repo:tagonly"
        assert extract_tag(ref) == ref[-40:]
