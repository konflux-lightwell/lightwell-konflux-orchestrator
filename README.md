# import-orchestrator

CLI tool for orchestrating data imports.

## Requirements

- Python 3.11+

## Installation

Install the package in editable mode:

```bash
pip install -e .
```

For development (includes pytest, ruff, etc.):

```bash
pip install -e ".[dev]"
```

### Dependencies

| Package | Purpose |
|---------|---------|
| `click` | CLI framework |

## Usage

```bash
import-orchestrator --version
import-orchestrator --help
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
