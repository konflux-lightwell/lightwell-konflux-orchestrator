# Reconciling and resuming orchestration workflows
This proposal aims to improve the orchestrator's ability to recover the state from a previous CLI invocation and resume
the orchestration of the workflow for each artifact being handled.

The primary motivation is to allow the orchestrator to be called from within the scheduled job that runs in Balor
Fianna. That job will simply look at pending tickets and pass the build/release intent to the orchestrator. The
orchestrator should then be able to import new artifacts and resume work on those already triggered by previous calls.

## Main goals
- Resume the process of building/releasing an artifact across multiple invocations of the orchestrator

  Given the same input data for an orchestration command, the orchestrator should be able to query cluster resources
  and resume the operation where it left off. A cache mechanism is proposed to make subsequent orchestration attempts
  more efficient, but the orchestrator should be able to resume even if the cache is completely missing.

- Keep detailed historical data of all attempts to run the workflow

  Have a clear separation of phases and the data relevant to each. Separate the intent of releasing an artifact from
  the attempts to release it, keeping track of all pipelineRuns and other cluster resources created on each attempt.

- Identify failures and make smart decisions on whether to retry or fail an intent

  Add heuristics to separate retriable failures from those that cannot be retried. Keep track of error messages so that
  it's easier for callers to get information when failures occur.

## Secondary goals
- Provide a flexible database/caching backend, so we can pivot to other solutions if needed.

# New Workflow Model

Currently, the orchestrator keeps very limited data of an artifact and its build/release workflow. Every new attempt
overwrites the previous data. There is little information to act on failures, and even unrecoverable failures are
retried.

It is necessary to separate the intent of releasing an artifact (Request) from the actual attempts made to release it
(Attempt). Break the workflow into three distinct phases: intake (build), snapshot creation, and release, and keep
richer data about each phase.

## Models
**Request**: an intent to release a single artifact for a specific target (e.g., remediated, novel).

```python
class Request:
  id: int
  namespace: str
  application: str
  component: str
  pipeline: str
  release_plan: str
  artifact: str
  target: str
  created_at: date
```

**Attempt**: a single execution of the build/release workflow in Konflux.
A single request will have one or more attempts.

```python
class Attempt:
  id: int
  request_id: int
  created_at: date
```

**AttemptPhase**: One of the defined phases:

1. Intake (or build)

    Corresponds to the pipeline that starts the workflow, which is normally considered a build pipeline in Konflux (e.g.,
    pnc-import, python-sdist-ingest, python-remediated-build).

2. Snapshot

    Created by Konflux after the initial pipeline is finished. The snapshot is the unit that is actually released.

3. Release

    Pipeline that is run in the Releng managed namespace and is responsible for the release of the artifacts contained
    in the snapshot by means of a ReleasePlan.

```python
class AttemptPhase:
  id: int
  attempt_id: int
  name: str
  reference_type: str # e.g., pipelineRun, snapshot, release
  reference_id: str
  status: str
  error_message: str
  created_at: date
  updated_at: date
```

# Resume and reconcile

Multiple invocations of the orchestrator using the same input data should lead to the same behavior: all requests
should be carried from intake to a successful release. To achieve that, the orchestrator should be able to query existing
data on the cluster and update its internal state accordingly. This will also help mitigate failures that can happen
during the whole process.

The current database solution should be moved to a design perspective in which any data stored about the process works
as a cache: it has the goal of speeding up the querying of previous data and lowering the amount of queries made to the
cluster, but it should not be a prerequisite to running a cohesive orchestrator process among multiple calls.

## Fingerprinting

Fingerprinting key cluster resources (such as PipelineRuns and Releases) allows the orchestrator to reconcile its cache
in cases of failures or missing data.

A fingerprint for the request is generated from the following combination of parameters:

```python
@dataclass(frozen=True)
class Fingerprint:
    namespace: str
    application: str
    component: str
    pipeline: str
    release_plan: str
    target: str
    artifact: str
```

If a new request is made with a fingerprint that already exists in the cluster, the orchestrator will then try to
reconcile the cache and resume the process.

### Intake Pipelines

Adding the fingerprint as a label to the PipelineRun allows the orchestrator to find previously created objects and
reconcile an inconsistent cache.

```python
.metadata.labels["lightwell.io/request-fingerprint"] = fingerprint.hash()[:63] # K8s caps labels at 63 chars
```

