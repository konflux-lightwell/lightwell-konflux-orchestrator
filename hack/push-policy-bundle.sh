#!/usr/bin/env bash
# Push the Rego policy directory as an OCI artifact.
#
# Usage:
#   ./hack/push-policy-bundle.sh [<image-ref> [<extra-tag>...]]
#
# Examples:
#   ./hack/push-policy-bundle.sh quay.io/light-castle/ec-policy:latest
#   ./hack/push-policy-bundle.sh quay.io/light-castle/ec-policy:$(git rev-parse --short HEAD) latest
#
# Prerequisites:
#   - oras CLI (https://oras.land)
#   - Logged in to the target registry: oras login quay.io
#
# The ECP references the pushed bundle by digest, e.g.:
#   oci::quay.io/light-castle/ec-policy@sha256:<digest>
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
POLICY_DIR="${REPO_ROOT}/policy"

DEFAULT_IMAGE="quay.io/light-castle/ec-policy:latest"
IMAGE="${1:-${DEFAULT_IMAGE}}"
shift || true
EXTRA_TAGS=("$@")

if ! command -v oras &>/dev/null; then
  echo "ERROR: oras not found. Install from https://oras.land/docs/installation" >&2
  exit 1
fi

echo "Pushing policy bundle to ${IMAGE}"
echo "  Source: ${POLICY_DIR}"
echo ""

cd "${REPO_ROOT}"
oras push "${IMAGE}" \
  --annotation "org.opencontainers.image.revision=$(git rev-parse HEAD 2>/dev/null || echo 'unknown')" \
  --annotation "org.opencontainers.image.source=https://github.com/konflux-lightwell/lightwell-konflux-orchestrator" \
  policy/

REPO="${IMAGE%%:*}"
for TAG in "${EXTRA_TAGS[@]+"${EXTRA_TAGS[@]}"}"; do
  echo "Tagging ${REPO}:${TAG}"
  oras tag "${IMAGE}" "${TAG}"
done

echo ""
echo "Pushed. Pinned digest reference for ECP:"
oras manifest fetch "${IMAGE}" --descriptor | python3 -c "
import json,sys
d=json.load(sys.stdin)
repo=sys.argv[1].split(':')[0]
print(f'  oci::{repo}@{d[\"digest\"]}')
" "${IMAGE}"
