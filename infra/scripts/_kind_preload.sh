#!/usr/bin/env bash
# =============================================================================
# File: _kind_preload.sh
# Version: 1
# Path: infra/scripts/_kind_preload.sh
# Description: Pull the third-party images an overlay needs on the RUNNER, then
#              `kind load` them into the cluster. Sourced, never executed
#              directly (leading underscore).
#
#              WHY. A kind node's containerd pulls on its own, anonymously, and
#              on a GitHub-hosted runner that now fails against Docker Hub:
#
#                failed to resolve reference "docker.io/minio/minio:RELEASE…":
#                pull access denied, repository does not exist or may require
#                authorization: server message: insufficient_scope:
#                authorization failed
#
#              The message reads like a missing repository; it is the anonymous
#              pull limit on a shared runner IP. The blast radius is what makes
#              it worth a helper: on v0.1.0-beta.14 both kind jobs died with
#              "timed out waiting for the condition on deployments/
#              c4-orchestrator" because minio never started, its Service had no
#              endpoints, `minio` stopped resolving, and every component whose
#              lifespan calls `ensure_bucket()` crash-looped. Four layers
#              between the cause and the symptom.
#
#              Pulling on the runner instead fixes it because the runner's
#              docker daemon is where a `docker/login-action` credential lands,
#              and because one pull replaces one-per-node. The scripts already
#              did exactly this for the platform images; it was only ever the
#              third-party ones that were left to the node.
#
#              THE LIST IS DERIVED, NEVER HARDCODED. It comes from the built
#              overlay, so adding a component or bumping a pinned tag needs no
#              edit here. A hardcoded list would silently rot and reintroduce
#              this failure for the next image added.
#
#              FAILS FAST AND LOUD. A pull that cannot succeed here is fatal on
#              the spot, with the registry's own message, rather than surfacing
#              five minutes later as a DNS error inside an application pod.
# =============================================================================

# Usage: preload_third_party_images <cluster-name> <overlay-path> [already-loaded-image ...]
preload_third_party_images() {
    local cluster="$1"
    local overlay="$2"
    shift 2
    local already=("$@")

    echo "==> Resolving third-party images from ${overlay}"
    local images
    images=$(kubectl kustomize "${overlay}" \
        | awk '/^[[:space:]]*image:[[:space:]]/ {print $2}' \
        | tr -d '"' | sort -u)

    local img skip s
    for img in ${images}; do
        skip=0
        for s in "${already[@]}"; do
            if [ "${img}" = "${s}" ]; then
                skip=1
            fi
        done
        if [ "${skip}" -eq 1 ]; then
            continue
        fi
        echo "    pulling ${img}"
        if ! docker pull "${img}"; then
            echo "ERROR: could not pull ${img} on the runner." >&2
            echo "       Anonymous Docker Hub pulls are rate-limited on shared" >&2
            echo "       CI IPs. Add a docker/login-action step with Docker Hub" >&2
            echo "       credentials, or mirror this image into GHCR." >&2
            return 1
        fi
        kind load docker-image "${img}" --name "${cluster}"
    done
    echo "==> Third-party images loaded"
}
