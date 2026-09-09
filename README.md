# import-orchestrator

CLI tool for orchestrating batch import PipelineRuns across ecosystems with intelligent state tracking, dynamic throttling, and automatic retry logic.

## Overview

This tool orchestrates the release of Lightwell artifacts through Konflux. Commands are grouped per ecosystem (e.g. `java`, `python`), each encapsulating the logic for triggering Konflux PipelineRuns with intelligent throttling and automatic retry on transient failures.

## Ecosystems

Commands are namespaced by ecosystem: `import-orchestrator <ecosystem> <command>`.

Two ecosystems are available today. Each uses its own default database (`--db` overrides it):

| Ecosystem | Default database | Description | Commands |
|-----------|------------------|-------------|----------|
| `java` | `./java_import_state.db` | PNC OCI image imports | `fetch`, `import-file`, `import-manifest`, `orchestrate`, `run`, `trigger` |
| `python` | `./python_import_state.db` | CVE-remediated Python wheel builds | `import-file`, `orchestrate`, `run`, `trigger` |

The `python` ecosystem identifies each build by a `package==version` reference (e.g. `ntplib==0.4.0`) instead of an OCI image, and runs the `python-remediated-build` pipeline. It has no `fetch` or `import-manifest` commands; populate its database with `import-file` (one `package==version` per line).

**Key Features:**
- **State persistence**: SQLite database tracks each OCI reference status (pending, triggered, running, success, failed)
- **Intelligent throttling**: Monitors actual running PipelineRun count instead of fixed time delays
- **Automatic retries**: Configurable retry logic for transient failures (default: 3 attempts)
- **Idempotent**: Can be stopped and resumed without losing progress
- **Completion tracking**: Runs until all imports succeed or exhaust retries

## Requirements

- Python 3.11+
- Cluster credentials via kubeconfig or `KONFLUX_TOKEN`
- Access to the KubeArchive API (for archived PipelineRun lookups)


## Installation

Install the package in editable mode:

```bash
pip install -e .
```

For development (includes pytest, ruff, etc.):

```bash
pip install -e ".[dev]"
```

## Usage

### Java Ecosystem

```bash
# Show help
import-orchestrator --help
import-orchestrator java --help
import-orchestrator java fetch --help
import-orchestrator java import-file --help
import-orchestrator java orchestrate --help
import-orchestrator java import-manifest --help
import-orchestrator java trigger --help
import-orchestrator java run --help

# Typical workflow: fetch then orchestrate
QUAY_TOKEN=<token> import-orchestrator java fetch
import-orchestrator java orchestrate --max-parallel 10

# Alternative: import from file then orchestrate
import-orchestrator java import-file refs.txt
import-orchestrator java orchestrate --max-parallel 10

# Alternative: import from a consolidated build manifest
import-orchestrator java import-manifest consolidated.yaml
import-orchestrator java orchestrate --max-parallel 10

# Trigger a single PNC import PipelineRun
import-orchestrator java trigger 'quay.io/light-castle/rebuild-pnc:tag@sha256:abc123...'

# Run a single reference end-to-end (trigger + monitor + release), then print
# a JSON result to stdout. Uses a throwaway database unless --db is given.
import-orchestrator java run 'quay.io/light-castle/rebuild-pnc:tag@sha256:abc123...'

# Same, but persist state to a database and also write the result to a file
import-orchestrator --db ./one-off.db java run \
  'quay.io/light-castle/rebuild-pnc:tag@sha256:abc123...' \
  --artifact-type REBUILD --output-json ./result.json

# Fetch only (populate database for inspection)
QUAY_TOKEN=<token> import-orchestrator java fetch

# Resume interrupted orchestration from existing database
import-orchestrator java orchestrate

# Fetch REMEDIATED builds instead of STAGE (default)
QUAY_TOKEN=<token> import-orchestrator java fetch --artifact-type REMEDIATED
import-orchestrator java orchestrate --max-parallel 5

# Reset database and start fresh
QUAY_TOKEN=<token> import-orchestrator --reset java fetch
import-orchestrator java orchestrate
```

