#!/usr/bin/env bash
# =============================================================================
# File: run.sh
# Version: 2
# Path: infra/k8s/run.sh
# Description: Apply a K8s overlay to the active kubectl context.
#              Wrapper around the denied `kubectl apply -k` (per
#              `.claude/settings.json`); the wrapper is the explicit,
#              auditable entry point per CLAUDE.md §5.3.
#
#              v2 (2026-05-27): adds `--crds` — installs the Traefik
#              IngressRoute/Middleware CRDs (v3.3, matching the gateway
#              image) before applying the overlay. Needed once per cluster
#              on docker-desktop / any cluster that doesn't already ship
#              them (kind smoke installs them itself).
#
#              Usage (from monorepo root or anywhere via absolute path):
#                infra/k8s/run.sh dev          # apply overlays/dev
#                infra/k8s/run.sh prod         # apply overlays/prod (when it exists)
#                infra/k8s/run.sh dev --wait   # wait for Deployments
#                infra/k8s/run.sh dev --no-jobs # skip bootstrap Jobs
#
#              The active kubectl context decides WHICH cluster receives
#              the apply. Verify before running:
#                kubectl config current-context
#                kubectl config use-context <kind-aywizz-ci|docker-desktop|...>
#
#              Pre-req for `dev`: a cluster reachable via `kubectl` AND
#              Traefik CRDs installed (the kind smoke script does this
#              automatically — see `infra/scripts/k8s_kind_smoke.sh`).
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Traefik CRDs (IngressRoute / Middleware). Pinned tag mirrors the gateway
# image (traefik:v3.3) and the kind smoke script.
TRAEFIK_VERSION="v3.3"
TRAEFIK_CRDS_URL="https://raw.githubusercontent.com/traefik/traefik/${TRAEFIK_VERSION}/docs/content/reference/dynamic-configuration/kubernetes-crd-definition-v1.yml"

usage() {
    cat <<EOF
Usage: $(basename "$0") <env> [options]

Environments:
  dev     apply overlays/dev
  prod    apply overlays/prod (when present)

Options:
  --crds        install Traefik CRDs (v3.3) before applying — once per cluster
  --wait        wait for every Deployment to become Available (5 min cap)
  --no-jobs     skip bootstrap Jobs (use when re-applying without re-init)
  -h, --help    this message
EOF
}

if [ "$#" -lt 1 ]; then
    usage >&2
    exit 2
fi

case "$1" in
    -h|--help) usage; exit 0 ;;
esac

ENV="$1"
shift

WAIT=0
SKIP_JOBS=0
WANT_CRDS=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --wait) WAIT=1 ;;
        --no-jobs) SKIP_JOBS=1 ;;
        --crds) WANT_CRDS=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

case "${ENV}" in
    dev|prod) ;;
    *) echo "ERROR: unknown env: ${ENV} (expected dev|prod)" >&2; exit 2 ;;
esac

OVERLAY_PATH="${SCRIPT_DIR}/overlays/${ENV}"
if [ ! -d "${OVERLAY_PATH}" ]; then
    echo "ERROR: overlay does not exist: ${OVERLAY_PATH}" >&2
    exit 2
fi

CONTEXT="$(kubectl config current-context 2>/dev/null || true)"
if [ -z "${CONTEXT}" ]; then
    echo "ERROR: no active kubectl context. Run \`kubectl config use-context <name>\` first." >&2
    exit 1
fi
echo "==> kubectl context: ${CONTEXT}"

if [ "${WANT_CRDS}" -eq 1 ]; then
    echo "==> Installing Traefik ${TRAEFIK_VERSION} CRDs"
    kubectl apply -f "${TRAEFIK_CRDS_URL}"
fi

echo "==> Applying overlay: ${OVERLAY_PATH}"

if [ "${SKIP_JOBS}" -eq 1 ]; then
    # Build, strip Job documents, then apply. `yq`-free filter via Python.
    BUILD_OUT="$(mktemp)"
    trap 'rm -f "${BUILD_OUT}"' EXIT
    kubectl kustomize "${OVERLAY_PATH}" | python3 - > "${BUILD_OUT}" <<'PY'
import sys, yaml
docs = list(yaml.safe_load_all(sys.stdin))
kept = [d for d in docs if isinstance(d, dict) and d.get("kind") != "Job"]
sys.stdout.write(yaml.safe_dump_all(kept, sort_keys=False))
PY
    kubectl apply -f "${BUILD_OUT}"
else
    kubectl apply -k "${OVERLAY_PATH}"
fi

if [ "${WAIT}" -eq 1 ]; then
    echo "==> Waiting for Deployments to become Available (5 min cap each)"
    NS="aywizz"
    for d in $(kubectl get deployments -n "${NS}" -o jsonpath='{.items[*].metadata.name}'); do
        echo "    waiting for deployment/${d}"
        kubectl wait --for=condition=Available -n "${NS}" \
            "deployment/${d}" --timeout=5m
    done
    echo "==> All Deployments Available"
fi

echo "==> run ${ENV} OK"
