# METADATA
# title: PNC Import Trust Policy
# description: >-
#   Validates that the oci-verify-import task operated on an approved source
#   registry and used an approved signing key.
#
#   Both rules are required to be configured in ruleData. If a rule should not
#   apply, remove this policy bundle from the ECP entirely.
#
#   Required ruleData keys:
#
#     oci_verify_import_allowed_source_registries:
#       - quay.io/light-castle/rebuild-pnc   # validated ECP
#
#     oci_verify_import_allowed_signing_keys:
#       - SHA256:abcd...                      # PNC signing key fingerprint
#
package pnc_import

import rego.v1

import data.lib.json as j
import data.lib.metadata
import data.lib.rule_data

# ---------------------------------------------------------------------------
# Source registry check
# ---------------------------------------------------------------------------

# METADATA
# title: Source registry permitted
# description: >-
#   Verify the SOURCE_IMAGE parameter of the oci-verify-import task originates
#   from an allowed source registry.
# custom:
#   short_name: source_registry_permitted
#   failure_msg: >-
#     SOURCE_IMAGE %q does not start with any allowed source registry prefix.
#     Allowed: %v
#   collections:
#     - lightwell
deny contains result if {
	some source_image in _source_images
	not _source_image_permitted(source_image)
	result := metadata.result_helper(rego.metadata.chain(), [source_image, _allowed_source_registries])
}

# METADATA
# title: Source registry ruleData provided
# description: >-
#   Confirm oci_verify_import_allowed_source_registries was provided in ruleData.
# custom:
#   short_name: source_registry_rule_data_provided
#   failure_msg: '%s'
#   collections:
#     - lightwell
deny contains result if {
	some e in _source_registry_rule_data_errors
	result := metadata.result_helper_with_severity(rego.metadata.chain(), [e.message], e.severity)
}

# ---------------------------------------------------------------------------
# Signing key fingerprint check (input parameter)
# ---------------------------------------------------------------------------

# METADATA
# title: Signing key permitted
# description: >-
#   Verify the VERIFICATION_KEY_FINGERPRINT result of the oci-verify-import task
#   is in the approved set. The task computes this from the key it actually used.
# custom:
#   short_name: signing_key_permitted
#   failure_msg: >-
#     VERIFICATION_KEY_FINGERPRINT %q is not in the allowed set. Allowed: %v
#   collections:
#     - lightwell
deny contains result if {
	some fingerprint in _signing_key_fingerprints
	not fingerprint in _allowed_signing_keys
	result := metadata.result_helper(rego.metadata.chain(), [fingerprint, _allowed_signing_keys])
}

# METADATA
# title: Signing key result present
# description: >-
#   Confirm the oci-verify-import task emitted a VERIFICATION_KEY_FINGERPRINT result.
#   Absence means the task did not run the key check, which is a policy violation.
# custom:
#   short_name: signing_key_result_present
#   failure_msg: 'VERIFICATION_KEY_FINGERPRINT result not found in any oci-verify-import task'
#   collections:
#     - lightwell
deny contains result if {
	count(_signing_key_fingerprints) == 0
	count(_pipelinerun_attestations) > 0
	result := metadata.result_helper(rego.metadata.chain(), [])
}

# METADATA
# title: Signing key ruleData provided
# description: >-
#   Confirm oci_verify_import_allowed_signing_keys was provided in ruleData.
# custom:
#   short_name: signing_key_rule_data_provided
#   failure_msg: '%s'
#   collections:
#     - lightwell
deny contains result if {
	some e in _signing_key_rule_data_errors
	result := metadata.result_helper_with_severity(rego.metadata.chain(), [e.message], e.severity)
}

# ---------------------------------------------------------------------------
# Helpers — extract input parameters from SLSA PipelineRun attestations
# ---------------------------------------------------------------------------

# SLSA v0.2: predicate.invocation.parameters
_source_images contains img if {
	some att in _pipelinerun_attestations
	att.statement.predicateType == "https://slsa.dev/provenance/v0.2"
	img := att.statement.predicate.invocation.parameters.SOURCE_IMAGE
}

# SLSA v1.0: predicate.buildDefinition.externalParameters.runSpec.params
_source_images contains img if {
	some att in _pipelinerun_attestations
	att.statement.predicateType == "https://slsa.dev/provenance/v1"
	some p in att.statement.predicate.buildDefinition.externalParameters.runSpec.params
	p.name == "SOURCE_IMAGE"
	img := p.value
}

# SLSA v0.2: task result from buildConfig.tasks[*].results
_signing_key_fingerprints contains fp if {
	some att in _pipelinerun_attestations
	att.statement.predicateType == "https://slsa.dev/provenance/v0.2"
	some task in att.statement.predicate.buildConfig.tasks
	some r in task.results
	r.name == "VERIFICATION_KEY_FINGERPRINT"
	fp := r.value
}

# SLSA v1.0: byProducts from runDetails
_signing_key_fingerprints contains fp if {
	some att in _pipelinerun_attestations
	att.statement.predicateType == "https://slsa.dev/provenance/v1"
	some bp in att.statement.predicate.runDetails.byProducts
	bp.name == "VERIFICATION_KEY_FINGERPRINT"
	fp := bp.content
}

# Filter to PipelineRun SLSA attestations only.
_pipelinerun_attestations contains att if {
	some att in input.attestations
	att.statement.predicateType in {
		"https://slsa.dev/provenance/v0.2",
		"https://slsa.dev/provenance/v1",
	}
}

_source_image_permitted(source_image) if {
	some prefix in _allowed_source_registries
	startswith(source_image, prefix)
}

# ---------------------------------------------------------------------------
# ruleData accessors with schema validation
# ---------------------------------------------------------------------------

_allowed_source_registries := rule_data.get("oci_verify_import_allowed_source_registries")

_allowed_signing_keys := rule_data.get("oci_verify_import_allowed_signing_keys")

_string_list_schema := {
	"$schema": "http://json-schema.org/draft-07/schema#",
	"type": "array",
	"items": {"type": "string"},
	"uniqueItems": true,
	"minItems": 1,
}

_source_registry_rule_data_errors contains error if {
	some e in j.validate_schema(
		rule_data.get("oci_verify_import_allowed_source_registries"),
		_string_list_schema,
	)
	error := {"message": e.message, "severity": e.severity}
}

_signing_key_rule_data_errors contains error if {
	some e in j.validate_schema(
		rule_data.get("oci_verify_import_allowed_signing_keys"),
		_string_list_schema,
	)
	error := {"message": e.message, "severity": e.severity}
}
