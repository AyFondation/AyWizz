#!/usr/bin/env bash
# =============================================================================
# File: k8s_kind_smoke.sh
# Version: 1
# Path: infra/scripts/k8s_kind_smoke.sh
# Description: L2 + L3 — apply the dev overlay to an ephemeral kind
#              cluster and verify endpoints respond.
#
#                L2 (cluster smoke):
#                  - kind create cluster
#                  - install Traefik CRDs (required by IngressRoute /
#                    Middleware resources)
#                  - kubectl apply -k overlays/dev
#                  - kubectl wait for every Deployment + StatefulSet
#                  - kubectl wait for every Job to complete
#
#                L3 (endpoint smoke):
#                  - kubectl port-forward svc/c1-gateway 18000:80
#                  - curl /auth/config (no auth, returns 200)
#                  - curl protected /api/v1/memory/health (no creds, 401)
#
#              Cluster is destroyed on exit (success OR failure) via
#              EXIT trap. Re-runnable.
#
#              Required tools (CI installs automatically):
#                kind         — github.com/kubernetes-sigs/kind
#                kubectl      — already in devcontainer
#                curl         — denied by Claude allow-list; this script
#                               is intended to run in CI runners where
#                               curl is universally present
#
#              Usage (from monorepo root):
#                infra/scripts/k8s_kind_smoke.sh
#                infra/scripts/k8s_kind_smoke.sh --keep-cluster   # debug
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OVERLAY_PATH="${INFRA_ROOT}/k8s/overlays/dev"
CLUSTER_NAME="aywizz-ci"
NAMESPACE="aywizz"
PORT_FWD_LOCAL=18000

# CRDs required by the c1_gateway/middlewares.yaml + ingressroutes.yaml.
# Pinned tag matches the Traefik image version in the gateway deployment.
TRAEFIK_VERSION="v3.3"
TRAEFIK_CRDS_URL="https://raw.githubusercontent.com/traefik/traefik/${TRAEFIK_VERSION}/docs/content/reference/dynamic-configuration/kubernetes-crd-definition-v1.yml"

KEEP_CLUSTER=0
if [ "${1:-}" = "--keep-cluster" ]; then
    KEEP_CLUSTER=1
fi

cleanup() {
    if [ "${KEEP_CLUSTER}" -eq 1 ]; then
        echo "==> --keep-cluster set; not deleting ${CLUSTER_NAME}"
        return
    fi
    echo "==> Tearing down kind cluster ${CLUSTER_NAME}"
    kind delete cluster --name "${CLUSTER_NAME}" 2>/dev/null || true
}
trap cleanup EXIT

# -----------------------------------------------------------------------------
# Pre-flight
# -----------------------------------------------------------------------------

for tool in kind kubectl curl; do
    if ! command -v "${tool}" >/dev/null 2>&1; then
        echo "ERROR: required tool not on PATH: ${tool}" >&2
        exit 2
    fi
done

# -----------------------------------------------------------------------------
# L2 — cluster bring-up
# -----------------------------------------------------------------------------

echo "==> Creating kind cluster ${CLUSTER_NAME}"
kind delete cluster --name "${CLUSTER_NAME}" 2>/dev/null || true
kind create cluster --name "${CLUSTER_NAME}" --wait 2m

echo "==> Installing Traefik ${TRAEFIK_VERSION} CRDs"
kubectl apply -f "${TRAEFIK_CRDS_URL}"

echo "==> Applying overlay ${OVERLAY_PATH}"
kubectl apply -k "${OVERLAY_PATH}"

echo "==> Waiting for Deployments to become Available"
DEPLOYMENTS=(
    c1-gateway
    c2-auth
    c3-conversation
    c4-orchestrator
    c5-requirements
    c6-validation
    c7-memory
    c9-mcp
    c12-workflow
    ollama
)
for d in "${DEPLOYMENTS[@]}"; do
    echo "    waiting for deployment/${d}"
    kubectl wait --for=condition=Available -n "${NAMESPACE}" \
        "deployment/${d}" --timeout=5m
done

echo "==> Waiting for StatefulSets to be Ready"
STATEFULSETS=(arangodb minio)
for s in "${STATEFULSETS[@]}"; do
    echo "    waiting for statefulset/${s} replicas-ready"
    # Note: there is no `condition=Ready` for StatefulSets; we poll
    # `.status.readyReplicas == .spec.replicas` via kubectl wait's
    # generic JSONPath-based wait.
    kubectl wait --for=jsonpath='{.status.readyReplicas}'=1 -n "${NAMESPACE}" \
        "statefulset/${s}" --timeout=5m
done

echo "==> Waiting for bootstrap Jobs to complete"
JOBS=(arangodb-init minio-init ollama-seed c12-workflow-seed)
for j in "${JOBS[@]}"; do
    echo "    waiting for job/${j}"
    kubectl wait --for=condition=Complete -n "${NAMESPACE}" \
        "job/${j}" --timeout=10m
done

# -----------------------------------------------------------------------------
# L3 — endpoint smoke via Traefik
# -----------------------------------------------------------------------------

echo "==> Starting port-forward svc/c1-gateway -> localhost:${PORT_FWD_LOCAL}"
kubectl port-forward -n "${NAMESPACE}" svc/c1-gateway \
    "${PORT_FWD_LOCAL}:80" >/tmp/k8s-port-forward.log 2>&1 &
PF_PID=$!
sleep 3

if ! kill -0 "${PF_PID}" 2>/dev/null; then
    echo "ERROR: port-forward died immediately. Log:" >&2
    cat /tmp/k8s-port-forward.log >&2
    exit 1
fi

cleanup_pf() {
    kill "${PF_PID}" 2>/dev/null || true
    cleanup
}
trap cleanup_pf EXIT

echo "==> GET /auth/config — expect 200 (open route)"
HTTP=$(curl -sS -o /dev/null -w '%{http_code}' \
    "http://localhost:${PORT_FWD_LOCAL}/auth/config")
if [ "${HTTP}" != "200" ]; then
    echo "ERROR: /auth/config returned ${HTTP}, expected 200" >&2
    exit 1
fi
echo "    OK"

echo "==> GET /api/v1/memory/health — expect 401 (forward-auth-c2 fires)"
# /health is open inside the C7 process, but Traefik's forward-auth
# middleware fires FIRST and rejects unauthenticated requests with 401.
# A 401 here proves the Traefik middleware chain is wired correctly.
HTTP=$(curl -sS -o /dev/null -w '%{http_code}' \
    "http://localhost:${PORT_FWD_LOCAL}/api/v1/memory/health")
if [ "${HTTP}" != "401" ]; then
    echo "ERROR: /api/v1/memory/health returned ${HTTP}, expected 401" >&2
    exit 1
fi
echo "    OK"

echo "==> L2+L3 OK — cluster smoke + endpoint contract verified"
