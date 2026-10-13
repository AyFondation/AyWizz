#!/usr/bin/env bash
# =============================================================================
# File: run_ui_system_tests.sh
# Version: 1
# Path: ay_platform_core/scripts/run_ui_system_tests.sh
# Description: Run the PLAYWRIGHT system tier — a real browser against a real
#              K8s deployment — against whatever cluster the current kube
#              context points at.
#
#              WHY IT EXISTS. `ay_platform_ui/tests/system/` held five specs
#              that NOTHING executed. `npm run test:system` and
#              `playwright.system.config.ts` were both present, the specs
#              listed correctly, and no script or workflow called them. It is
#              the only tier that drives a browser through the deployed
#              platform — the exact class `source-upload.spec.ts` exists to
#              guard, since a mocked test encodes the same wrong contract as
#              the code it tests (the 405 regression).
#
#              WHY SEPARATE FROM `run_k8s_system_tests.sh`. That script owns
#              one job — create a kind cluster, test, tear it down — and is
#              what CI needs. An operator has a cluster already, and
#              `overlays/dev` ALREADY sets `C2_DEMO_SEED_ENABLED=true` and
#              `C2_UX_DEV_MODE_ENABLED=true`, so the specs' preconditions are
#              met on a running dev stack with no kind involved. This script
#              is that path, and `run_k8s_system_tests.sh --ui` delegates to
#              it so the browser/port-forward logic lives in ONE place.
#
#              WHAT IT DOES NOT DO: it creates nothing and deletes nothing.
#              The cluster is yours; this only forwards a port, installs the
#              browser, and runs the specs.
#
#              PRECONDITIONS, asserted rather than assumed (the specs' own
#              `_global-setup.ts` checks the same three):
#                · a `c1-gateway` Service in the namespace,
#                · `/ux/config` answering through it,
#                · `auth_mode=local` with non-empty `dev_credentials` —
#                  i.e. a demo-seeded stack. Without the seed,
#                  `source-upload.spec.ts` authenticates as nobody and the
#                  project-scoped write answers 403, which is the failure
#                  that kept this tier unwired.
#
# Usage (from monorepo root):
#   ay_platform_core/scripts/run_ui_system_tests.sh
#   ay_platform_core/scripts/run_ui_system_tests.sh --port 18002
#   ay_platform_core/scripts/run_ui_system_tests.sh --namespace aywizz
#   ay_platform_core/scripts/run_ui_system_tests.sh -- --grep source-upload
#
# Exit codes:
#   0 specs passed · 1 specs failed or a precondition unmet · 2 bad usage
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MONOREPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
UI_ROOT="${MONOREPO_ROOT}/ay_platform_ui"

NAMESPACE="aywizz"
GATEWAY_SVC="c1-gateway"
GATEWAY_PORT=80
#: Distinct from the pytest k8s tier's 18001 and k8s_kind_smoke.sh's 18000
#: so all three can run on one machine without colliding.
LOCAL_PORT=18002
SKIP_BROWSER_INSTALL=0
PLAYWRIGHT_ARGS=()

while [ "$#" -gt 0 ]; do
    case "$1" in
        --namespace) NAMESPACE="$2"; shift ;;
        --port)      LOCAL_PORT="$2"; shift ;;
        --skip-browser-install) SKIP_BROWSER_INSTALL=1 ;;
        --) shift; PLAYWRIGHT_ARGS=("$@"); break ;;
        -h|--help) sed -n '4,50p' "$0"; exit 0 ;;
        *) echo "ERROR: unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

for tool in kubectl npm curl; do
    if ! command -v "${tool}" >/dev/null 2>&1; then
        echo "ERROR: required tool not on PATH: ${tool}" >&2
        exit 2
    fi
done

if ! kubectl get "svc/${GATEWAY_SVC}" -n "${NAMESPACE}" >/dev/null 2>&1; then
    echo "ERROR: no svc/${GATEWAY_SVC} in namespace ${NAMESPACE}." >&2
    echo "  Bring a stack up first:  infra/k8s/run.sh dev --ingress --workers" >&2
    echo "  Or point at another namespace with --namespace." >&2
    exit 1
fi

# -----------------------------------------------------------------------------
# Port-forward the GATEWAY, not the UI Service.
#
# `public/runtime-config.json` leaves `apiBaseUrl` empty, so `apiClient`
# emits RELATIVE paths and the browser must reach the pages and the API
# through ONE origin. Traefik is that origin: it routes `/` to the UI and
# `/auth/*`, `/api/*`, `/docs/*` to the components. Forwarding the UI
# Service instead would serve every page and 404 every API call — a failure
# mode that reads like a broken application rather than a broken harness.
# -----------------------------------------------------------------------------
echo "==> Port-forwarding ${NAMESPACE}/${GATEWAY_SVC} to localhost:${LOCAL_PORT}"
kubectl port-forward -n "${NAMESPACE}" "svc/${GATEWAY_SVC}" \
    "${LOCAL_PORT}:${GATEWAY_PORT}" >/dev/null 2>&1 &
PF_PID=$!

cleanup() {
    if [ -n "${PF_PID:-}" ]; then
        kill "${PF_PID}" 2>/dev/null || true
    fi
}
trap cleanup EXIT

BASE_URL="http://localhost:${LOCAL_PORT}"

echo "==> Waiting for ${BASE_URL}/ux/config"
ready=0
for _ in $(seq 1 60); do
    if curl -fsS "${BASE_URL}/ux/config" >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 2
done
if [ "${ready}" -ne 1 ]; then
    echo "ERROR: the gateway did not answer /ux/config within 120s." >&2
    echo "  Check:  kubectl get pods -n ${NAMESPACE}" >&2
    exit 1
fi

# The demo seed is the precondition that kept this tier unwired, so it is
# reported HERE — before a browser starts — rather than discovered as a 403
# inside a spec. A warning and not a failure: only the demo-seed specs need
# it, and `_global-setup.ts` draws the same line.
if ! curl -fsS "${BASE_URL}/ux/config" | grep -q '"dev_credentials"'; then
    echo "WARNING: /ux/config returns no dev_credentials." >&2
    echo "  demo-seed-login.spec.ts will fail, and any project-scoped" >&2
    echo "  write will answer 403. Deploy an overlay with" >&2
    echo "  C2_DEMO_SEED_ENABLED=true and C2_UX_DEV_MODE_ENABLED=true" >&2
    echo "  (overlays/dev, or overlays/ui-system-test on kind)." >&2
fi

if [ "${SKIP_BROWSER_INSTALL}" -eq 0 ]; then
    echo "==> Installing the Playwright browser (chromium)"
    # `--with-deps` is deliberately NOT passed: it apt-gets as root, which a
    # devcontainer user cannot do. On a CI runner the OS libraries are
    # already present; in a devcontainer a missing one fails loudly at
    # browser launch rather than being masked by a failed install here.
    npm --prefix "${UI_ROOT}" exec -- playwright install chromium
fi

echo "==> Running the Playwright system specs against ${BASE_URL}"
cd "${UI_ROOT}"
SYSTEM_STACK_BASE_URL="${BASE_URL}" npm run test:system -- "${PLAYWRIGHT_ARGS[@]}"

echo "==> UI system specs passed"
