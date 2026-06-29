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

import subprocess

from import_orchestrator.utils import extract_tag, should_retry


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


class TestShouldRetry:
    def _make_error(self, returncode: int = 1, stderr: str = "") -> subprocess.CalledProcessError:
        error = subprocess.CalledProcessError(returncode, "cmd")
        error.stderr = stderr
        return error

    def test_generic_error_is_retryable(self):
        error = self._make_error(returncode=1, stderr="connection timeout")
        assert should_retry(error) is True

    def test_validation_error_is_not_retryable(self):
        error = self._make_error(returncode=1, stderr="Validation Error: invalid field")
        assert should_retry(error) is False

    def test_authentication_error_is_not_retryable(self):
        error = self._make_error(returncode=1, stderr="Authentication failed")
        assert should_retry(error) is False

    def test_exit_code_2_is_not_retryable(self):
        error = self._make_error(returncode=2, stderr="something went wrong")
        assert should_retry(error) is False

    def test_bytes_stderr_is_handled(self):
        error = subprocess.CalledProcessError(1, "cmd")
        error.stderr = b"connection refused"
        assert should_retry(error) is True

    def test_bytes_stderr_with_validation_error(self):
        error = subprocess.CalledProcessError(1, "cmd")
        error.stderr = b"Validation Error: bad input"
        assert should_retry(error) is False

    def test_none_stderr_is_handled(self):
        error = subprocess.CalledProcessError(1, "cmd")
        error.stderr = None
        assert should_retry(error) is True
