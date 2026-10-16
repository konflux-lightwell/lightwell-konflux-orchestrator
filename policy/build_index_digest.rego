# METADATA
# title: Build Index Digest Policy
# description: >-
#   Validates that the released build recorded an attested build-index digest
#   in the SLSA provenance attestation, and that the digest is well-formed.
#
#   No ruleData is required for this policy; it performs a pure structural
#   attestation check against the PipelineRun SLSA attestation.
#
package build_index_digest

import rego.v1

import data.lib.metadata

# ---------------------------------------------------------------------------
# Build index digest check
# ---------------------------------------------------------------------------

# METADATA
# title: BUILD_INDEX_DIGEST result present
# description: >-
#   Confirm the pipeline emitted a BUILD_INDEX_DIGEST result. Absence means
#   the build did not attest the build-index digest, which is a policy violation.
# custom:
#   short_name: build_index_digest_result_present
#   failure_msg: 'BUILD_INDEX_DIGEST result not found in any pipeline task'
#   collections:
#     - lightwell
deny contains result if {
	count(_build_index_digests) == 0
	count(_pipelinerun_attestations) > 0
	result := metadata.result_helper(rego.metadata.chain(), [])
}

# METADATA
# title: BUILD_INDEX_DIGEST well-formed
# description: >-
#   Verify every BUILD_INDEX_DIGEST result matches the expected sha256 format.
# custom:
#   short_name: build_index_digest_well_formed
#   failure_msg: 'BUILD_INDEX_DIGEST %q is not a valid sha256 digest'
#   collections:
#     - lightwell
deny contains result if {
	some digest in _build_index_digests
	not _is_valid_digest(digest)
	result := metadata.result_helper(rego.metadata.chain(), [digest])
}

# ---------------------------------------------------------------------------
# Helpers — extract BUILD_INDEX_DIGEST from SLSA PipelineRun attestations
# ---------------------------------------------------------------------------

# SLSA v0.2: task result from buildConfig.tasks[*].results
_build_index_digests contains digest if {
	some att in _pipelinerun_attestations
	att.statement.predicateType == "https://slsa.dev/provenance/v0.2"
	some task in att.statement.predicate.buildConfig.tasks
	some r in task.results
	r.name == "BUILD_INDEX_DIGEST"
	digest := r.value
}

# SLSA v1.0: byproducts from runDetails
_build_index_digests contains digest if {
	some att in _pipelinerun_attestations
	att.statement.predicateType == "https://slsa.dev/provenance/v1"
	some bp in att.statement.predicate.runDetails.byproducts
	_name_matches(bp.name, "BUILD_INDEX_DIGEST")
	digest := json.unmarshal(base64.decode(bp.content))
}

# Filter to PipelineRun SLSA attestations only.
_pipelinerun_attestations contains att if {
	some att in input.attestations
	att.statement.predicateType in {
		"https://slsa.dev/provenance/v0.2",
		"https://slsa.dev/provenance/v1",
	}
}

_is_valid_digest(digest) if {
	is_string(digest)
	regex.match(`^sha256:[0-9a-f]{64}$`, digest)
}

# Byproduct names may be bare ("FOO") or prefixed ("pipelineRunResults/FOO").
_name_matches(name, target) if name == target

_name_matches(name, target) if endswith(name, concat("", ["/", target]))