Before a new intake pipeline is triggered, the orchestrator must perform a search for pipelineRuns that contain the
fingerprint as a label, and reconcile the cache state if needed. The search must be performed on the target
namespace and check for running, successful, or failed pipelines. In case none are found, a new query must be
made to Kubearchive, which will avoid missing important data that was already pruned.

### Snapshots
A Snapshot is created by Konflux's integration service when an intake pipeline is finished. For this reason, the
orchestrator can't immediately add a fingerprint to the Snapshot.

To retrieve data from potentially existing Snapshots, the lookup must be made from the intake PipelineRuns. The same
process described above to reconcile the intake phase must be used to gather data on Snapshots. The Snapshot created as
the result of a PipelineRun can be found by verifying the following label:

```
appstudio.openshift.io/snapshot: "my-app-jx7k2"
```

The status of the Snapshot can be identified by checking the content of additional annotations:

```
chains.tekton.dev/signed=true
test.appstudio.openshift.io/create-snapshot-status={"message":"Sucessfully created snapshot. See annotation appstudio.openshift.io/snapshot for name","status":"success"}
```

### Releases
Once the intake pipeline finishes, a snapshot is created and with it, the orchestrator can proceed with the creation of
the Release object. Adding the fingerprinting label to the Release object allows the reconciliation to occur. For that
to work, any ReleasePlanAdmission that has auto-releases enabled has to have that setting switched to off.

Before attempting to release, the orchestrator should query the cluster to find data about any previous releases and
trigger a new release only if there are no pending releases in execution. Retries should be only done in case of
recoverable errors.

# Interface

The new proposed commands are ecosystem-agnostic, and require the caller to provide the full set of parameters to
execute the workflow. While this achieves a reusable interface, it also places a burden on the caller. To address this
problem, the concept of recipes is introduced: simple yaml files that have the parameters configured for each of the
known ecosystems and targets.

## CLI commands

- import: imports a new request from a set of parameters (or a recipe)
- import-file: imports several requests by reading a text file
- run: orchestrates all the requests that were previously imported
- run-single: essentially works as an import+run of a single artifact
- reconcile: reconstruct the cache without applying any changes to the cluster

Examples:
```sh
$ orchestrator import \
  --namespace lightwell-poc-tenant \
  --pipeline import-pnc \
  --application java-remediated \
  --component java-remediated \
  --releasePlan java-remediated-release-prod \
  --artifact quay.io/light-castle/java-secure@sha256

$ orchestrator import \
  --recipe java-remediated \
  --artifact quay.io/light-castle/java-secure@sha256

$ orchestrator import-file refs.txt \
  --recipe java-remediated

$ orchestrator run \
  --max-parallel 10 \
  --max-retries 3
```

## Recipes
Holds a set of common configuration values for a target within an ecosystem. This is meant to reduce the number of
parameters needed to import new requests, since these values are typically the same across requests for a given
target.

java-remediated.yaml
```yaml
target: remediated
namespace: lightwell-poc-tenant
pipeline: import-pnc
application: java-remediated
component: java-remediated
releasePlan: java-remediated-release-prod
```

Note that any value can be overridden by a CLI parameter, as these take precedence.

## Backwards Compatibility

The current commands can still be kept as a compatibility layer: they will essentially invoke the new CLI commands
using a pre-established recipe.

# Concerns
1. Concurrency

    This design does not touch on concurrency problems. Two simultaneous processes of the orchestrator managing the same
    requests will result in problems. This is not an immediate issue since the Balor Fianna scheduler will not run
    multiple orchestration processes simultaneously, but nothing keeps a developer from running one locally.

2. Number of queries made to the cluster

    In order to move the data stored to a caching role, we create a higher dependency on the data available on the
    cluster. With the challenge of scaling the amount of builds in Lightwell, this might become an issue.

# Out of scope

1. Multi-cluster awareness

    I opted against adding a `cluster` value to the Request model because, unless we expect the orchestrator to be able
    to switch across multiple clusters within the same `run` invocation, that key would not add any benefit.

2. Error analysis and recovery

    Even though this is one of the main goals, the details on how to triage errors and determine the best course of
    action for each type will be covered in another design document.

# Implementation Proposal

1. Switch off auto-releases and allow the orchestrator to set a releasePlan
2. Implement the new Request/Attempt model
3. Implement the reconciliation logic for each of the phases
4. Make all the proposed interface changes
5. Create an interface to the persistence layer and move the current implementation to adhere to it
