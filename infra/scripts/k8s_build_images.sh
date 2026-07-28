#!/usr/bin/env bash
# =============================================================================
# File: k8s_build_images.sh
# Version: 2
# Path: infra/scripts/k8s_build_images.sh
# Description: Build the two platform tier images and tag them exactly as the
#              K8s overlays reference them, so a LOCAL cluster that shares the
#              host Docker image store (docker-desktop) can run them with
#              `imagePullPolicy: IfNotPresent` — no registry, no pull, no auth.
#
#              Wrapper around the denied `docker build` (per
#              `.claude/settings.json`); the wrapper is the explicit, auditable
#              entry point per CLAUDE.md §5.3.
#
#              Images (match the K8s manifests' `image:` refs):
#                ghcr.io/ayfondation/aywizz-api:<tag>  <- infra/docker/Dockerfile.api
#                ghcr.io/ayfondation/aywizz-ui:<tag>   <- infra/docker/Dockerfile.ui
#                ghcr.io/ayfondation/aywizz-c13-extractor:<tag> (opt-in, --c13)
#                     <- infra/c13_extractor/docker/Dockerfile
#
#              Usage (from the monorepo root):
#                infra/scripts/k8s_build_images.sh                # both, :latest
#                infra/scripts/k8s_build_images.sh --api-only
#                infra/scripts/k8s_build_images.sh --ui-only
#                infra/scripts/k8s_build_images.sh --tag dev-1
#
#              NOTE for `kind` clusters: kind does NOT share the host image
#              store — use `infra/scripts/k8s_kind_smoke.sh`, which builds AND
#              `kind load`s its own `:test` tags.
#
#              Typical local-K8s sequence:
#                infra/scripts/k8s_build_images.sh
#                infra/k8s/run.sh dev --crds --wait
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MONOREPO_ROOT="$(cd "${INFRA_ROOT}/.." && pwd)"

DOCKERFILE_API="${INFRA_ROOT}/docker/Dockerfile.api"
DOCKERFILE_UI="${INFRA_ROOT}/docker/Dockerfile.ui"
DOCKERFILE_C13="${INFRA_ROOT}/c13_extractor/docker/Dockerfile"

IMAGE_API="ghcr.io/ayfondation/aywizz-api"
IMAGE_UI="ghcr.io/ayfondation/aywizz-ui"
IMAGE_C13="ghcr.io/ayfondation/aywizz-c13-extractor"

TAG="latest"
# Default tier = api + ui (always deployed). C13 is opt-in (per-overlay
# extractor, R-100-125) — build it only when asked.
BUILD_API=1
BUILD_UI=1
BUILD_C13=0

usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

Builds the platform images with the tags the K8s overlays expect.
Default (no flag): api + ui. C13 is opt-in.

Options:
  --tag <tag>   image tag to build (default: latest — what overlays/dev pins)
  --c13         ALSO build the C13 extractor image
  --api-only    build only ${IMAGE_API}
  --ui-only     build only ${IMAGE_UI}
  --c13-only    build only ${IMAGE_C13}
  -h, --help    this message
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --tag)
            [ "$#" -ge 2 ] || { echo "error: --tag needs a value" >&2; exit 2; }
            TAG="$2"; shift 2 ;;
        --c13)      BUILD_C13=1; shift ;;
        --api-only) BUILD_API=1; BUILD_UI=0; BUILD_C13=0; shift ;;
        --ui-only)  BUILD_API=0; BUILD_UI=1; BUILD_C13=0; shift ;;
        --c13-only) BUILD_API=0; BUILD_UI=0; BUILD_C13=1; shift ;;
        *) echo "error: unknown option '$1'" >&2; usage >&2; exit 2 ;;
    esac
done

if [ "${BUILD_API}" -eq 0 ] && [ "${BUILD_UI}" -eq 0 ] && [ "${BUILD_C13}" -eq 0 ]; then
    echo "error: nothing selected to build" >&2
    exit 2
fi

if [ "${BUILD_API}" -eq 1 ]; then
    [ -f "${DOCKERFILE_API}" ] || { echo "error: missing ${DOCKERFILE_API}" >&2; exit 1; }
    echo "==> Building ${IMAGE_API}:${TAG}"
    docker build -t "${IMAGE_API}:${TAG}" -f "${DOCKERFILE_API}" "${MONOREPO_ROOT}"
fi

if [ "${BUILD_UI}" -eq 1 ]; then
    [ -f "${DOCKERFILE_UI}" ] || { echo "error: missing ${DOCKERFILE_UI}" >&2; exit 1; }
    echo "==> Building ${IMAGE_UI}:${TAG}"
    docker build -t "${IMAGE_UI}:${TAG}" -f "${DOCKERFILE_UI}" "${MONOREPO_ROOT}"
fi

if [ "${BUILD_C13}" -eq 1 ]; then
    [ -f "${DOCKERFILE_C13}" ] || { echo "error: missing ${DOCKERFILE_C13}" >&2; exit 1; }
    # C13 vendors ay_extractor/ from the monorepo root (D-020 §3); the image
    # stamps the source revision via MONOREPO_GIT_SHA (falls back to 'local').
    GIT_SHA="$(git -C "${MONOREPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo local)"
    echo "==> Building ${IMAGE_C13}:${TAG} (sha ${GIT_SHA})"
    docker build -t "${IMAGE_C13}:${TAG}" -f "${DOCKERFILE_C13}" \
        --build-arg "MONOREPO_GIT_SHA=${GIT_SHA}" "${MONOREPO_ROOT}"
fi

echo "==> Done. Images available to any cluster sharing this Docker store:"
if [ "${BUILD_API}" -eq 1 ]; then
    echo "      ${IMAGE_API}:${TAG}"
fi
if [ "${BUILD_UI}" -eq 1 ]; then
    echo "      ${IMAGE_UI}:${TAG}"
fi
if [ "${BUILD_C13}" -eq 1 ]; then
    echo "      ${IMAGE_C13}:${TAG}"
fi
echo "    Next: infra/k8s/run.sh dev --crds --wait"
