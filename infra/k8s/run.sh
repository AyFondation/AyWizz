#!/usr/bin/env bash
# =============================================================================
# File: run.sh
# Version: 6
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
#              v3 (2026-07-27): adds `--reinit` — deletes the bootstrap
#              Jobs before applying so a changed (immutable) Job spec, e.g.
#              a new MinIO bucket, actually takes effect. All Jobs are
#              idempotent one-shots.
#              v4 (2026-07-28): adds `--restart` — forces a rollout restart
#              of every Deployment after apply. Needed when the images
#              changed but their TAGS did not (overlays/dev pins `:latest`
#              with `IfNotPresent` + `disableNameSuffixHash`, so `apply` is
#              a no-op and pods keep the old image). Run it after
#              `infra/scripts/k8s_build_images.sh`.
#              v6 (2026-09-02): adds `--ingress` — installs the ingress-nginx
#              controller (pinned in `base/ingress_nginx/`) that serves the
#              edge `Ingress` declared by each overlay. Needed once per
#              cluster on any cluster that ships no ingress controller
#              (docker-desktop, kind, bare-metal). SKIP it when the cluster
#              already has one and point `ingressClassName` at that class.
#              v5 (2026-08-24): fixed `--no-jobs` — the Job-stripping filter
#              was fed via a heredoc on `python3 -` which replaced python's
#              stdin, swallowing the `kubectl kustomize` pipe and applying an
#              EMPTY manifest. Now uses `python3 -c` + a non-empty guard.
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

# The ingress controller that serves the edge `Ingress` object. Pinned in
# `base/ingress_nginx/kustomization.yaml`; applied as its OWN kustomize
# target because it declares its own namespace (see that file's header).
INGRESS_NGINX_PATH="${SCRIPT_DIR}/base/ingress_nginx"

usage() {
    cat <<EOF
Usage: $(basename "$0") <env> [options]

Environments:
  dev     apply overlays/dev
  prod    apply overlays/prod (when present)

Options:
  --crds        install Traefik CRDs (v3.3) before applying — once per cluster
  --ingress     install the ingress-nginx controller before applying — once
                per cluster. SKIP on a cluster that already has an ingress
                controller (AKS app routing, AGIC, an existing nginx); set
                `ingressClassName` in overlays/<env>/ingress.yaml instead.
  --wait        wait for every Deployment to become Available (5 min cap)
  --no-jobs     skip bootstrap Jobs (use when re-applying without re-init)
  --reinit      delete + recreate bootstrap Jobs (needed when a Job spec
                changed, e.g. a new MinIO bucket). Mutually exclusive with
                --no-jobs. All Jobs are idempotent one-shots.
  --restart     force a rollout restart of every Deployment after apply
                (needed when images changed but their tags did not, e.g.
                :latest — run after k8s_build_images.sh)
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
WANT_INGRESS=0
REINIT=0
RESTART=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --wait) WAIT=1 ;;
        --no-jobs) SKIP_JOBS=1 ;;
        --crds) WANT_CRDS=1 ;;
        --ingress) WANT_INGRESS=1 ;;
        --reinit) REINIT=1 ;;
        --restart) RESTART=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

if [ "${SKIP_JOBS}" -eq 1 ] && [ "${REINIT}" -eq 1 ]; then
    echo "ERROR: --no-jobs and --reinit are mutually exclusive" >&2
    exit 2
fi

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

# Applied as a SEPARATE kustomize target on purpose: the bundle owns an
# `ingress-nginx` Namespace that the overlays' `namespace: aywizz`
# transformer would rename (same trap as base/c4_workers, Q-100-023).
if [ "${WANT_INGRESS}" -eq 1 ]; then
    echo "==> Installing ingress-nginx controller"
    kubectl apply -k "${INGRESS_NGINX_PATH}"
    echo "==> Waiting for the ingress-nginx admission webhook to be ready"
    kubectl wait --namespace ingress-nginx \
        --for=condition=Ready pod \
        --selector=app.kubernetes.io/component=controller \
        --timeout=180s
fi

# Bootstrap Jobs are immutable: once Completed, `apply` cannot change their
# spec (e.g. a new bucket in minio-init). --reinit deletes them first so the
# apply recreates them with the current spec. They are all idempotent
# one-shots, so re-running is safe.
if [ "${REINIT}" -eq 1 ]; then
    echo "==> --reinit: deleting bootstrap Jobs in aywizz so they re-run"
    kubectl delete jobs --all -n aywizz --ignore-not-found
fi

echo "==> Applying overlay: ${OVERLAY_PATH}"

if [ "${SKIP_JOBS}" -eq 1 ]; then
    # Build, strip Job documents, then apply. `yq`-free filter via Python.
    # NB: the filter program is passed via `python3 -c` (NOT a heredoc on
    # `python3 -`) — a heredoc replaces python's stdin, which would swallow
    # the `kubectl kustomize` pipe and emit an EMPTY manifest (apply then
    # fails / no-ops). With `-c`, stdin stays the kustomize pipe.
    BUILD_OUT="$(mktemp)"
    trap 'rm -f "${BUILD_OUT}"' EXIT
    kubectl kustomize "${OVERLAY_PATH}" \
        | python3 -c 'import sys, yaml
docs = list(yaml.safe_load_all(sys.stdin))
kept = [d for d in docs if isinstance(d, dict) and d.get("kind") != "Job"]
sys.stdout.write(yaml.safe_dump_all(kept, sort_keys=False))' \
        > "${BUILD_OUT}"
    if [ ! -s "${BUILD_OUT}" ]; then
        echo "ERROR: --no-jobs produced an empty manifest (filter failed)" >&2
        exit 2
    fi
    kubectl apply -f "${BUILD_OUT}"
else
    kubectl apply -k "${OVERLAY_PATH}"
fi

if [ "${RESTART}" -eq 1 ]; then
    echo "==> Forcing rollout restart of all Deployments in aywizz"
    kubectl rollout restart deployment -n aywizz
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