### Python Ecosystem

The `python` ecosystem builds CVE-remediated wheels from `lightwell-builds` sources. Each item is a `package==version` reference:

```bash
# Show help
import-orchestrator python --help
import-orchestrator python import-file --help
import-orchestrator python orchestrate --help
import-orchestrator python trigger --help
import-orchestrator python run --help

# Import package references from a file, then orchestrate
import-orchestrator python import-file packages.txt
import-orchestrator python orchestrate --max-parallel 5

# Trigger a single build
import-orchestrator python trigger 'ntplib==0.4.0'

# Run a single package end-to-end (trigger + monitor + release), then print
# a JSON result to stdout. Uses a throwaway database unless --db is given.
import-orchestrator python run 'ntplib==0.4.0'

# Same, but persist state to a database and also write the result to a file
import-orchestrator --db ./one-off.db python run 'ntplib==0.4.0' \
  --output-json ./result.json
```

The `import-file` input lists one `package==version` per line; blank lines and lines starting with `#` are ignored:

```
# CVE-remediated Python packages
ntplib==0.4.0
requests==2.31.0
```

### Command-Line Options

#### Global Options

Global options are placed before the ecosystem: `import-orchestrator [--db PATH] [--reset] <ecosystem> <command>`.

| Option | Default | Description |
|--------|---------|-------------|
| `--db` | per-ecosystem (e.g. `./java_import_state.db`) | SQLite database path |
| `--reset` | `false` | Reset database (delete existing data before running) |

#### `fetch` Subcommand

Fetches OCI references from Quay and stores them in the database.

```bash
import-orchestrator java fetch [--artifact-type {STAGE,REBUILD,REMEDIATED,NOVEL}]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--artifact-type` | `STAGE` (or `LIGHTWELL_ARTIFACT_TYPE` env var) | Artifact type: STAGE, REBUILD, REMEDIATED, or NOVEL |

#### `import-file` Subcommand

Imports OCI references from a text file into the database.

```bash
import-orchestrator java import-file <file>
```

Reads OCI references from a text file (one per line) and adds them to the database as pending imports. Lines starting with `#` and blank lines are ignored.

**File format:**
```
# Comments are ignored
quay.io/namespace/repo:tag@sha256:abc123...
quay.io/namespace/repo:tag2@sha256:def456...

# Blank lines are also ignored
quay.io/namespace/repo:tag3@sha256:789abc...
```

#### `import-manifest` Subcommand

Imports OCI references from a consolidated build manifest (YAML) into the database.

```bash
import-orchestrator java import-manifest <file>
```

Each library entry's `output.artifact.tag` and `output.artifact.digest` are combined into a `tag@digest` reference when both are present, falling back to digest-only or tag-only. Entries missing both fields are skipped. A malformed digest (missing `@` separator) is treated as an error and aborts processing.

**Manifest format:**
```yaml
libraries:
  - output:
      artifact:
        tag: "quay.io/namespace/repo:build-100"
        digest: "quay.io/namespace/repo@sha256:abc123..."
  - output:
      artifact:
        digest: "quay.io/namespace/repo@sha256:def456..."
```

#### `orchestrate` Subcommand

Orchestrates the import process by triggering PipelineRuns and monitoring their status.

```bash
import-orchestrator java orchestrate [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--max-parallel` | `1` | Maximum parallel PipelineRuns |
| `--poll-interval` | `30` | Seconds between status checks |
| `--max-retries` | `3` | Max retry attempts for failed imports |
| `--artifact-type` | `STAGE` (or `LIGHTWELL_ARTIFACT_TYPE` env var) | Artifact type: STAGE, REBUILD, REMEDIATED, or NOVEL |

#### `trigger` Subcommand

Triggers a single PNC import PipelineRun for manual or ad-hoc imports.

