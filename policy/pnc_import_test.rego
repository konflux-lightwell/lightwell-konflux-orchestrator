package pnc_import_test

import rego.v1

import data.pnc_import

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

_rebuild_source := "quay.io/light-castle/rebuild-pnc:lw-ABC@sha256:abc123"
_secure_source := "quay.io/light-castle/secure-pnc:lw-XYZ@sha256:xyz789"
_unknown_source := "quay.io/some-other-registry/image:tag@sha256:000000"

_allowed_rebuild := ["quay.io/light-castle/rebuild-pnc"]
_allowed_secure := ["quay.io/light-castle/secure-pnc"]

_pnc_fingerprint := "SHA256:LEMzGGdveXznXBtjkbiSPIwR2NxdsNbBL6m5FyW9dOI"
_other_fingerprint := "SHA256:HHTvfqOgdrdt9TXDyYDMYlwZ8r8rAsiNjiVlB"
_allowed_keys := [_pnc_fingerprint]

_allowed_validated := ["validated"]
_allowed_remediated := ["remediated"]
_allowed_both := ["validated", "remediated"]

_image_ref := "quay.io/redhat-user-workloads/lightwell-poc-tenant/pnc-import/pnc-import@sha256:abc123"

# ---------------------------------------------------------------------------
# Source registry — SLSA v0.2
# ---------------------------------------------------------------------------

test_source_registry_permitted_v02 if {
	count(pnc_import.deny) == 0 with input.attestations as [_mock_v02(_rebuild_source, _pnc_fingerprint)]
		with data.rule_data_custom.oci_verify_import_allowed_source_registries as _allowed_rebuild
		with data.rule_data_custom.oci_verify_import_allowed_signing_keys as _allowed_keys
		with data.rule_data_custom.oci_verify_import_allowed_distribution_targets as _allowed_validated
}

test_source_registry_denied_wrong_repo_v02 if {
	deny := pnc_import.deny with input.attestations as [_mock_v02(_secure_source, _pnc_fingerprint)]
		with data.rule_data_custom.oci_verify_import_allowed_source_registries as _allowed_rebuild
		with data.rule_data_custom.oci_verify_import_allowed_signing_keys as _allowed_keys
	some r in deny
	r.code == "pnc_import.source_registry_permitted"
}

test_source_registry_denied_unknown_repo_v02 if {
	deny := pnc_import.deny with input.attestations as [_mock_v02(_unknown_source, _pnc_fingerprint)]
		with data.rule_data_custom.oci_verify_import_allowed_source_registries as _allowed_rebuild
		with data.rule_data_custom.oci_verify_import_allowed_signing_keys as _allowed_keys
	some r in deny
	r.code == "pnc_import.source_registry_permitted"
}

test_remediated_source_permitted_in_remediated_ecp_v02 if {
	count(pnc_import.deny) == 0 with input.attestations as [_mock_v02(_secure_source, _pnc_fingerprint)]
		with data.rule_data_custom.oci_verify_import_allowed_source_registries as _allowed_secure
		with data.rule_data_custom.oci_verify_import_allowed_signing_keys as _allowed_keys
		with data.rule_data_custom.oci_verify_import_allowed_distribution_targets as _allowed_remediated
}

# ---------------------------------------------------------------------------
# Source registry — SLSA v1.0
# ---------------------------------------------------------------------------

test_source_registry_permitted_v1 if {
	count(pnc_import.deny) == 0 with input.attestations as [_mock_v1(_rebuild_source, _pnc_fingerprint)]
		with data.rule_data_custom.oci_verify_import_allowed_source_registries as _allowed_rebuild
		with data.rule_data_custom.oci_verify_import_allowed_signing_keys as _allowed_keys
		with data.rule_data_custom.oci_verify_import_allowed_distribution_targets as _allowed_validated
}

test_source_registry_denied_wrong_repo_v1 if {
	deny := pnc_import.deny with input.attestations as [_mock_v1(_secure_source, _pnc_fingerprint)]
		with data.rule_data_custom.oci_verify_import_allowed_source_registries as _allowed_rebuild
		with data.rule_data_custom.oci_verify_import_allowed_signing_keys as _allowed_keys
	some r in deny
	r.code == "pnc_import.source_registry_permitted"
}

