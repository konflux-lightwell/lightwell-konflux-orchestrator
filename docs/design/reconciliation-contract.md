# Generic orchestration and reconciliation contract

**Status: target design proposal, not an implemented API.** This contract defines
what the generic library and CLI must provide to integration callers. Existing
commands and [PR #45](https://github.com/konflux-lightwell/lightwell-konflux-orchestrator/pull/45)
are implementation context, not the source of truth or constraints on this design;
they may need to change to conform. This proposal changes no runtime behavior.

## Ownership and invocation model

The caller supplies intent, persists the latest complete checkpoint and its
fingerprints, chooses when to invoke again, and performs its own finalization.
The orchestrator owns Konflux discovery, resource identity, execution, observation,
and **PipelineRun → Snapshot → Release** lineage. Callers must not reconstruct
that chain from names, parse logs, query private database tables, or implement a
second Konflux state machine.

Initially, one logical operation uses **one persistent database and one active
orchestrator process at a time**. Repeated scheduled invocations reuse that database
and `operation_id`; invocations are bounded and serialized, not a permanent service.
The store must reject another operation or conflicting intent and prevent concurrent
writers. Independent operations can later use independent processes/databases with
the same contract; shared storage, cross-process work distribution, and scheduler
design are out of scope. Local isolation does not prevent remote resource conflicts.

Each invocation chooses `execute` (observe and perform permitted actions toward the
goal) or `reconcile` (discover, adopt into local state, and observe; no remote
mutations). Both use the same engine, checkpoints, outcomes, and events. Switching
mode or observation budget does not change intent. A caller can stop after any
bounded invocation and resume from durable state, or restore a checkpoint into an
empty persistent store and validate it before acting.

In particular, a completed PipelineRun is **not** a completed release operation.
The orchestrator must discover its Snapshot, then discover/adopt or create the
appropriate Release, without re-importing. An operation may also start from an
explicit existing PipelineRun or Snapshot. If the requested Release is already
successful, return a completion result and outputs without creating anything. The
caller can then finalize; its finalization is not an orchestrator stage.

## Identity and fingerprints

| Field | Contract |
| --- | --- |
| `operation_id` | Caller-supplied opaque identity of one logical intent, stable across invocations and restarts. |
| `attempt_id` | Persisted identity of a stage execution; stable during polling, adoption, or uncertain-create recovery. A deliberate replacement gets a new ID. |
| `invocation_id` | New identity for every bounded call, including observation-only calls. |
| `ResourceIdentity` | Stable cluster ID, API group, kind, namespace, name, and UID. API version is observation metadata, not a different identity. |
| `intent_fingerprint` | Versioned SHA-256 of normalized effective intent, independent of database path and invocation. |
| `state_fingerprint` | Versioned SHA-256 of stage plus immutable resource identity, or a persisted pre-resource identifier. Stable while the same stage/resource remains current. |
| `status_fingerprint` | Versioned SHA-256 of semantic status, including state fingerprint, outcome, stable reason codes, attempts, lineage, and output digests. |

A cluster ID must survive kubeconfig/context renaming and distinguish clusters;
a URL or context alias alone is not sufficient. A same-name object with a new UID
is a different resource. Before a resource is observed, use the tagged identifier
`{operation_id, attempt_id}` (`attempt_id: null` before allocating an attempt),
never a guessed name or fabricated UID. Namespace is null for cluster-scoped
resources; UID is required for observed resource identity. Binding a verified
resource changes the state fingerprint. Waiting for a Snapshot can therefore have stage `snapshot`
with a pre-resource identifier and a completed PipelineRun in lineage.

Intent includes the target scope, ecosystem, immutable source/build inputs,
pipeline/template revision and effective parameters, requested goal, selected
ReleasePlan identity and revision, and retry/adoption policy. Persist requested
and resolved values and defaults. Tags or package versions alone do not establish
content equivalence. Release creation additionally binds the verified Snapshot UID
and component digests in its durable create intent. Missing required resolution
prevents side effects: return `unknown` for unavailable evidence or `blocked` for
a known unsatisfied prerequisite. Never silently change intent on restart. Reusing
an operation ID with different intent is a conflict; changed goals/inputs/plans
require a new operation, which can explicitly adopt verified prior resources.

Each fingerprint is `{profile, digest}`; profiles specify exact input fields and
normalization and ship canonical test vectors. Use canonical JSON (RFC 8785),
include the profile in hash input, distinguish null from empty values, and sort
set-like collections by stable identity while preserving ordered parameters.
Exclude credentials from all records and hashes; exclude observation timestamps,
poll counts, resource versions, free-text messages, and logs from state/status
hashes. Profiles are versioned independently; do not compare unlike profiles as
equivalent. Fingerprints are neither authorization nor ownership proof.

For example, with a fixed intent and Release UID:

| Observation | State fingerprint | Status fingerprint | Notification |
| --- | --- | --- | --- |
| Release progressing | `state-v1:A` | `status-v1:B` | Transition with complete checkpoint |
| Same Release, progress message refreshed | `state-v1:A` | `status-v1:B` | Status update allowed, no transition required |
| Same Release succeeds | `state-v1:A` | `status-v1:C` | Transition and completion result |
| Replacement Release has new UID | `state-v1:D` | `status-v1:E` | Transition; retain prior attempt/lineage |

Letters above abbreviate digests. A state fingerprint alone is not a resume token:
callers persist the complete checkpoint even when that fingerprint is unchanged.

## Outcomes and evidence

Stages are `pipelinerun`, `snapshot`, and `release`. Each operation and attempt
has a separate outcome and structured reasons (`code`, human-readable `message`).

| Outcome | Meaning |
| --- | --- |
| `pending` | Known progressing, accepted but not started, or waiting for an allowed retry. |
| `succeeded` | Positive evidence that the requested goal completed with required outputs. |
| `failed` | Confirmed terminal failure; the operation cannot reach its goal under its retry policy. |
| `blocked` | Known prerequisite, policy, or configuration prevents progress and requires intervention; not a failed remote execution. |
| `unknown` | Insufficient or conflicting evidence, such as API outage, ambiguous create/matches, or unverifiable lineage. Not permission to create again. |

A failed attempt can coexist with a pending operation. An import-only goal can
succeed at `pipelinerun`; a release goal cannot succeed there. Budget expiry and
cancellation describe the invocation, not remote failure. On unknown observations,
retain the last confirmed state, observation time, and evidence source separately;
do not present stale evidence as fresh success. Known running conditions are
pending, whereas an API lookup error is unknown.

Record typed resource references and verified edges, including source/build
identity, Snapshot component digests, ReleasePlan identity/revision, and output
locations/digests. Evidence comes from structured API fields, owner references,
validated correlation metadata and content, or authoritative archive records,
never log parsing or naming conventions. Archive absence does not prove live
absence. Explicit Snapshot entry may have unavailable upstream PipelineRun
lineage; record that gap and enforce the requested adoption policy, rather than
fabricating an edge. Retain all failed/replaced attempts and their lineage. Distinguish
multiple Releases by exact Snapshot, plan, and attempt, not newest name or timestamp.

## Typed request, checkpoint, and result

The following is the required v1 model shape, not an available import. Named nested
types must have published wire schemas and serialization fixtures at implementation.
`?` means an explicit nullable field, not a silently omitted required field.

```text
ResourceIdentity = {cluster_id, api_group, kind, namespace, name, uid}
Fingerprint = {profile, digest}
OperationIntent = {operation_id, target, inputs, entry_resource?, goal,
                   pipeline_revision?, release_plan?, retry_policy, adoption_policy}
OrchestrationRequest = {schema_version: 1, intent: OperationIntent,
                        mode: execute|reconcile, budget: {max_seconds, max_actions}}
State = {stage, outcome, reasons[], active_attempt_id?, current_resource?,
         state_identifier, state_fingerprint, status_fingerprint}
Checkpoint = {schema_version: 1, operation_id, intent: OperationIntent,
              requested_inputs, resolved_inputs, intent_fingerprint,
              state: State, attempts[], resources[], lineage[], outputs[],
              unresolved_creates[], observations[], last_confirmed_state?,
              retry_counts, revision, last_event_sequence, created_at, updated_at}
OrchestrationEvent = {schema_version: 1, record_type: status|transition,
                      operation_id, invocation_id, sequence, observed_at,
                      previous_state_fingerprint?, previous_status_fingerprint?,
                      checkpoint: Checkpoint}
OrchestrationResult = {schema_version: 1, record_type: result,
                       operation_id, invocation_id, observed_at,
                       stop_reason: completed|observed|budget_exhausted|
                                    blocked|cancelled|error,
                       invocation_error?, completion?, checkpoint: Checkpoint}
Completion = {goal, terminal_resource: ResourceIdentity, outputs[]}
```

For example, a `current_resource` value is:

```json
{
  "cluster_id": "cluster-7e8149",
  "api_group": "tekton.dev",
  "kind": "PipelineRun",
  "namespace": "build-tenant",
  "name": "import-attempt-001",
  "uid": "84d8a5b0-1c07-4a32-a2ab-a676394375d4"
}
```

Attempts include their ID, stage, outcome/reasons, retry predecessor, resources,
creation/adoption provenance, and timestamps. Unresolved creates include the attempt,
exact target/name, normalized desired spec and its digest, lineage bindings, and
submission/observation evidence. Observations include resource, API version, source,
time, confidence, and structured conditions. Lineage edges identify both endpoints
and supporting evidence; explicit gaps carry reasons. Output records include type,
location, and immutable digest when applicable. Sensitive inputs/checkpoints need
operation-state access controls, and credentials are supplied separately.

Persist accepted operation intent **before any remote side effect**, then persist
resolved intent and each attempt/create intent before submission. A checkpoint is
a complete portable snapshot of all known state, including histories and uncertain
creates, not a delta or an opaque database row. Absent resources/outputs use null or
empty collections. Loading validates schema, operation, intent, target, lineage,
and fingerprints, then revalidates remote evidence using fresh credentials.
Database/checkpoint disagreements must be explicitly reconciled by revision and
evidence or rejected; never blindly overwrite newer state. A fingerprint or
checkpoint does not itself authorize mutation.

Every controlled bounded invocation after acceptance returns the complete latest
checkpoint for **all five outcomes**, even when no transition occurred or an error
stopped work. `completion` is present only for verified goal success; a pending
result is complete as a record, not completed work. Invalid requests/checkpoints
rejected before acceptance have typed validation errors and no side effects.
Abrupt process termination can prevent the final record; durable local state and
safe remote reconciliation cover that case.

## Discovery, restart, and safe adoption

1. Validate and durably accept intent; load the latest checkpoint/local state.
   Reconcile known UIDs and unresolved creates before any new submission.
2. Discover the appropriate resources and lineage within the target scope. Attach
   operation/attempt/intent correlation metadata to created objects and use stable
   attempt-specific names where supported. Metadata and names are lookup aids,
   not proof: validate scope, spec/content, UID, lineage, and ReleasePlan.
3. Adopt one verified progressing **or successful** match, including an explicitly
   supplied resource lacking correlation metadata when policy permits equivalence.
   Record adoption, preserve original identity, and do not consume a retry.
   Multiple plausible matches remain `unknown` with candidates/evidence in the
   checkpoint until safely disambiguated; never pick the first/latest or create
   another resource to resolve ambiguity.
4. On a timed-out create, retain the original attempt/create intent and query for
   that attempt. An empty eventually consistent lookup is not proof of failure.
   Retry only when safe: the same exact stable name/spec can converge through
   conflict-and-validate. If safety cannot be established, return `unknown` rather
   than generate a fresh name. There is no atomic database/API transaction or
   exactly-once creation promise.
5. In execute mode, advance from completed PipelineRun to Snapshot to Release.
   Never rerun successful upstream work merely because a downstream resource is
   not yet visible. A confirmed failed attempt may get a new attempt under explicit
   retry policy; release retries retain the verified Snapshot and upstream lineage.
   Reconcile mode reports the same state without performing remote actions.
6. Return verified completion if the goal is already met. Repeated invocations
   must not create additional resources or repeat caller-owned finalization.

## Library and CLI parity

An illustrative library entry point is:

```python
orchestrate(
    request: OrchestrationRequest,
    *,
    store: OperationStore,
    clients: KonfluxClients,
    checkpoint: Checkpoint | None = None,
    on_event: Callable[[OrchestrationEvent], None] | None = None,
) -> OrchestrationResult
```

The library has typed requests/results/checkpoints and callback events, with no
printing, process exits, or global CLI configuration. An iterator adapter may
expose the same events and final result. The engine bounds remote calls and work
by the requested budget, reserving time to persist and return. Callbacks are
ordered, run after commit, and must return promptly; a callback exception stops
further actions and returns an invocation-error result with the last checkpoint,
not a failed remote attempt. Cancellation after acceptance returns a checkpoint
when controlled and never implicitly deletes remote work.

Emit an initial `status` on each invocation, a `transition` for every observed
meaningful change (state **or status** fingerprint), and optional status refreshes
even when both fingerprints are unchanged. Each event includes the resulting
complete checkpoint. Persist state, checkpoint revision, and operation-scoped
monotonic event sequence before notification. Duplicate deliveries retain sequence
and are deduplicated by `(operation_id, sequence)`. Delivery can be repeated or
missed across crashes; there is no required event broker/replay log. A final result
or later reconciliation status fully repairs a caller's view without event replay.

The CLI is an adapter over the same API. Machine mode writes one UTF-8 JSON object
per line to **stdout**, flushing every event, followed by exactly one final result
on a controlled accepted invocation. Human progress, diagnostics, and dependency
logs go to **stderr**. An optional result file is an atomic write of the same final
result, not the stream. Missing/truncated final output indicates interrupted
observation, not failed remote work. Exact command/flag spelling is not prescribed.

Proposed machine-mode exit codes separate domain outcomes from invocation errors:

| Code | Meaning |
| --- | --- |
| `0` | Successfully completed invocation, including `pending`, `unknown`, or `blocked`; inspect checkpoint outcome and `completion` for goal success. |
| `1` | Confirmed terminal operation failure (`failed`). |
| `2` | Invalid request or invocation error; not evidence of remote failure. |

Budget exhaustion during healthy bounded execution is code `0`. Controlled
cancellation is an invocation error (code `2`), with its explicit stop reason and
checkpoint; abrupt signal termination may prevent output. Invocation errors take
precedence over domain codes, and never overwrite the observed domain outcome.
Numeric shell status alone is deliberately not a completion signal.

A caller loop is conceptually: invoke with persisted intent/checkpoint; atomically
replace its checkpoint on each event and final result; finalize only when
`completion` is present; otherwise decide when or whether to invoke again. The
orchestrator does not prescribe that scheduling or the caller's finalization logic.

## Versioning and acceptance requirements

Every request, event, result, and checkpoint declares integer `schema_version`,
initially `1`. Additive optional fields may remain in v1; consumers ignore unknown
fields. Required-field, enum, identity, or semantic changes require a new major
version. Reject unsupported versions before side effects. Wire, fingerprint,
package, and database schema versions are independent.

Implementation acceptance requires shared library/CLI conformance tests proving:

- Repeated bounded invocations and checkpoint-only restore continue a completed
  PipelineRun through Snapshot/Release without importing again; an already-complete
  Release yields completion for caller finalization and creates nothing.
- PipelineRun/Snapshot entry, adoption without metadata, missing lineage, multiple
  matches, changed UIDs, and ReleasePlan mismatches enforce identity/evidence rules.
- All five outcomes remain distinct; API outages, uncertain creates, missing
  Snapshot visibility, retry exhaustion, blocked prerequisites, and cancellation
  never produce false completion or uncontrolled duplicate resources.
- Crash points before/after intent persistence, submission, commit, and notification
  converge safely; downstream retries retain successful upstream lineage.
- Every controlled accepted call, including unchanged polls and callback errors,
  supplies a complete serializable checkpoint; events cover state/status changes
  and permit unchanged-fingerprint status updates. Golden vectors verify hashes.
- Conflicting intent/database/checkpoints and unsupported schemas are rejected
  safely; one-operation isolation and writer exclusion hold without a scheduler.
- Library calls never print/exit; CLI JSONL is flushed, uncontaminated, and ends in
  the same complete result, with tested exit codes and interrupted-stream handling.

These are requirements for subsequent implementation, including changes to existing
code or PR #45 as needed, not claims that current behavior already satisfies them.