```bash
import-orchestrator java trigger <source_image> [tag] [OPTIONS]
```

| Argument/Option | Description |
|-----------------|-------------|
| `source_image` | OCI image reference to import (must be digest-pinned with @sha256:) |
| `tag` | Optional destination tag override (default: derived from source image) |
| `--artifact-type` | Artifact type: STAGE (default), REBUILD, REMEDIATED, or NOVEL |
| `--dry-run` | Print the PipelineRun YAML without submitting it |

#### `run` Subcommand

Runs a **single** reference end-to-end and blocks until it finishes: ingest → trigger the
PipelineRun → monitor it to completion → monitor the release → emit a JSON result. It is the
one-shot counterpart to `import-file` + `orchestrate`, intended for CI jobs and ad-hoc runs that
handle exactly one artifact and want a machine-readable answer.

Unlike `trigger`, which submits a PipelineRun and returns immediately, `run` waits for the outcome.
It has no `--dry-run`.

```bash
# Java: one digest-pinned image
import-orchestrator java run <source_image> [tag] [OPTIONS]

# Python: one package==version reference
import-orchestrator python run <ref> [OPTIONS]
```

**Java arguments and options:**

| Argument/Option | Default | Description |
|-----------------|---------|-------------|
| `source_image` | — | OCI image reference to import (must be digest-pinned with `@sha256:`) |
| `tag` | derived from source image | Optional destination tag override |
| `--artifact-type` | `STAGE` (or `LIGHTWELL_ARTIFACT_TYPE` env var) | Artifact type: STAGE, REBUILD, REMEDIATED, or NOVEL |
| `--poll-interval` | `30` | Seconds between status checks |
| `--max-retries` | `3` | Max retry attempts on transient failure |
| `--output-json` | — | Also write the result JSON to this path |

**Python arguments and options:**

| Argument/Option | Default | Description |
|-----------------|---------|-------------|
| `ref` | — | Package reference as `package==version` (e.g. `ntplib==0.4.0`) |
| `--target` | `REMEDIATED` (or `LIGHTWELL_PYTHON_TARGET` env var) | Build target |
| `--builds-tag` / `--builds-ref` | `<package>/<version>` | `lightwell-builds` git revision to build from |
| `--poll-interval` | `30` | Seconds between status checks |
| `--max-retries` | `3` | Max retry attempts on transient failure |
| `--output-json` | — | Also write the result JSON to this path |

##### Database: ephemeral by default

`run` does **not** touch the ecosystem's shared database unless you ask it to. With no global
`--db`, it creates a throwaway SQLite database in a temporary directory and deletes it when the
command exits. That isolation is what keeps the run single-ref: the monitoring loop only ever sees
the one reference you passed, rather than picking up pending rows left behind by an earlier
`import-file` or `fetch`. Pass the global `--db` (before the ecosystem name) to persist state
instead — useful for resuming or for inspecting the row afterwards:

```bash
# Ephemeral — nothing left on disk
import-orchestrator java run 'quay.io/light-castle/rebuild-pnc:tag@sha256:abc...'

# Persistent — state kept in ./one-off.db
import-orchestrator --db ./one-off.db java run 'quay.io/light-castle/rebuild-pnc:tag@sha256:abc...'
```

> **Point `--db` at a fresh or single-purpose database.** `run` seeds your reference into the
> database and then runs the standard orchestration loop, which picks up **every** pending row it
> finds — it is not filtered to your reference. If you point `--db` at a shared database that
> already has pending work (from `fetch`, `import-file`, or an interrupted `orchestrate`), `run`
> will work through all of it, one at a time, and won't return until the whole database reaches a
> terminal state. The result JSON still describes only your reference, but the exit code reflects
> the database as a whole (see Exit codes below). The default ephemeral database avoids this
> entirely.

