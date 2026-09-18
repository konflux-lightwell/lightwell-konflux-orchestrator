# Versioned reconciliation contract for CLI and library consumers

**Status: proposal, not an implemented API.** This document defines a small shared
contract for integrations that start work, record progress, and resume after an
interruption without inferring state from log messages or SQLite internals.

Today, `run` waits for completion and emits an unversioned JSON object containing
resource names and import status. Its default database is ephemeral; an explicit
database may contain other items that affect the invocation's exit code.
[PR #45](https://github.com/konflux-lightwell/lightwell-konflux-orchestrator/pull/45)
proposes release-only reconciliation, adoption, and release-attempt history. This
contract builds on those concepts without depending on that draft's implementation.
It does not add a service, distributed scheduler, or general workflow framework.

## Identity and stable fingerprints

Keep logical intent, executions, and observed Kubernetes objects distinct:

| Field | Meaning |
| --- | --- |
| `operation_id` | Caller-supplied opaque ID, persisted before side effects and reused across invocations for one logical request. |
| `invocation_id` | Unique ID for each bounded CLI call or library call, including read-only reconciliation. |
| `attempt_id` | Persisted ID for one import or release attempt; unchanged when an uncertain create is retried or adopted. A deliberate replacement gets a new ID. |
| `resource` | Cluster identity, API group/version, kind, namespace, name, and UID when observed; names alone are not globally unique. |
| `intent_fingerprint` | Versioned SHA-256 hash of the normalized effective request, independent of process or database location. |
| `status_fingerprint` | Versioned SHA-256 hash of normalized semantic state, used to detect meaningful changes. |

The intent includes ecosystem, immutable source/build inputs, target cluster and
namespace, pipeline/template revision and effective parameters, requested terminal
stage, and selected release policy/ReleasePlan identity and revision. Resolve
mutable references where possible and retain both requested and resolved values;
a package version or tag alone is not proof of equivalent content. If required
inputs cannot be resolved, report `unknown` rather than claim safe reuse.
A release-attempt fingerprint also binds the exact Snapshot UID/component digests
and ReleasePlan. Persist resolved defaults so a restart cannot silently change them.

Before v1 ships, each fingerprint profile must specify its input fields,
normalization (including nulls and unordered collections), and canonical JSON
encoding. Include the profile version in the hash input and expose it alongside
the digest. Exclude timestamps, poll counters, resource versions, transient error
text, credentials, and log messages from semantic status hashes. Status hashes
include resource identities, outcomes, lineage, stable reason codes, and output
digests. Do not hash credentials into intent or expose them in records. The same
`operation_id` with a different intent fingerprint is a conflict, not an update;
intentional changed inputs or promotion to another plan require a new operation.
Fingerprints express equivalence, not authorization or proof of ownership.

## Outcomes and lineage

Every operation and attempt has an `outcome`, a stage (`import`, `snapshot`, or
`release`), and a structured reason with a stable code and human-readable message.

| Outcome | Interpretation |
| --- | --- |
| `pending` | Accepted but not started, known progressing, or waiting for a retry allowed by policy. Not terminal. |
| `unknown` | Available evidence cannot establish current state: unavailable API, ambiguous lookup/create, or missing lineage. Not terminal and not permission to retry a create. |
| `failed` | Confirmed terminal failure. An operation fails only when the requested goal cannot be reached under its retry policy. |
| `succeeded` | Positive evidence that the requested terminal stage completed successfully, with the required outputs recorded. |

A failed attempt can coexist with a pending operation if a replacement is allowed.
Deadline expiry or local cancellation is an invocation stop reason, not evidence
that remote work failed. Keep the last confirmed observation and its timestamp
when the current outcome is unknown. A valid running condition is pending; an API
lookup failure is unknown. Neither an empty lookup nor a process exit code proves
success or terminal failure.

Represent **PipelineRun → Snapshot → Release** as explicit resource references and
edges, not an assumed chain reconstructed from naming conventions. Record the
source/build identity, Snapshot component image digests, selected ReleasePlan, and
known output locations/digests. Each observation records its time and source (live
API or archive). A same-name object with a different UID is not the original.

Snapshot reuse or release-only operation may have no known import PipelineRun;
record that gap explicitly rather than fabricate one. Retain every failed/replaced
attempt and its lineage while identifying the active attempt. Multiple Releases
for a Snapshot must be distinguished by plan and attempt. Release retries reuse
the verified Snapshot and do not rerun the import. Success means the requested
goal is satisfied: import-only success does not imply release success.

## Events and complete bounded results

A reconciliation invocation has a caller-selected time/work budget, including
bounded remote requests. It observes existing work, performs permitted actions,
and returns even if remote work remains pending. These records are shared by both
interfaces:

| Record | Required content |
| --- | --- |
| Common envelope | `schema_version`, `record_type`, operation/invocation IDs, intent fingerprint and profile, observation timestamp. |
| `status` | Full current state of the operation on initial observation and reconciliation; stage, outcome, reasons, resource/attempt references, and status fingerprint/profile. |
| `transition` | Change from previous to current semantic state, including previous/current fingerprints, attempt ID when applicable, and resulting state. |
| `result` | Complete invocation result/checkpoint as described below; emitted once on a controlled exit. |

An event has an operation-scoped, persisted monotonic `sequence`; duplicates retain
the same sequence and are deduplicated by `(operation_id, sequence)`. Commit state
and its sequence before notification. Delivery is best-effort and can be repeated
or missed around crashes; this proposal does not require a durable event broker
or replay log. Consumers replace their view from a complete result or a later
reconciliation status, rather than requiring every transition. Polls with an
unchanged status fingerprint need not emit another transition.

The final `result` is a snapshot of **all known state**, not just changes since the
last callback. It contains the effective request, all correlation IDs, current
stage/outcome/reasons, invocation stop reason (`completed`, `budget_exhausted`,
`cancelled`, or `error`), timestamps, retry policy/counts, active and historical
attempts, full resource lineage and known outputs, unresolved create intents,
observation confidence, last event sequence, and a versioned resume checkpoint.
Absent resources/outputs are explicit nulls or empty collections; missing evidence
must not appear as success. It is complete even when no transition occurred.

The checkpoint includes the same persisted intent, resolved inputs, resource and
attempt identities, and pending-create markers needed to resume safely without
the event stream. It is serializable, validated on load, and bound to the operation,
intent fingerprint, and target. Do not embed credentials or require a private
SQLite row layout. Loading it requires fresh credentials and remote validation;
it is not authority to create resources or proof that observations remain current.
A supplied database and checkpoint that disagree must be reconciled or rejected,
never silently overwritten. Return the latest checkpoint in every controlled
pending, unknown, failed, or succeeded result. Abrupt termination may prevent a
final result; durable local state and cluster reconciliation cover that case.

## Restart and idempotent adoption

Before a remote create, persist the attempt and create intent. Attach correlation
metadata for the operation, attempt, and fingerprint to created objects, and use a
stable attempt-specific resource name where supported. These are lookup aids;
adoption must also validate target scope, spec/content, lineage, and release plan.
Existing resources without that metadata may be reused only after an unambiguous,
explicit equivalence check. Preserve original resource identity and record adoption
rather than implying that this invocation created it.

On restart, load and validate durable state/checkpoint, then reconcile known UIDs
and unresolved create intents before scheduling work. Prefer authoritative live
observations; use archive evidence for retained lineage/terminal observations when
appropriate, without treating archive absence as current cluster absence.
Adopt a matching progressing or successful object. If a create timed out, query
for the same attempt before retrying; an empty eventually consistent lookup is not
proof that creation failed. Retrying the exact same stable name can converge via
conflict-and-validate, but never generate a new name solely because visibility is
uncertain. Ambiguous matches or unverifiable absence remain unknown and may require
operator recovery. SQLite and the remote API do not form an atomic transaction;
this is convergence with guarded retries, not an exactly-once guarantee.

A confirmed failed attempt may get a new attempt ID under explicit retry policy.
Adoption, polling, and process restart do not consume a new attempt. Preserve
successful import/Snapshot lineage while retrying a Release. A completed operation
is not automatically rerun on the next invocation.

## CLI and library surfaces

An opt-in versioned CLI mode emits one UTF-8 JSON object per line (JSONL) on
**stdout**: status/transition records followed by one final result on controlled
exit. Flush each record; put all human progress, diagnostics, and dependency logs
on **stderr**. The final-result file option writes the same result atomically,
not the event stream. A truncated stream or missing result means the invocation
was interrupted, not that remote work failed. Consumers use the structured
outcome and stop reason; exit codes distinguish successful completion, confirmed
failure, incomplete reconciliation, and invocation/usage errors. Exact flags and
numeric codes should be agreed during implementation, without silently changing
existing `run`/`promote` output or exit-code behavior.

The library exposes the same semantics without printing or exiting the process.
An illustrative signature (not an available import) is:

```python
reconcile(
    request: ReconcileRequest,
    *,
    checkpoint: Checkpoint | None = None,
    on_event: Callable[[ReconcileEvent], None] | None = None,
) -> ReconcileResult
```

Use typed request, event, result, resource, and checkpoint models with explicit
serialization to the wire schema. The request includes the operation ID, budget,
target, inputs, goal, and retry policy; database/client configuration is supplied
explicitly, not through global CLI state. Callbacks run in sequence after durable
state updates and should return promptly. A callback exception stops further
scheduling and returns an invocation-error result with the last checkpoint; it
must not be classified as a failed remote attempt or cause automatic resubmission.
Validation errors before accepting an operation can raise typed exceptions.
Cancellation after acceptance should return a partial result when possible and
does not delete remote work. An async API is not required for the initial contract.

## Versioning, isolation, and delivery

Every wire record and checkpoint declares an integer `schema_version`, initially
`1`. Additive optional fields may remain in v1; consumers ignore unknown fields.
Required-field, outcome-enum, identity, or semantic changes require a new major
version. Reject unsupported major versions before side effects. Fingerprint
profiles are versioned separately; never compare different profiles as equivalent.
Schema versioning is independent of package and SQLite schema versions. Ship
published schema/serialization fixtures with implementation; no migration or new
runtime interface is introduced by this design-only change.

Until operation-scoped storage and locking exist, use **one logical operation per
persistent database, with one active orchestrator process**. A restart reuses that
database and operation ID; serialize invocations externally. Do not share default
ecosystem databases among independent integrations or assume SQLite locking
protects remote creation. Use a durable private database path, retain checkpoints,
and avoid `--reset` during resume. The current ephemeral `run` database is suitable
for disposable calls, not crash recovery; an explicit shared database may process
unrelated rows. Proposed resumable mode should reject conflicting database intent
rather than silently adopt it. Checkpoints and outputs may reveal source locations
and should be stored with the same access controls as operation state.

Implement incrementally: shared models/serialization and fingerprint fixtures,
then bounded reconciliation/checkpoints, then CLI JSONL and library callbacks over
the same engine. Acceptance tests should cover unchanged polls and complete final
results, import-only/release-only lineage, failure then release retry, API outage,
ambiguous creates and adoption after restart, conflicting operation IDs, callback
failure/cancellation, missing final output, schema compatibility, and clean JSONL
stdout. Full implementation and multi-process scheduling are outside this PR.
