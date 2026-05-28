#!/usr/bin/env bash
# =============================================================================
# File: k8s_validate.sh
# Version: 2
# Path: infra/scripts/k8s_validate.sh
# Description: L1 — static lint of K8s manifests. Two stages:
#                (1) `kubectl kustomize` builds the overlay — catches
#                    Kustomize errors, missing references, malformed
#                    generators, and unparseable YAML. ALWAYS run.
#                (2) `kubeval` validates each document against published
#                    Kubernetes schemas. Skipped silently when kubeval
#                    is not on PATH (CI installs it).
#
#              v2 (2026-04-28): dropped `kubectl apply --dry-run=client`
#              because it requires cluster connectivity even with
#              `--validate=ignore`. Replaced with kubeval (true offline
#              schema validation) + a Python sanity sweep that asserts
#              every doc has apiVersion / kind / metadata.name set.
#
#              Usage (from monorepo root):
#                infra/scripts/k8s_validate.sh
#                infra/scripts/k8s_validate.sh overlays/prod
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OVERLAY="${1:-overlays/dev}"
OVERLAY_PATH="${INFRA_ROOT}/k8s/${OVERLAY}"

if [ ! -d "${OVERLAY_PATH}" ]; then
    echo "ERROR: overlay path does not exist: ${OVERLAY_PATH}" >&2
    exit 2
fi

BUILD_OUT="$(mktemp)"
trap 'rm -f "${BUILD_OUT}"' EXIT

echo "==> Building overlay: ${OVERLAY_PATH}"
if ! kubectl kustomize "${OVERLAY_PATH}" > "${BUILD_OUT}"; then
    echo "ERROR: kubectl kustomize failed" >&2
    exit 1
fi

LINES=$(wc -l < "${BUILD_OUT}")
DOCS=$(grep -c '^---$' "${BUILD_OUT}" || true)
echo "    built ${LINES} lines / ${DOCS} document separators"

echo "==> Sanity check: every document has apiVersion / kind / metadata.name"
python3 - "${BUILD_OUT}" <<'PY'
import sys, yaml
path = sys.argv[1]
with open(path) as f:
    docs = list(yaml.safe_load_all(f))
errors = []
for i, d in enumerate(docs):
    if d is None:
        continue
    if not isinstance(d, dict):
        errors.append(f"doc {i}: not a mapping ({type(d).__name__})")
        continue
    for key in ("apiVersion", "kind"):
        if not d.get(key):
            errors.append(f"doc {i}: missing {key}")
    md = d.get("metadata") or {}
    if not md.get("name"):
        errors.append(f"doc {i} (kind={d.get('kind')}): missing metadata.name")
if errors:
    print("\n".join(errors), file=sys.stderr)
    sys.exit(1)
n = sum(1 for d in docs if isinstance(d, dict))
print(f"    {n} documents, all have apiVersion / kind / metadata.name")
PY

if command -v kubeval >/dev/null 2>&1; then
    echo "==> kubeval schema validation"
    # `--ignore-missing-schemas` skips Traefik CRDs (IngressRoute,
    # Middleware) — they have no published schema in the kubeval store.
    # L2 (kind cluster) exercises real admission for those.
    if ! kubeval --strict --ignore-missing-schemas "${BUILD_OUT}"; then
        echo "ERROR: kubeval found schema violations" >&2
        exit 1
    fi
else
    echo "==> kubeval not installed, skipping schema validation"
    echo "    (install via https://github.com/instrumenta/kubeval — CI installs automatically)"
fi

echo "==> L1 OK"