> **Note:** `--reset` deletes the database it resolves *before* `run` swaps in its ephemeral one.
> Running `import-orchestrator --reset java run ...` without an explicit `--db` therefore deletes
> the shared `java_import_state.db` and then runs against a throwaway database anyway. Don't
> combine `--reset` with `run` unless you also pass `--db`.

##### Result JSON

On completion `run` prints exactly one JSON object to **stdout** — all progress and diagnostic
output goes to stderr, so `import-orchestrator java run ... | jq` is safe. Pass `--output-json
PATH` to write the same payload to a file as well (stdout still gets it).

```json
{
  "ref": "quay.io/light-castle/rebuild-pnc:tag@sha256:abc123...",
  "status": "success",
  "pipelinerun_name": "pnc-import-abc123",
  "snapshot_name": "rebuild-pnc-xyz",
  "release_name": "rebuild-pnc-xyz-release",
  "error_message": null,
  "retry_count": 0
}
```

| Field | Description |
|-------|-------------|
| `ref` | The reference that was run |
| `status` | Final state — `success` or `failed` |
| `pipelinerun_name` | Name of the triggered PipelineRun (`null` if it never got that far) |
| `snapshot_name` | Konflux Snapshot produced by the build |
| `release_name` | Release created from the snapshot |
| `error_message` | Failure detail, `null` on success |
| `retry_count` | How many retries were consumed |

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `QUAY_TOKEN` | Yes (for `fetch`) | Authentication token for Quay.io API |
| `KONFLUX_TOKEN` or `KUBECONFIG` | Yes (for `orchestrate`, `trigger`, and `run`) | Cluster authentication |
| `LIGHTWELL_ARTIFACT_TYPE` | No | `STAGE` (default), `REBUILD`, `REMEDIATED`, or `NOVEL` |
| `LIGHTWELL_PYTHON_TARGET` | No | Build target for the `python` ecosystem: `REMEDIATED` (default) |
| `TEKTON_PIPELINE_DIR` | No | Path to directory containing Tekton pipeline definitions (defaults to `tekton/` in repository root) |


### Operation Flow

#### `fetch` subcommand

1. Uses the integrated `QuayClient` to query OCI references from Quay.io based on the selected artifact type
2. Stores references in SQLite with `status='pending'`
3. Reports newly added vs. already tracked references
4. Prints database statistics

**Exit codes:**
- `0` — Fetch successful (even if no new references found)
- `1` — API errors or authentication failures

#### `import-file` subcommand

1. Reads OCI references from the specified text file
2. Skips blank lines and comment lines (starting with `#`)
3. Adds each reference to the database with `status='pending'`
4. Skips duplicates (already in database)
5. Prints summary of how many were added

**Exit codes:**
- `0` — Import successful (even if all duplicates)
- `2` — File not found

#### `import-manifest` subcommand

1. Reads a consolidated build manifest (YAML)
2. For each library entry, combines `output.artifact.tag` and `output.artifact.digest` into a `tag@digest` reference
3. Falls back to digest-only or tag-only when one is missing; skips entries missing both
4. Raises an error if a digest field is malformed (missing `@` separator)
5. Adds each reference to the database with `status='pending'`
6. Skips duplicates (already in database)

**Exit codes:**
- `0` — Import successful (even if all duplicates)
- `2` — File not found

#### `orchestrate` subcommand

1. Checks if database has any OCI references (warns if empty but continues)
2. **Orchestration loop:**
   - Checks status of triggered/running imports via `KubeClient`
   - Updates database with current PipelineRun statuses
   - Triggers new imports up to `--max-parallel` limit using the ecosystem's PipelineRun builder
   - Sleeps for `--poll-interval` seconds
   - Repeats until all imports are complete (success or retry-exhausted)

**Exit codes:**
- `0` — All imports successful or no work to do
- `1` — Some imports failed after exhausting retries

#### `trigger` subcommand

1. Validates that the source image is digest-pinned
2. Loads the pipeline definition from `tekton/pipelines/pnc-import/`
3. Builds the PipelineRun manifest with source and destination image references
4. Submits the PipelineRun to Konflux via the K8s API

