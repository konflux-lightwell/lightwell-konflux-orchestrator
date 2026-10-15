package build_index_digest_test

import rego.v1

import data.build_index_digest

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

_valid_digest := "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
_valid_digest_2 := "sha256:fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"

_malformed_short := "sha256:0123456789abcdef"
_malformed_upper := "sha256:0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF"
_malformed_no_prefix := "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
_malformed_bad_char := "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdeg"

# ---------------------------------------------------------------------------
# Tests — present + valid passes
# ---------------------------------------------------------------------------

test_digest_present_and_valid_v02 if {
	count(build_index_digest.deny) == 0 with input.attestations as [_mock_v02(_valid_digest)]
}

test_digest_present_and_valid_v1 if {
	count(build_index_digest.deny) == 0 with input.attestations as [_mock_v1(_valid_digest)]
}

test_digest_present_and_valid_v1_bare_name if {
	count(build_index_digest.deny) == 0 with input.attestations as [_mock_v1_bare_name(_valid_digest)]
}

test_multiple_valid_digests_pass if {
	count(build_index_digest.deny) == 0 with input.attestations as [
		_mock_v02(_valid_digest),
		_mock_v1(_valid_digest_2),
	]
}

# ---------------------------------------------------------------------------
# Tests — missing result denies
# ---------------------------------------------------------------------------

test_digest_missing_v02_denied if {
	deny := build_index_digest.deny with input.attestations as [_mock_v02_no_digest]
	_has_code(deny, "build_index_digest.build_index_digest_result_present")
}

test_digest_missing_v1_denied if {
	deny := build_index_digest.deny with input.attestations as [_mock_v1_no_digest]
	_has_code(deny, "build_index_digest.build_index_digest_result_present")
}

# ---------------------------------------------------------------------------
# Tests — malformed digest denies
# ---------------------------------------------------------------------------

test_digest_malformed_short_denied if {
	deny := build_index_digest.deny with input.attestations as [_mock_v02(_malformed_short)]
	_has_code(deny, "build_index_digest.build_index_digest_well_formed")
}

test_digest_malformed_upper_denied if {
	deny := build_index_digest.deny with input.attestations as [_mock_v02(_malformed_upper)]
	_has_code(deny, "build_index_digest.build_index_digest_well_formed")
}

test_digest_malformed_no_prefix_denied if {
	deny := build_index_digest.deny with input.attestations as [_mock_v1(_malformed_no_prefix)]
	_has_code(deny, "build_index_digest.build_index_digest_well_formed")
}

test_digest_malformed_bad_char_denied if {
	deny := build_index_digest.deny with input.attestations as [_mock_v02(_malformed_bad_char)]
	_has_code(deny, "build_index_digest.build_index_digest_well_formed")
}

# ---------------------------------------------------------------------------
# Tests — edge cases
# ---------------------------------------------------------------------------

test_empty_attestations_edge_case if {
	count(build_index_digest.deny) == 0 with input.attestations as []
}

test_unrelated_attestations_ignored if {
	count(build_index_digest.deny) == 0 with input.attestations as [_mock_unrelated]
}

# ---------------------------------------------------------------------------
# Mock attestation builders
# ---------------------------------------------------------------------------

_mock_v02(digest) := {"statement": {
	"predicateType": "https://slsa.dev/provenance/v0.2",
	"predicate": {
		"buildType": "tekton.dev/v1/PipelineRun",
		"buildConfig": {"tasks": [{"name": "build-binaries", "results": [{"name": "BUILD_INDEX_DIGEST", "value": digest}]}]},
	},
}}

_mock_v02_no_digest := {"statement": {
	"predicateType": "https://slsa.dev/provenance/v0.2",
	"predicate": {
		"buildType": "tekton.dev/v1/PipelineRun",
		"buildConfig": {"tasks": [{"name": "build-binaries", "results": []}]},
	},
}}

_mock_v1(digest) := {"statement": {
	"predicateType": "https://slsa.dev/provenance/v1",
	"predicate": {
		"buildDefinition": {
			"buildType": "https://tekton.dev/chains/v2/slsa-tekton",
		},
		"runDetails": {"byproducts": [{"name": "pipelineRunResults/BUILD_INDEX_DIGEST", "content": base64.encode(json.marshal(digest))}]},
	},
}}

_mock_v1_bare_name(digest) := {"statement": {
	"predicateType": "https://slsa.dev/provenance/v1",
	"predicate": {
		"buildDefinition": {
			"buildType": "https://tekton.dev/chains/v2/slsa-tekton",
		},
		"runDetails": {"byproducts": [{"name": "BUILD_INDEX_DIGEST", "content": base64.encode(json.marshal(digest))}]},
	},
}}

_mock_v1_no_digest := {"statement": {
	"predicateType": "https://slsa.dev/provenance/v1",
	"predicate": {
		"buildDefinition": {
			"buildType": "https://tekton.dev/chains/v2/slsa-tekton",
		},
		"runDetails": {"byproducts": []},
	},
}}

_mock_unrelated := {"statement": {
	"predicateType": "https://in-toto.io/attestation/link/v0.3",
	"predicate": {},
}}

_has_code(deny, code) if {
	some r in deny
	r.code == code
}