# ---------------------------------------------------------------------------
# Signing key fingerprint — read from task results (v0.2)
# ---------------------------------------------------------------------------

test_signing_key_permitted_v02 if {
	count(pnc_import.deny) == 0 with input.attestations as [_mock_v02(_rebuild_source, _pnc_fingerprint)]
		with data.rule_data_custom.oci_verify_import_allowed_source_registries as _allowed_rebuild
		with data.rule_data_custom.oci_verify_import_allowed_signing_keys as _allowed_keys
		with data.rule_data_custom.oci_verify_import_allowed_distribution_targets as _allowed_validated
}

test_signing_key_denied_wrong_key_v02 if {
	deny := pnc_import.deny with input.attestations as [_mock_v02(_rebuild_source, _other_fingerprint)]
		with data.rule_data_custom.oci_verify_import_allowed_source_registries as _allowed_rebuild
		with data.rule_data_custom.oci_verify_import_allowed_signing_keys as _allowed_keys
	some r in deny
	r.code == "pnc_import.signing_key_permitted"
}

test_signing_key_denied_wrong_key_v1 if {
	deny := pnc_import.deny with input.attestations as [_mock_v1(_rebuild_source, _other_fingerprint)]
		with data.rule_data_custom.oci_verify_import_allowed_source_registries as _allowed_rebuild
		with data.rule_data_custom.oci_verify_import_allowed_signing_keys as _allowed_keys
	some r in deny
	r.code == "pnc_import.signing_key_permitted"
}

test_signing_key_result_absent_is_denied if {
	deny := pnc_import.deny with input.attestations as [_mock_v02_no_fingerprint(_rebuild_source)]
		with data.rule_data_custom.oci_verify_import_allowed_source_registries as _allowed_rebuild
		with data.rule_data_custom.oci_verify_import_allowed_signing_keys as _allowed_keys
	some r in deny
	r.code == "pnc_import.signing_key_result_present"
}

# ---------------------------------------------------------------------------
# ruleData validation — missing or empty produces an error
# ---------------------------------------------------------------------------

test_missing_source_registry_rule_data if {
	some r in pnc_import.deny
	r.code == "pnc_import.source_registry_rule_data_provided" with input.attestations as [_mock_v02(_rebuild_source, _pnc_fingerprint)]
		with data.rule_data_custom.oci_verify_import_allowed_source_registries as null
		with data.rule_data_custom.oci_verify_import_allowed_signing_keys as _allowed_keys
}

test_empty_source_registry_rule_data if {
	some r in pnc_import.deny
	r.code == "pnc_import.source_registry_rule_data_provided" with input.attestations as [_mock_v02(_rebuild_source, _pnc_fingerprint)]
		with data.rule_data_custom.oci_verify_import_allowed_source_registries as []
		with data.rule_data_custom.oci_verify_import_allowed_signing_keys as _allowed_keys
}

test_missing_signing_key_rule_data if {
	some r in pnc_import.deny
	r.code == "pnc_import.signing_key_rule_data_provided" with input.attestations as [_mock_v02(_rebuild_source, _pnc_fingerprint)]
		with data.rule_data_custom.oci_verify_import_allowed_source_registries as _allowed_rebuild
		with data.rule_data_custom.oci_verify_import_allowed_signing_keys as null
}

test_empty_signing_key_rule_data if {
	some r in pnc_import.deny
	r.code == "pnc_import.signing_key_rule_data_provided" with input.attestations as [_mock_v02(_rebuild_source, _pnc_fingerprint)]
		with data.rule_data_custom.oci_verify_import_allowed_source_registries as _allowed_rebuild
		with data.rule_data_custom.oci_verify_import_allowed_signing_keys as []
}

# ---------------------------------------------------------------------------
# Distribution target annotation
# ---------------------------------------------------------------------------