**Exit codes:**
- `0` — PipelineRun triggered successfully
- `1` — Validation or submission errors

#### `run` subcommand

1. Resolves the database: a temporary throwaway unless a global `--db` was given
2. Ingests the single reference into that database with `status='pending'`
3. Triggers its PipelineRun (with `--max-parallel` fixed at 1)
4. Polls the PipelineRun every `--poll-interval` seconds until it completes, retrying transient
   failures up to `--max-retries` times
5. Monitors the resulting Snapshot and Release to completion
6. Prints the result JSON to stdout (and to `--output-json PATH` if given)
7. Removes the temporary database, if one was created

**Exit codes:**
- `0` — No import in the database ended in `failed`
- `1` — At least one import ended in `failed` after exhausting retries
- `2` — CLI usage error (no ecosystem or no subcommand given)

With the default ephemeral database the database holds only your reference, so `0`/`1` mean exactly
"this reference succeeded/failed". With an explicit `--db` that contains other pending rows, the
exit code covers all of them — read `status` in the result JSON if you need the verdict for your
reference specifically. The result JSON is printed for both `0` and `1`.

### Database Inspection

The SQLite database can be queried directly for monitoring:

```bash
# View current state summary
sqlite3 java_import_state.db \
  "SELECT status, COUNT(*) FROM oci_references GROUP BY status"

# List all failed imports with errors
sqlite3 java_import_state.db \
  "SELECT oci_ref, error_message, retry_count FROM oci_references WHERE status='failed'"

# Show recent activity
sqlite3 java_import_state.db \
  "SELECT oci_ref, status, triggered_at, completed_at FROM oci_references ORDER BY id DESC LIMIT 10"

# Reset specific import to retry manually
sqlite3 java_import_state.db \
  "UPDATE oci_references SET status='pending', retry_count=0 WHERE oci_ref='quay.io/...'"
```

### Database Schema

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PRIMARY KEY | Auto-increment ID |
| `oci_ref` | TEXT UNIQUE | OCI reference (e.g., `quay.io/repo:tag@sha256:...`) |
| `status` | TEXT | `pending`, `triggered`, `running`, `success`, `failed` |
| `pipelinerun_name` | TEXT | Name of triggered PipelineRun |
| `triggered_at` | TIMESTAMP | When import was triggered |
| `completed_at` | TIMESTAMP | When import finished (success or failure) |
| `last_checked_at` | TIMESTAMP | Last status check time |
| `error_message` | TEXT | Error details for failed imports |
| `retry_count` | INTEGER | Number of retry attempts |
| `created_at` | TIMESTAMP | When reference was first added |

## Project Structure

```
import-orchestrator/
├── src/import_orchestrator/
│   ├── clients/           # Quay and Kubernetes API clients
│   ├── commands/          # CLI subcommand implementations
│   ├── ecosystems/        # Per-ecosystem config, parser, and PipelineRun builder (e.g. java/)
│   ├── engine/            # Core ecosystem-neutral logic
│   ├── database.py        # SQLite state persistence
│   ├── models.py          # Data models
│   └── constants.py       # Configuration constants
├── tekton/                # Tekton Pipeline definitions
├── policy/                # Conforma policy definitions
└── tests/                 # Pytest test suite
```

## Development

### Running Tests

```bash
pytest
```

Or via tox:

```bash
tox -e py311
```

### Code Standards

The project enforces the following standards, all configured with a **120-character line length** and targeting **Python 3.11**:

- **Ruff** -- Linter with rules: `E` (pycodestyle errors), `F` (pyflakes), `W` (pycodestyle warnings), `I` (import sorting)

### Linting and Formatting

```bash
ruff check src/ tests/
```

Or via tox:

```bash
tox -e ruff
```

To automatically format the code:
```bash
ruff format src/ tests/
```

### Running All Checks

```bash
tox
```

This runs all test and linting environments.

## License

Apache License 2.0
