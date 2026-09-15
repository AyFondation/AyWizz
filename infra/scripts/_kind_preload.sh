#!/usr/bin/env bash
# =============================================================================
# File: _kind_preload.sh
# Version: 2
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
#              CORRECTION (2026-09-15): this header previously read that
#              message as "the anonymous pull limit on a shared runner IP".
#              That was wrong. The message meant what it said — the MinIO
#              repositories are no longer publicly pullable from Docker Hub.
#              Proof: the same `pull access denied for minio/minio` now
#              reproduces on a developer workstation with no rate limit and
#              on every tag, old and new, while arangodb / n8nio / ollama /
#              traefik pull anonymously from the same host. The platform's
#              MinIO images therefore moved to quay.io, where both are
#              published. The helper itself is still worth having: on
#              v0.1.0-beta.14 both kind jobs died with
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
            echo "       Read the daemon's message ABOVE before assuming a" >&2
            echo "       rate limit — the two failure modes look alike and" >&2
            echo "       have opposite fixes:" >&2
            echo "         'toomanyrequests' / 'rate limit'  -> anonymous pull" >&2
            echo "            quota on a shared CI IP. Fix: docker/login-action." >&2
            echo "         'pull access denied' / 'repository does not exist'" >&2
            echo "            -> the repository is GONE or no longer public." >&2
            echo "            Fix: another registry, or mirror into GHCR." >&2
            echo "       This distinction is not academic: the MinIO images" >&2
            echo "       hit the SECOND case (minio/* left public Docker Hub)," >&2
            echo "       and this message previously asserted the first — which" >&2
            echo "       sent the v0.1.0-beta.14 diagnosis after a credential" >&2
            echo "       that would never have helped." >&2
            return 1
        fi
        kind load docker-image "${img}" --name "${cluster}"
    done
    echo "==> Third-party images loaded"
}
