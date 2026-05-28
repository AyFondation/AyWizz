#!/usr/bin/env bash
# =============================================================================
# File: run_k8s_system_tests.sh
# Version: 2
# Path: ay_platform_core/scripts/run_k8s_system_tests.sh
# Description: Bring up a kind cluster, deploy the system-test overlay,
#              run pytest against it, tear down. End-to-end self-contained.
#
#              v2 (2026-04-29) : also builds + kind-loads the UI image
#              (`aywizz-ui:test` from `infra/docker/Dockerfile.ui`)
#              and waits for the `ay-platform-ui` Deployment.
#
#              Phases:
#                1. Pre-flight  : kind, kubectl, docker on PATH.
#                2. Cluster up  : kind create + Traefik CRDs install.
#                3. Images      : build api+ui + `kind load docker-image`.
#                4. Apply       : `kubectl apply -k overlays/system-test`.
#                5. Wait        : Deployments Available / StatefulSets Ready /
#                                 Jobs Complete.
#                6. Tests       : `pytest -m system_k8s tests/system/k8s/`.
#                7. Teardown    : `kind delete cluster` (always, even on fail).
#
#              Usage (from monorepo root):
#                ay_platform_core/scripts/run_k8s_system_tests.sh
#                ay_platform_core/scripts/run_k8s_system_tests.sh --keep-cluster
#                ay_platform_core/scripts/run_k8s_system_tests.sh --skip-build
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUBPROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MONOREPO_ROOT="$(cd "${SUBPROJECT_ROOT}/.." && pwd)"
OVERLAY_PATH="${MONOREPO_ROOT}/infra/k8s/overlays/system-test"
DOCKERFILE_API="${MONOREPO_ROOT}/infra/docker/Dockerfile.api"
DOCKERFILE_UI="${MONOREPO_ROOT}/infra/docker/Dockerfile.ui"

CLUSTER_NAME="aywizz-systest"
NAMESPACE="aywizz"
IMAGE_TAG_API="aywizz-api:test"
IMAGE_TAG_UI="aywizz-ui:test"
TRAEFIK_VERSION="v3.3"
TRAEFIK_CRDS_URL="https://raw.githubusercontent.com/traefik/traefik/${TRAEFIK_VERSION}/docs/content/reference/dynamic-configuration/kubernetes-crd-definition-v1.yml"

KEEP_CLUSTER=0
SKIP_BUILD=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --keep-cluster) KEEP_CLUSTER=1 ;;
        --skip-build)   SKIP_BUILD=1 ;;
        -h|--help)
            sed -n '4,28p' "$0"
            exit 0
            ;;
        *) echo "ERROR: unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

# -----------------------------------------------------------------------------
# 1. Pre-flight
# -----------------------------------------------------------------------------

for tool in kind kubectl docker; do
    if ! command -v "${tool}" >/dev/null 2>&1; then
        echo "ERROR: required tool not on PATH: ${tool}" >&2
        echo "  kind   : github.com/kubernetes-sigs/kind" >&2
        echo "  kubectl: kubernetes.io/docs/tasks/tools" >&2
        echo "  docker : docker.com/get-started" >&2
        exit 2
    fi
done

cleanup() {
    if [ "${KEEP_CLUSTER}" -eq 1 ]; then
        echo "==> --keep-cluster set; cluster ${CLUSTER_NAME} preserved"
        return
    fi
    echo "==> Tearing down kind cluster ${CLUSTER_NAME}"
    kind delete cluster --name "${CLUSTER_NAME}" 2>/dev/null || true
}
trap cleanup EXIT

# -----------------------------------------------------------------------------
# 2. Cluster up
# -----------------------------------------------------------------------------

echo "==> Creating kind cluster ${CLUSTER_NAME}"
kind delete cluster --name "${CLUSTER_NAME}" 2>/dev/null || true
kind create cluster --name "${CLUSTER_NAME}" --wait 2m

echo "==> Installing Traefik ${TRAEFIK_VERSION} CRDs"
kubectl apply -f "${TRAEFIK_CRDS_URL}"

# -----------------------------------------------------------------------------
# 3. Image build + load
# -----------------------------------------------------------------------------

if [ "${SKIP_BUILD}" -eq 0 ]; then
    echo "==> Building API image ${IMAGE_TAG_API}"
    docker build -t "${IMAGE_TAG_API}" -f "${DOCKERFILE_API}" "${MONOREPO_ROOT}"
    echo "==> Building UI image ${IMAGE_TAG_UI}"
    docker build -t "${IMAGE_TAG_UI}" -f "${DOCKERFILE_UI}" "${MONOREPO_ROOT}"
    echo "==> Loading images into kind"
    kind load docker-image "${IMAGE_TAG_API}" --name "${CLUSTER_NAME}"
    kind load docker-image "${IMAGE_TAG_UI}" --name "${CLUSTER_NAME}"
else
    echo "==> --skip-build: assuming ${IMAGE_TAG_API} + ${IMAGE_TAG_UI} already loaded"
fi

# -----------------------------------------------------------------------------
# 4. Apply the system-test overlay
# -----------------------------------------------------------------------------

echo "==> Applying overlay ${OVERLAY_PATH}"
kubectl apply -k "${OVERLAY_PATH}"

# -----------------------------------------------------------------------------
# 5. Wait
# -----------------------------------------------------------------------------

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
    ay-platform-ui
)
for d in "${DEPLOYMENTS[@]}"; do
    echo "    waiting for deployment/${d}"
    kubectl wait --for=condition=Available -n "${NAMESPACE}" \
        "deployment/${d}" --timeout=5m
done

echo "==> Waiting for StatefulSets to be Ready"
for s in arangodb minio; do
    echo "    waiting for statefulset/${s}"
    kubectl wait --for=jsonpath='{.status.readyReplicas}'=1 -n "${NAMESPACE}" \
        "statefulset/${s}" --timeout=5m
done

echo "==> Waiting for bootstrap Jobs to complete"
for j in arangodb-init minio-init c12-workflow-seed; do
    echo "    waiting for job/${j}"
    kubectl wait --for=condition=Complete -n "${NAMESPACE}" \
        "job/${j}" --timeout=10m
done

# -----------------------------------------------------------------------------
# 6. Tests
# -----------------------------------------------------------------------------

echo "==> Running pytest -m system_k8s"
cd "${SUBPROJECT_ROOT}"
python -m pytest -m system_k8s tests/system/k8s/ -v --no-cov

echo "==> All system_k8s tests passed"