test_distribution_target_permitted if {
	codes := {r.code | some r in pnc_import.deny} with input.image.ref as _image_ref
		with input.attestations as []
		with data.rule_data_custom.oci_verify_import_allowed_source_registries as _allowed_rebuild
		with data.rule_data_custom.oci_verify_import_allowed_signing_keys as _allowed_keys
		with data.rule_data_custom.oci_verify_import_allowed_distribution_targets as _allowed_validated
		with ec.oci.image_manifest as _mock_manifest_validated
	not "pnc_import.distribution_target_permitted" in codes
}

test_distribution_target_denied_wrong_value if {
	deny := pnc_import.deny with input.image.ref as _image_ref
		with input.attestations as []
		with data.rule_data_custom.oci_verify_import_allowed_source_registries as _allowed_rebuild
		with data.rule_data_custom.oci_verify_import_allowed_signing_keys as _allowed_keys
		with data.rule_data_custom.oci_verify_import_allowed_distribution_targets as _allowed_validated
		with ec.oci.image_manifest as _mock_manifest_remediated
	some r in deny
	r.code == "pnc_import.distribution_target_permitted"
}

test_distribution_target_absent_is_denied if {
	deny := pnc_import.deny with input.image.ref as _image_ref
		with input.attestations as []
		with data.rule_data_custom.oci_verify_import_allowed_source_registries as _allowed_rebuild
		with data.rule_data_custom.oci_verify_import_allowed_signing_keys as _allowed_keys
		with data.rule_data_custom.oci_verify_import_allowed_distribution_targets as _allowed_validated
		with ec.oci.image_manifest as _mock_manifest_no_annotation
	some r in deny
	r.code == "pnc_import.distribution_target_annotation_present"
}

# ---------------------------------------------------------------------------
# Mock attestation builders
# ---------------------------------------------------------------------------

# v0.2: SOURCE_IMAGE in invocation.parameters; VERIFICATION_KEY_FINGERPRINT in task result
_mock_v02(source_image, key_fingerprint) := {"statement": {
	"predicateType": "https://slsa.dev/provenance/v0.2",
	"predicate": {
		"buildType": "tekton.dev/v1/PipelineRun",
		"invocation": {"parameters": {"SOURCE_IMAGE": source_image}},
		"buildConfig": {"tasks": [{"name": "verify-and-mirror", "results": [{"name": "VERIFICATION_KEY_FINGERPRINT", "value": key_fingerprint}]}]},
	},
}}

# v0.2 without VERIFICATION_KEY_FINGERPRINT result — simulates old task or missing step
_mock_v02_no_fingerprint(source_image) := {"statement": {
	"predicateType": "https://slsa.dev/provenance/v0.2",
	"predicate": {
		"buildType": "tekton.dev/v1/PipelineRun",
		"invocation": {"parameters": {"SOURCE_IMAGE": source_image}},
		"buildConfig": {"tasks": [{"name": "verify-and-mirror", "results": []}]},
	},
}}

# v1: SOURCE_IMAGE in runSpec params; VERIFICATION_KEY_FINGERPRINT in byproducts
_mock_v1(source_image, key_fingerprint) := {"statement": {
	"predicateType": "https://slsa.dev/provenance/v1",
	"predicate": {
		"buildDefinition": {
			"buildType": "https://tekton.dev/chains/v2/slsa-tekton",
			"externalParameters": {"runSpec": {"params": [{"name": "SOURCE_IMAGE", "value": source_image}]}},
		},
		"runDetails": {"byproducts": [{"name": "pipelineRunResults/VERIFICATION_KEY_FINGERPRINT", "content": base64.encode(json.marshal(key_fingerprint))}]},
	},
}}

# ---------------------------------------------------------------------------
# Mock OCI manifest builders
# ---------------------------------------------------------------------------

_mock_manifest_validated(_) := {"annotations": {"dev.lightwell.distribution-target": "validated"}}

_mock_manifest_remediated(_) := {"annotations": {"dev.lightwell.distribution-target": "remediated"}}

_mock_manifest_no_annotation(_) := {"annotations": {}}

# ---------------------------------------------------------------------------
# Content-class gate (novel vs backport)
#
# These assert on specific deny CODES (presence/absence), so they don't need to
# satisfy the unrelated source-registry / signing-key / distribution-target
# rules — those may fire with other codes and are ignored here.
# ---------------------------------------------------------------------------

