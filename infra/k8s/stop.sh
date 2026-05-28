#!/usr/bin/env bash
# =============================================================================
# File: stop.sh
# Version: 1
# Path: infra/k8s/stop.sh
# Description: Tear down a K8s overlay from the active kubectl context.
#              Wrapper around the denied `kubectl delete -k` (per
#              `.claude/settings.json`); the wrapper is the explicit,
#              auditable entry point per CLAUDE.md §5.3.
#
#              Default behaviour KEEPS PersistentVolumeClaims (StatefulSet
#              StorageClass + retain semantics — re-applying brings the
#              data back). Pass `--wipe` to ALSO delete the PVCs (and the
#              namespace itself, which cascades).
#
#              Usage:
#                infra/k8s/stop.sh dev
#                infra/k8s/stop.sh dev --wipe   # destroy data too
#                infra/k8s/stop.sh prod
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    cat <<EOF
Usage: $(basename "$0") <env> [options]

Environments:
  dev     delete resources of overlays/dev
  prod    delete resources of overlays/prod (when present)

Options:
  --wipe        also delete PVCs + the namespace itself (DESTRUCTIVE).
                Without --wipe, PVCs remain so re-running run.sh
                re-attaches the existing data volumes.
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

WIPE=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --wipe) WIPE=1 ;;
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
    echo "ERROR: no active kubectl context." >&2
    exit 1
fi
echo "==> kubectl context: ${CONTEXT}"

NS="aywizz"

echo "==> Deleting resources from overlay: ${OVERLAY_PATH}"
# `--ignore-not-found` makes the call idempotent — second run after a
# `--wipe` doesn't complain about missing resources.
kubectl delete -k "${OVERLAY_PATH}" --ignore-not-found=true

if [ "${WIPE}" -eq 1 ]; then
    echo "==> --wipe set: deleting PVCs in namespace ${NS}"
    kubectl delete pvc --all -n "${NS}" --ignore-not-found=true
    echo "==> Deleting namespace ${NS}"
    kubectl delete namespace "${NS}" --ignore-not-found=true
fi

echo "==> stop ${ENV} OK"
