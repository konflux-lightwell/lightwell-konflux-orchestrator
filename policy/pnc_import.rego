# METADATA
# title: PNC Import Trust Policy
# description: >-
#   Validates that the oci-verify-import task operated on an approved source
#   registry and used an approved signing key, and that the released image
#   carries a distribution-target annotation matching the release stream.
#
#   All ruleData keys are required. If a rule should not apply, remove this
#   policy bundle from the ECP entirely.
#
#   Required ruleData keys:
#
#     oci_verify_import_allowed_source_registries:
#       - quay.io/light-castle/rebuild-pnc   # validated ECP
#
#     oci_verify_import_allowed_signing_keys:
#       - SHA256:abcd...                      # PNC signing key fingerprint
#
#     oci_verify_import_allowed_distribution_targets:
#       - validated                           # or remediated; stage allows both
#
package pnc_import

import rego.v1

import data.lib.json as j
import data.lib.metadata
import data.lib.oci
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
# Distribution target annotation check
# ---------------------------------------------------------------------------

# METADATA
# title: Distribution target annotation present
# description: >-
#   Verify the dev.lightwell.distribution-target annotation is present on the
#   released image's OCI manifest.
# custom:
#   short_name: distribution_target_annotation_present
#   failure_msg: 'dev.lightwell.distribution-target annotation is absent from the image manifest'
#   collections:
#     - lightwell
deny contains result if {
	manifest := ec.oci.image_manifest(input.image.ref)
	not manifest.annotations["dev.lightwell.distribution-target"]
	result := metadata.result_helper(rego.metadata.chain(), [])
}

# METADATA
# title: Distribution target annotation permitted
# description: >-
#   Verify the dev.lightwell.distribution-target annotation on the released
#   image is in the set of allowed values configured for this release stream.
# custom:
#   short_name: distribution_target_permitted
#   failure_msg: >-
#     dev.lightwell.distribution-target annotation %q is not in the allowed set.
#     Allowed: %v
#   collections:
#     - lightwell
deny contains result if {
	annotation := _distribution_target_annotation
	not annotation in _allowed_distribution_targets
	result := metadata.result_helper(rego.metadata.chain(), [annotation, _allowed_distribution_targets])
}

