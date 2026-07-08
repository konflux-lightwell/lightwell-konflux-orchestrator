# import-orchestrator

CLI tool for orchestrating batch PNC import PipelineRuns with intelligent state tracking, dynamic throttling, and automatic retry logic.

## Overview

This tool provides batch orchestration for importing PNC (Project Newcastle) builds into Konflux. It manages multiple concurrent imports with SQLite-based state persistence, monitors actual PipelineRun counts for intelligent throttling, and automatically retries transient failures.

**Key Features:**
- **State persistence**: SQLite database tracks each OCI reference status (pending, triggered, running, success, failed)
- **Intelligent throttling**: Monitors actual running PipelineRun count instead of fixed time delays
- **Automatic retries**: Configurable retry logic for transient failures (default: 3 attempts)
- **Idempotent**: Can be stopped and resumed without losing progress
- **Completion tracking**: Runs until all imports succeed or exhaust retries

## Requirements

- Python 3.11+
- `kubectl` authenticated to the target cluster (kubeconfig or `KONFLUX_TOKEN`)
- Companion scripts (from `build-definitions` submodule):
  - `build-definitions/docs/examples/fetch_pnc_oci_references.sh` — fetches OCI references from Quay
  - `build-definitions/docs/examples/trigger-pnc-import.sh` — triggers individual PipelineRuns

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

### Basic Commands

```bash
# Show help
import-orchestrator --help
import-orchestrator fetch --help
import-orchestrator import-file --help
import-orchestrator orchestrate --help

# Typical workflow: fetch then orchestrate
QUAY_TOKEN=<token> import-orchestrator fetch
import-orchestrator orchestrate --max-parallel 10

# Alternative: import from file then orchestrate
import-orchestrator import-file refs.txt
import-orchestrator orchestrate --max-parallel 10

# Fetch only (populate database for inspection)
QUAY_TOKEN=<token> import-orchestrator fetch

# Resume interrupted orchestration from existing database
import-orchestrator orchestrate

# Import remediated builds instead of rebuilds
QUAY_TOKEN=<token> LIGHTWELL_ARTIFACT_TYPE=REMEDIATED import-orchestrator fetch
import-orchestrator orchestrate --max-parallel 5

# Reset database and start fresh
QUAY_TOKEN=<token> import-orchestrator --reset fetch
import-orchestrator orchestrate
```

### Command-Line Options

#### Global Options

| Option | Default | Description |
|--------|---------|-------------|
| `--db` | `./pnc_import_state.db` | SQLite database path |
| `--reset` | `false` | Reset database (delete existing data before running) |

#### `fetch` Subcommand

Fetches OCI references from Quay and stores them in the database.

```bash
import-orchestrator fetch [--fetch-script SCRIPT]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--fetch-script` | `build-definitions/docs/examples/fetch_pnc_oci_references.sh` | Path to fetch script |

#### `import-file` Subcommand

Imports OCI references from a text file into the database.

```bash
import-orchestrator import-file <file>
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

#### `orchestrate` Subcommand

Orchestrates the import process by triggering PipelineRuns and monitoring their status.

```bash
import-orchestrator orchestrate [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--trigger-script` | `build-definitions/docs/examples/trigger-pnc-import.sh` | Path to trigger script |
| `--max-parallel` | `1` | Maximum parallel PipelineRuns |
| `--poll-interval` | `30` | Seconds between status checks |
| `--max-retries` | `3` | Max retry attempts for failed imports |

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `QUAY_TOKEN` | Yes (for `fetch`) | Authentication token for Quay.io API |
| `KONFLUX_TOKEN` or `KUBECONFIG` | Yes (for `orchestrate`) | Kubectl authentication |
| `LIGHTWELL_ARTIFACT_TYPE` | No | `REBUILD` (default) or `REMEDIATED` |
| `TEKTON_PIPELINE_DIR` | No | Path to directory containing Tekton pipeline definitions (defaults to `tekton/` in repository root) |
| `TASK_BUNDLE_PULLSPEC` | No | Override for oci-verify-import task bundle (defaults to resolving floating tag via skopeo) |

### Operation Flow

#### `fetch` subcommand

1. Runs `fetch_pnc_oci_references.sh` to get OCI references from Quay
2. Stores references in SQLite with `status='pending'`
3. Reports newly added vs. already tracked references
4. Prints database statistics

**Exit codes:**
- `0` — Fetch successful (even if no new references found)
- `2` — Fetch script not found

#### `import-file` subcommand

1. Reads OCI references from the specified text file
2. Skips blank lines and comment lines (starting with `#`)
3. Adds each reference to the database with `status='pending'`
4. Skips duplicates (already in database)
5. Prints summary of how many were added

**Exit codes:**
- `0` — Import successful (even if all duplicates)
- `2` — File not found

#### `orchestrate` subcommand

1. Checks if database has any OCI references (warns if empty but continues)
2. **Orchestration loop:**
   - Checks status of triggered/running imports via kubectl
   - Updates database with current PipelineRun statuses
   - Triggers new imports up to `--max-parallel` limit
   - Sleeps for `--poll-interval` seconds
   - Repeats until all imports are complete (success or retry-exhausted)

**Exit codes:**
- `0` — All imports successful or no work to do
- `1` — Some imports failed after exhausting retries
- `2` — Trigger script not found

### Database Inspection

The SQLite database can be queried directly for monitoring:

```bash
# View current state summary
sqlite3 pnc_import_state.db \
  "SELECT status, COUNT(*) FROM oci_references GROUP BY status"

# List all failed imports with errors
sqlite3 pnc_import_state.db \
  "SELECT oci_ref, error_message, retry_count FROM oci_references WHERE status='failed'"

# Show recent activity
sqlite3 pnc_import_state.db \
  "SELECT oci_ref, status, triggered_at, completed_at FROM oci_references ORDER BY id DESC LIMIT 10"

# Reset specific import to retry manually
sqlite3 pnc_import_state.db \
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
- **Bandit** -- Security vulnerability scanning
- **pip-audit** -- Dependency vulnerability scanning

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

### Security Checks

```bash
tox -e bandit
tox -e pip-audit
```

### Running All Checks

```bash
tox
```

This runs all environments: `py311`, `ruff`, `bandit`, `pip-audit`.

## License

Apache License 2.0
