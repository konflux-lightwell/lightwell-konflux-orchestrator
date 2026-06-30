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
- `QUAY_TOKEN` environment variable (unless using `--skip-fetch`)
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
import-orchestrator orchestrate --help

# Fetch OCI refs and trigger up to 10 parallel imports
QUAY_TOKEN=<token> import-orchestrator orchestrate --max-parallel 10

# Resume interrupted run from existing database
import-orchestrator --db pnc_import_state.db orchestrate --skip-fetch

# Fetch only (dry run to populate database for inspection)
QUAY_TOKEN=<token> import-orchestrator orchestrate --fetch-only

# Import remediated builds instead of rebuilds
QUAY_TOKEN=<token> LIGHTWELL_ARTIFACT_TYPE=REMEDIATED \
  import-orchestrator orchestrate --max-parallel 5

# Reset database and start fresh
QUAY_TOKEN=<token> import-orchestrator --reset orchestrate
```

### Command-Line Options

| Option | Default | Description |
|--------|---------|-------------|
| `--db` | `./pnc_import_state.db` | SQLite database path |
| `--fetch-script` | `build-definitions/docs/examples/fetch_pnc_oci_references.sh` | Path to fetch script |
| `--trigger-script` | `build-definitions/docs/examples/trigger-pnc-import.sh` | Path to trigger script |
| `--max-parallel` | `5` | Maximum parallel PipelineRuns |
| `--poll-interval` | `30` | Seconds between status checks |
| `--max-retries` | `3` | Max retry attempts for failed imports |
| `--skip-fetch` | `false` | Skip fetching OCI refs (resume from existing database) |
| `--fetch-only` | `false` | Only fetch and populate database, don't trigger imports |
| `--reset` | `false` | Reset database (delete existing data before fetch) |

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `QUAY_TOKEN` | Yes (unless `--skip-fetch`) | Authentication token for Quay.io API |
| `KONFLUX_TOKEN` or `KUBECONFIG` | Yes | Kubectl authentication |
| `LIGHTWELL_ARTIFACT_TYPE` | No | `REBUILD` (default) or `REMEDIATED` |

### Operation Flow

1. **Fetch phase** (unless `--skip-fetch`):
   - Runs `fetch_pnc_oci_references.sh` to get OCI references
   - Stores references in SQLite with `status='pending'`
   - Reports newly added vs. already tracked references

2. **Orchestration loop** (unless `--fetch-only`):
   - Checks status of triggered/running imports via kubectl
   - Updates database with current PipelineRun statuses
   - Triggers new imports up to `--max-parallel` limit
   - Sleeps for `--poll-interval` seconds
   - Repeats until all imports are complete (success or retry-exhausted)

3. **Exit codes**:
   - `0` — All imports successful or no work to do
   - `1` — Some imports failed after exhausting retries
   - `2` — Script validation error (missing files)

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

- **Black** -- Code formatter (skip string normalization with `-S`)
- **Ruff** -- Linter with rules: `E` (pycodestyle errors), `F` (pyflakes), `W` (pycodestyle warnings), `I` (import sorting)
- **Flake8** -- Additional style checking
- **Bandit** -- Security vulnerability scanning
- **pip-audit** -- Dependency vulnerability scanning

### Linting and Formatting

Check formatting (no changes applied):

```bash
tox -e black
```

Auto-format code:

```bash
tox -e black-format
```

Run ruff:

```bash
tox -e ruff
```

Run flake8:

```bash
tox -e flake8
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

This runs all environments: `py311`, `flake8`, `black`, `ruff`, `bandit`, `pip-audit`.

## License

Apache License 2.0
