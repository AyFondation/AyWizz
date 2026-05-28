#!/usr/bin/env bash
# =============================================================================
# File: docker_test_cleanup.sh
# Version: 1
# Path: ay_platform_core/scripts/docker_test_cleanup.sh
# Description: Stops and removes orphaned Docker containers spawned by
#              testcontainers (ArangoDB / MinIO / Loki / Elasticsearch /
#              Ollama). Use after a pytest run that was killed (timeout,
#              SIGTERM, ctrl-C) where the `with container:` teardown
#              didn't get a chance to run.
#
#              Targets ONLY containers whose image starts with the known
#              testcontainer-spawned set; never touches the devcontainer
#              itself or unrelated user containers.
#
# Usage:
#   ay_platform_core/scripts/docker_test_cleanup.sh [--dry-run]
#
#   With --dry-run, lists what WOULD be removed without taking action.
#
# Exit codes:
#   0: cleanup completed (or nothing to clean)
#   1: docker CLI missing
#   2: error during stop/remove
# =============================================================================

set -uo pipefail

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=1
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker CLI not on PATH" >&2
    exit 1
fi

# Image-prefix patterns we treat as testcontainer-spawned. Anything not
# matching these stays untouched.
PATTERNS=(
    "arangodb/arangodb"
    "minio/minio"
    "grafana/loki"
    "docker.elastic.co/elasticsearch"
    "ollama/ollama"
    "testcontainers/ryuk"
)

declare -a TARGETS=()
while read -r line; do
    [[ -z "$line" ]] && continue
    container_id="$(echo "$line" | awk '{print $1}')"
    image="$(echo "$line" | awk '{print $2}')"
    for pattern in "${PATTERNS[@]}"; do
        if [[ "$image" == "$pattern"* ]]; then
            TARGETS+=("$container_id|$image")
            break
        fi
    done
done < <(docker ps -a --format "{{.ID}} {{.Image}}")

if [[ ${#TARGETS[@]} -eq 0 ]]; then
    echo "no orphaned testcontainer-spawned containers found"
    exit 0
fi

echo "found ${#TARGETS[@]} orphaned container(s):"
for entry in "${TARGETS[@]}"; do
    cid="${entry%%|*}"
    img="${entry##*|}"
    echo "  $cid  $img"
done

if [[ $DRY_RUN -eq 1 ]]; then
    echo "(dry-run; no action taken)"
    exit 0
fi

EXIT_CODE=0
for entry in "${TARGETS[@]}"; do
    cid="${entry%%|*}"
    if ! docker stop "$cid" >/dev/null 2>&1; then
        echo "WARN: failed to stop $cid (already stopped?)" >&2
    fi
    if ! docker rm -v "$cid" >/dev/null 2>&1; then
        echo "ERROR: failed to remove $cid" >&2
        EXIT_CODE=2
    else
        echo "removed $cid"
    fi
done

exit $EXIT_CODE