# METADATA
# title: Distribution target ruleData provided
# description: >-
#   Confirm oci_verify_import_allowed_distribution_targets was provided in ruleData.
# custom:
#   short_name: distribution_target_rule_data_provided
#   failure_msg: '%s'
#   collections:
#     - lightwell
deny contains result if {
	some e in _distribution_target_rule_data_errors
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

# SLSA v1.0: byproducts from runDetails
_signing_key_fingerprints contains fp if {
	some att in _pipelinerun_attestations
	att.statement.predicateType == "https://slsa.dev/provenance/v1"
	some bp in att.statement.predicate.runDetails.byproducts
	_name_matches(bp.name, "VERIFICATION_KEY_FINGERPRINT")
	fp := json.unmarshal(base64.decode(bp.content))
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

_distribution_target_annotation := annotation if {
	manifest := ec.oci.image_manifest(input.image.ref)
	annotation := manifest.annotations["dev.lightwell.distribution-target"]
}

# Byproduct names may be bare ("FOO") or prefixed ("pipelineRunResults/FOO").
_name_matches(name, target) if name == target

_name_matches(name, target) if endswith(name, concat("", ["/", target]))

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

_allowed_distribution_targets := rule_data.get("oci_verify_import_allowed_distribution_targets")

_distribution_target_rule_data_errors contains error if {
	some e in j.validate_schema(
		rule_data.get("oci_verify_import_allowed_distribution_targets"),
		_string_list_schema,
	)
	error := {"message": e.message, "severity": e.severity}
}

# ---------------------------------------------------------------------------
# Vulnerability-class gate (per release stream)
#
# Implements the per-stream content policy (Andrew McNamara, 2026-08-26),
# derived from the GAV index attached to the released image as an OCI referrer:
#   predisclosure : must have >=1 LTWL (LW-) vuln; GAV present.
#   backport      : must have >=1 CVE vuln; GAV present; must have NO LTWL vuln.
#   validated     : GAV present; must have NO CVE and NO LTWL vuln.
#
# Opt-in per stream via the oci_verify_import_stream ruleData key
# (validated | backport | predisclosure). Streams that omit it are unaffected.
# Fail-closed: when a stream is configured, a missing GAV index referrer or a
# missing GAV is denied.
# ---------------------------------------------------------------------------

# METADATA
# title: GAV index referrer present
# description: >-
#   Verify a GAV index artifact is attached to the released image via the OCI
#   Referrers API. Absence means the content cannot be classified (fail-closed).
# custom:
#   short_name: gav_index_referrer_present
#   failure_msg: >-
#     no GAV index referrer (artifactType %q) is attached to the released image
#   collections:
#     - lightwell
deny contains result if {
	_stream_gate_enabled
	count(_gav_index_referrers) == 0
	result := metadata.result_helper(rego.metadata.chain(), [_gav_index_artifact_type])
}

# METADATA
# title: GAV present
# description: >-
#   Every release stream requires at least one GAV in the GAV index.
# custom:
#   short_name: gav_present
#   failure_msg: 'the GAV index contains no GAVs'
#   collections:
#     - lightwell
deny contains result if {
	_stream_gate_enabled
	count(_gav_index_referrers) > 0
	not _has_gav
	result := metadata.result_helper(rego.metadata.chain(), [])
}

# METADATA
# title: Predisclosure requires an LTWL vulnerability
# description: >-
#   The predisclosure (novel) stream must carry at least one LTWL (LW-) vuln id.
# custom:
#   short_name: predisclosure_requires_ltwl
#   failure_msg: 'predisclosure stream requires at least one LTWL (novel) vuln id, found none'
#   collections:
#     - lightwell
deny contains result if {
	_stream == "predisclosure"
	count(_gav_index_referrers) > 0
	not _has_ltwl
	result := metadata.result_helper(rego.metadata.chain(), [])
}

# METADATA
# title: Backport requires a CVE vulnerability
# description: >-
#   The backport (remediated) stream must carry at least one CVE vuln id.
# custom:
#   short_name: backport_requires_cve
#   failure_msg: 'backport stream requires at least one CVE vuln id, found none'
#   collections:
#     - lightwell
deny contains result if {
	_stream == "backport"
	count(_gav_index_referrers) > 0
	not _has_cve
	result := metadata.result_helper(rego.metadata.chain(), [])
}

# METADATA
# title: Backport excludes LTWL vulnerabilities
# description: >-
#   The backport (remediated) stream must not carry any LTWL (LW-) vuln id —
#   novel content must not reach the remediated repo.
# custom:
#   short_name: backport_excludes_ltwl
#   failure_msg: 'backport stream must not contain LTWL (novel) vuln ids: %v'
#   collections:
#     - lightwell
deny contains result if {
	_stream == "backport"
	_has_ltwl
	result := metadata.result_helper(rego.metadata.chain(), [_ltwl_vulns])
}

# METADATA
# title: Validated excludes CVE vulnerabilities
# description: >-
#   The validated (rebuild) stream is clean rebuilds and must carry no CVE vuln id.
# custom:
#   short_name: validated_excludes_cve
#   failure_msg: 'validated stream must not contain CVE vuln ids: %v'
#   collections:
#     - lightwell
deny contains result if {
	_stream == "validated"
	_has_cve
	result := metadata.result_helper(rego.metadata.chain(), [_cve_vulns])
}

# METADATA
# title: Validated excludes LTWL vulnerabilities
# description: >-
#   The validated (rebuild) stream must carry no LTWL (LW-) vuln id.
# custom:
#   short_name: validated_excludes_ltwl
#   failure_msg: 'validated stream must not contain LTWL vuln ids: %v'
#   collections:
#     - lightwell
deny contains result if {
	_stream == "validated"
	_has_ltwl
	result := metadata.result_helper(rego.metadata.chain(), [_ltwl_vulns])
}

# METADATA
# title: Stream ruleData valid
# description: >-
#   oci_verify_import_stream must be one of validated | backport | predisclosure.
# custom:
#   short_name: stream_rule_data_valid
#   failure_msg: 'oci_verify_import_stream %q is not one of validated, backport, predisclosure'
#   collections:
#     - lightwell
deny contains result if {
	_stream_gate_enabled
	not _stream in {"validated", "backport", "predisclosure"}
	result := metadata.result_helper(rego.metadata.chain(), [_stream])
}

# METADATA
# title: Vuln-id prefix ruleData provided
# description: >-
#   When a stream is configured, both oci_verify_import_novel_vuln_id_prefixes
#   and oci_verify_import_cve_vuln_id_prefixes must be provided.
# custom:
#   short_name: vuln_prefix_rule_data_provided
#   failure_msg: '%s'
#   collections:
#     - lightwell
deny contains result if {
	_stream_gate_enabled
	some e in _vuln_prefix_rule_data_errors
	result := metadata.result_helper_with_severity(rego.metadata.chain(), [e.message], e.severity)
}

# ---------------------------------------------------------------------------
# Helpers — classify the build from its GAV index referrer
# ---------------------------------------------------------------------------

# The gate is opt-in per stream: active only when oci_verify_import_stream is a
# string. rule_data.get returns [] for absent keys, so is_string distinguishes.
_stream := rule_data.get("oci_verify_import_stream")

_stream_gate_enabled if is_string(_stream)

# Artifact type oci-verify-import uses when attaching the GAV index to the
# mirrored image (see tekton/tasks/oci-verify-import/0.1/oci-verify-import.yaml).
_gav_index_artifact_type := "application/vnd.redhat.gav-index-build+json"

_gav_index_referrers contains r if {
	some r in ec.oci.image_referrers(input.image.ref)
	r.artifactType == _gav_index_artifact_type
}

_gav_index_vulns contains v if {
	some r in _gav_index_referrers
	doc := oci.parsed_blob_from_image(r.ref)
	some v in doc.vulns
}

_gav_index_gavs contains g if {
	some r in _gav_index_referrers
	doc := oci.parsed_blob_from_image(r.ref)
	some g in doc.gavs
}

_has_gav if count(_gav_index_gavs) > 0

_is_novel_vuln(v) if {
	some prefix in _novel_vuln_id_prefixes
	startswith(v, prefix)
}

_is_cve_vuln(v) if {
	some prefix in _cve_vuln_id_prefixes
	startswith(v, prefix)
}

_ltwl_vulns := {v | some v in _gav_index_vulns; _is_novel_vuln(v)}

_cve_vulns := {v | some v in _gav_index_vulns; _is_cve_vuln(v)}

_has_ltwl if count(_ltwl_vulns) > 0

_has_cve if count(_cve_vulns) > 0

_novel_vuln_id_prefixes := rule_data.get("oci_verify_import_novel_vuln_id_prefixes")

_cve_vuln_id_prefixes := rule_data.get("oci_verify_import_cve_vuln_id_prefixes")

_vuln_prefix_rule_data_errors contains error if {
	some e in j.validate_schema(
		rule_data.get("oci_verify_import_novel_vuln_id_prefixes"),
		_string_list_schema,
	)
	error := {"message": e.message, "severity": e.severity}
}

_vuln_prefix_rule_data_errors contains error if {
	some e in j.validate_schema(
		rule_data.get("oci_verify_import_cve_vuln_id_prefixes"),
		_string_list_schema,
	)
	error := {"message": e.message, "severity": e.severity}
}
