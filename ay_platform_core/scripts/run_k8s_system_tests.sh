#!/usr/bin/env bash
# =============================================================================
# File: run_k8s_system_tests.sh
# Version: 5
# Path: ay_platform_core/scripts/run_k8s_system_tests.sh
# Description: Bring up a kind cluster, deploy the system-test overlay,
#              run pytest against it, tear down. End-to-end self-contained.
#
#              v5 (2026-10-12) : `--ui` runs the PLAYWRIGHT system tier
#              instead of pytest — five specs in
#              `ay_platform_ui/tests/system/` that nothing executed until
#              now, and the only tier that drives a browser against the
#              deployed platform. It swaps in `overlays/ui-system-test`
#              (demo seed + dev mode, so a project-scoped write is not a
#              403), port-forwards the GATEWAY (not the UI Service — the
#              client emits relative paths, so pages and API must share one
#              origin), installs chromium, and runs them against that
#              origin.
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
#                ay_platform_core/scripts/run_k8s_system_tests.sh --ui
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
TRAEFIK_VERSION="v3.7.13"
TRAEFIK_CRDS_URL="https://raw.githubusercontent.com/traefik/traefik/${TRAEFIK_VERSION}/docs/content/reference/dynamic-configuration/kubernetes-crd-definition-v1.yml"

KEEP_CLUSTER=0
SKIP_BUILD=0
UI_TIER=0
UI_SKIP_BROWSER=0
#: Local port the Playwright run reaches the cluster edge through. Distinct
#: from the Python tier's 18001 and from k8s_kind_smoke.sh's 18000 so the
#: three can coexist on one runner.
UI_LOCAL_PORT=18002
while [ "$#" -gt 0 ]; do
    case "$1" in
        --keep-cluster) KEEP_CLUSTER=1 ;;
        --skip-build)   SKIP_BUILD=1 ;;
        --ui)           UI_TIER=1 ;;
        # Forwarded to run_ui_system_tests.sh. CI installs the browser with
        # `--with-deps` as root beforehand, which a devcontainer user cannot
        # do — so who installs it is the caller's business, not this
        # script's.
        --skip-browser-install) UI_SKIP_BROWSER=1 ;;
        -h|--help)
            sed -n '4,40p' "$0"
            exit 0
            ;;
        *) echo "ERROR: unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

# `--ui` swaps the overlay for the one that enables the demo seed and dev
# mode. It is a DIFFERENT overlay rather than two flipped flags so the
# Python tier's stack stays closed — see the header of
# `infra/k8s/overlays/ui-system-test/kustomization.yaml`.
if [ "${UI_TIER}" -eq 1 ]; then
    OVERLAY_PATH="${MONOREPO_ROOT}/infra/k8s/overlays/ui-system-test"
fi

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

# shellcheck source=../../infra/scripts/_k8s_diagnostics.sh
. "${MONOREPO_ROOT}/infra/scripts/_k8s_diagnostics.sh"
# shellcheck source=../../infra/scripts/_kind_preload.sh
. "${MONOREPO_ROOT}/infra/scripts/_kind_preload.sh"

cleanup() {
    # Capture BEFORE deleting: the trap fires on failure too, and the cluster
    # holds the only explanation. Guarded on a non-zero status so a passing
    # run stays quiet.
    local status=$?
    if [ "${status}" -ne 0 ]; then
        dump_k8s_diagnostics "${NAMESPACE}"
    fi
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

# Third-party images ALWAYS, even under --skip-build: they are not what that
# flag skips, and leaving them to the node's own containerd is what broke this
# job on v0.1.0-beta.14 (see `_kind_preload.sh`).
preload_third_party_images "${CLUSTER_NAME}" "${OVERLAY_PATH}" \
    "${IMAGE_TAG_API}" "${IMAGE_TAG_UI}"

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
# `c12-workflow-seed` removed 2026-09-18 — the c12-workflow Deployment's
# `import-workflows` initContainer imports and publishes the workflows, so a
# ready c12-workflow pod is now the proof this wait used to provide.
for j in arangodb-init minio-init; do
    echo "    waiting for job/${j}"
    kubectl wait --for=condition=Complete -n "${NAMESPACE}" \
        "job/${j}" --timeout=10m
done

# -----------------------------------------------------------------------------
# 6. Tests
# -----------------------------------------------------------------------------

if [ "${UI_TIER}" -eq 1 ]; then
    # -------------------------------------------------------------------------
    # 6b. UI system tier — a real browser against the real edge.
    #
    # WHY THIS EXISTS. `ay_platform_ui/tests/system/` held five specs that
    # NOTHING executed: `npm run test:system` and
    # `playwright.system.config.ts` were both present, the specs listed
    # correctly, and no workflow or script called them. It is the only tier
    # that drives a browser against the deployed platform — the exact class
    # `source-upload.spec.ts` was written to guard (the 405 regression).
    #
    # The two blockers were concrete, not unknown: nothing seeded the stack
    # (hence 403 on every project-scoped write) and no browser was
    # installed. The overlay fixes the first; the two steps below fix the
    # second and give the specs a stable origin to talk to.
    # -------------------------------------------------------------------------
    echo "==> Waiting for the UI Deployment"
    kubectl wait --for=condition=Available -n "${NAMESPACE}" \
        deployment/ay-platform-ui --timeout=5m

    # Delegated rather than duplicated: the port-forward target, the
    # readiness poll and the browser install are the same three steps an
    # operator needs against an EXISTING cluster, and two copies of them
    # would drift. That script creates and deletes nothing, so this one
    # keeps sole ownership of the cluster's lifetime.
    echo "==> Handing off to run_ui_system_tests.sh"
    UI_ARGS=(--namespace "${NAMESPACE}" --port "${UI_LOCAL_PORT}")
    if [ "${UI_SKIP_BROWSER}" -eq 1 ]; then
        UI_ARGS+=(--skip-browser-install)
    fi
    "${SCRIPT_DIR}/run_ui_system_tests.sh" "${UI_ARGS[@]}"
    exit 0
fi

echo "==> Running pytest -m system_k8s"
cd "${SUBPROJECT_ROOT}"
python -m pytest -m system_k8s tests/system/k8s/ -v --no-cov

echo "==> All system_k8s tests passed"