_gav_artifact_type := "application/vnd.redhat.gav-index-build+json"

_referrer_ref := "quay.io/redhat-user-workloads/lightwell-poc-tenant/pnc-import/pnc-import@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

# ec.oci.* builtins are mocked with functions (matching the manifest mocks
# above). parsed_blob_from_image(ref) walks image_manifest(ref).layers[0].digest
# -> blob; we mock the manifest layer and the blob body (a JSON string).
_mock_gav_referrers(_) := [{"artifactType": _gav_artifact_type, "ref": _referrer_ref}]

_mock_no_referrers(_) := []

_mock_gav_manifest(_) := {"layers": [{"digest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}]}

_mock_blob_novel(_) := "{\"vulns\": [\"LW-2026-4259\"]}"

_mock_blob_backport(_) := "{\"vulns\": [\"CVE-2021-37533\"]}"

_mock_blob_mixed(_) := "{\"vulns\": [\"CVE-2025-48924\", \"LW-2026-0092\"]}"

_allowed_backport := ["backport"]

_allowed_novel := ["novel"]

_novel_prefixes := ["LW-"]

# novel build is rejected on the remediated stream
test_novel_denied_in_remediated_stream if {
	deny := pnc_import.deny with input.image.ref as _image_ref
		with ec.oci.image_referrers as _mock_gav_referrers
		with ec.oci.image_manifest as _mock_gav_manifest
		with ec.oci.blob as _mock_blob_novel
		with data.rule_data_custom.oci_verify_import_allowed_content_classes as _allowed_backport
		with data.rule_data_custom.oci_verify_import_novel_vuln_id_prefixes as _novel_prefixes
	some r in deny
	r.code == "pnc_import.content_class_permitted"
}

# backport (CVE-only) build passes the content-class check on remediated
test_backport_allowed_in_remediated_stream if {
	deny := pnc_import.deny with input.image.ref as _image_ref
		with ec.oci.image_referrers as _mock_gav_referrers
		with ec.oci.image_manifest as _mock_gav_manifest
		with ec.oci.blob as _mock_blob_backport
		with data.rule_data_custom.oci_verify_import_allowed_content_classes as _allowed_backport
		with data.rule_data_custom.oci_verify_import_novel_vuln_id_prefixes as _novel_prefixes
	not _has_code(deny, "pnc_import.content_class_permitted")
}

# novel build passes on the novel stream
test_novel_allowed_in_novel_stream if {
	deny := pnc_import.deny with input.image.ref as _image_ref
		with ec.oci.image_referrers as _mock_gav_referrers
		with ec.oci.image_manifest as _mock_gav_manifest
		with ec.oci.blob as _mock_blob_novel
		with data.rule_data_custom.oci_verify_import_allowed_content_classes as _allowed_novel
		with data.rule_data_custom.oci_verify_import_novel_vuln_id_prefixes as _novel_prefixes
	not _has_code(deny, "pnc_import.content_class_permitted")
}

# mixed build (CVE + LW) classifies as novel -> rejected on remediated
test_mixed_build_is_novel if {
	deny := pnc_import.deny with input.image.ref as _image_ref
		with ec.oci.image_referrers as _mock_gav_referrers
		with ec.oci.image_manifest as _mock_gav_manifest
		with ec.oci.blob as _mock_blob_mixed
		with data.rule_data_custom.oci_verify_import_allowed_content_classes as _allowed_backport
		with data.rule_data_custom.oci_verify_import_novel_vuln_id_prefixes as _novel_prefixes
	some r in deny
	r.code == "pnc_import.content_class_permitted"
}

# no gav-index referrer -> fail closed
test_missing_gav_index_denied if {
	deny := pnc_import.deny with input.image.ref as _image_ref
		with ec.oci.image_referrers as _mock_no_referrers
		with data.rule_data_custom.oci_verify_import_allowed_content_classes as _allowed_backport
		with data.rule_data_custom.oci_verify_import_novel_vuln_id_prefixes as _novel_prefixes
	some r in deny
	r.code == "pnc_import.gav_index_referrer_present"
}

_has_code(deny, code) if {
	some r in deny
	r.code == code
}
