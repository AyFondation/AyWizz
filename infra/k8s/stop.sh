#!/usr/bin/env bash
# =============================================================================
# File: stop.sh
# Version: 3
# Path: infra/k8s/stop.sh
# Description: Tear down a K8s overlay from the active kubectl context.
#              Wrapper around the denied `kubectl delete -k` (per
#              `.claude/settings.json`); the wrapper is the explicit,
#              auditable entry point per CLAUDE.md §5.3.
#
#              Default behaviour KEEPS the Namespace and every
#              PersistentVolumeClaim, so `run.sh` re-binds the existing
#              volumes and the data comes back. Pass `--wipe` to destroy
#              the PVCs and the Namespace.
#
#              v3 (2026-09-08) — BUGFIX, data loss. v2 claimed the default
#              path preserved PVCs; it did not. `kubectl delete -k` deletes
#              EVERY declared resource, and the `Namespace` is one of them
#              (declared in `base/`). Deleting a Namespace cascades to the
#              PVCs it contains, and the dev PVs are provisioned with
#              reclaim policy `Delete` — so `stop.sh dev` destroyed
#              ArangoDB and MinIO exactly like `--wipe`, making `--wipe` a
#              no-op flag and the documented guarantee false. The
#              non-destructive path now renders the overlay and filters out
#              the `Namespace` and `PersistentVolumeClaim` documents before
#              deleting. Verify a change here with `--dry-run` before
#              trusting it: this script destroys data by design.
#
#              Usage:
#                infra/k8s/stop.sh dev
#                infra/k8s/stop.sh dev --wipe   # destroy data too
#                infra/k8s/stop.sh dev --wipe --ingress  # true from-scratch
#                infra/k8s/stop.sh prod
#
#              v2 (2026-09-02): adds `--ingress`, symmetric to
#              `run.sh --ingress` — removes the ingress-nginx controller so
#              a full teardown really returns the cluster to a bare state.
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
  --wipe        also delete the PVCs and the Namespace (DESTRUCTIVE —
                ArangoDB, MinIO, n8n and Ollama data are gone for good:
                the dev PVs use reclaim policy Delete).
                Without --wipe, the Namespace and every PVC are KEPT, so
                re-running run.sh re-attaches the existing data volumes.
  --dry-run     simulate: print what WOULD be deleted, delete nothing.
                Use it before any destructive run, and after changing this
                script.
  --ingress     ALSO remove the ingress-nginx controller installed by
                \`run.sh <env> --ingress\`. Symmetric to that flag. Leave it
                off to keep the controller across redeploys — it is a
                cluster prerequisite, not part of the application.
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
DROP_INGRESS=0
DRY_RUN=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --wipe) WIPE=1 ;;
        --ingress) DROP_INGRESS=1 ;;
        --dry-run) DRY_RUN=1 ;;
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

# `--dry-run` turns every delete into a server-free simulation, so the
# resource list can be reviewed BEFORE anything is destroyed. Worth using on
# this script in particular: getting its scope wrong costs the ArangoDB and
# MinIO volumes.
DRY_FLAG=""
if [ "${DRY_RUN}" -eq 1 ]; then
    DRY_FLAG="--dry-run=client"
    echo "==> DRY RUN — nothing will be deleted"
fi

# Render the overlay minus the documents whose `kind` matches $1, so a
# non-destructive teardown can skip the Namespace and the PVCs. Deleting the
# Namespace is what silently destroyed data before: it cascades to every PVC
# in it, and the hostpath/dynamic PVs are provisioned with reclaim policy
# `Delete`.
_render_without() {
    kubectl kustomize "${OVERLAY_PATH}" | awk -v drop="^kind: ($1)[[:space:]]*$" '
        BEGIN { RS = "\n---\n"; ORS = "" }
        {
            keep = 1
            n = split($0, lines, "\n")
            for (i = 1; i <= n; i++) if (lines[i] ~ drop) { keep = 0; break }
            if (keep && $0 ~ /[^[:space:]]/) {
                if (started) print "\n---\n"
                print $0
                started = 1
            }
        }
        END { print "\n" }'
}

echo "==> Deleting resources from overlay: ${OVERLAY_PATH}"
# `--ignore-not-found` makes every call idempotent — a second run after a
# `--wipe` doesn't complain about missing resources.
if [ "${WIPE}" -eq 1 ]; then
    # Destructive path: everything, then the leftovers the overlay does not
    # declare (StatefulSet volumeClaimTemplate PVCs) and the Namespace.
    kubectl delete -k "${OVERLAY_PATH}" --ignore-not-found=true ${DRY_FLAG}
    echo "==> --wipe set: deleting PVCs in namespace ${NS}"
    kubectl delete pvc --all -n "${NS}" --ignore-not-found=true ${DRY_FLAG}
    echo "==> Deleting namespace ${NS}"
    kubectl delete namespace "${NS}" --ignore-not-found=true ${DRY_FLAG}
else
    # Data-preserving path: drop the workloads but KEEP the Namespace and
    # every PVC. The Namespace must survive, otherwise its deletion cascades
    # to the PVCs regardless of what we skip here. Deleting a StatefulSet
    # does NOT delete its volumeClaimTemplate PVCs (Kubernetes semantics),
    # so ArangoDB and MinIO data survive and are re-bound on the next
    # `run.sh`.
    _render_without 'Namespace|PersistentVolumeClaim' \
        | kubectl delete -f - --ignore-not-found=true ${DRY_FLAG}
    echo "==> PVCs and namespace ${NS} kept (pass --wipe to destroy data)"
fi

# The ingress controller is a CLUSTER prerequisite, not part of the app, so
# it survives a normal teardown. --ingress removes it too, for a genuine
# from-scratch state.
if [ "${DROP_INGRESS}" -eq 1 ]; then
    echo "==> --ingress set: removing the ingress-nginx controller"
    kubectl delete -k "${SCRIPT_DIR}/base/ingress_nginx" --ignore-not-found=true
fi

echo "==> stop ${ENV} OK"
