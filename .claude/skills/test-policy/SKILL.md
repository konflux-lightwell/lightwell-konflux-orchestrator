---
name: test-policy
description: Use when running, testing, or verifying the Rego policy files in policy/. Triggers on "run policy tests", "test rego", "check policy", or before committing policy changes.
---

# Run Policy Tests

The `policy/` Rego files depend on `data.lib.*` from [conforma/policy](https://github.com/conforma/policy) and on `ec.oci.*` custom builtins from the `ec` CLI. Plain `opa` won't work.

## Setup (once)

Install the `ec` CLI binary:

```bash
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
VERSION=$(curl -sL https://api.github.com/repos/conforma/cli/releases/latest | python3 -c "import json,sys; print(json.load(sys.stdin)['tag_name'])")
curl -sL "https://github.com/conforma/cli/releases/download/${VERSION}/ec_${OS}_${ARCH}" \
  -o ~/.local/bin/ec && chmod +x ~/.local/bin/ec
```

## Command

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)

# Use the ec binary from the install location above (PATH may have an older version)
EC=~/.local/bin/ec

# Download lib once; re-run this line to refresh it
LIB="$REPO_ROOT/.claude/skills/test-policy/lib"
if [ ! -d "$LIB/lib" ]; then
  mkdir -p "$LIB"
  curl -sL https://github.com/conforma/policy/archive/refs/heads/main.tar.gz \
    | tar xz --strip-components=2 -C "$LIB" "policy-main/policy/lib"
fi

"$EC" opa test \
  --ignore '*_test.rego' \
  "$LIB/lib" \
  "$REPO_ROOT/policy/pnc_import.rego" \
  "$REPO_ROOT/policy/pnc_import_test.rego" \
  --verbose
```

`--ignore '*_test.rego'` excludes lib test files while still running the explicitly-listed pnc test file.

To refresh the cached lib: `rm -rf "$(git rev-parse --show-toplevel)/.claude/skills/test-policy/lib"`

## Run a single test

Add `--run <test_name>` before `--verbose` (reuse the same `EC`, `LIB`, `REPO_ROOT` variables):

```bash
"$EC" opa test \
  --ignore '*_test.rego' \
  "$LIB/lib" \
  "$REPO_ROOT/policy/pnc_import.rego" \
  "$REPO_ROOT/policy/pnc_import_test.rego" \
  --run test_distribution_target_absent_is_denied \
  --verbose
```

## Why not just `opa test`?

The policy uses `ec.oci.image_manifest`, a custom builtin only available in the `ec` binary.
