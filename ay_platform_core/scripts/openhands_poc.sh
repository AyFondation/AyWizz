#!/usr/bin/env bash
# =============================================================================
# File: openhands_poc.sh
# Version: 2
# Path: ay_platform_core/scripts/openhands_poc.sh
# Description: Wrapper for the Q13 OpenHands generate-engine POC (V2 #2,
#              R-200-029 — still PROPOSED, not ratified). Encapsulates the
#              denied `docker compose` calls behind an explicit, auditable
#              entry point per CLAUDE.md §5.3, so the POC can be driven
#              without widening the allow-list to `docker compose` itself.
#
#              Wraps what `infra/docker/c15-runner-poc-runbook.md` documents:
#              the opt-in `c4_openhands` compose service (profile `openhands`),
#              built from `infra/docker/Dockerfile.c15-runner` — the `ay-api`
#              image PLUS `ay_platform_core[openhands]` (openhands-sdk +
#              openhands-tools) and its runtime tools (tmux, git). The shared
#              `ay-api` image stays free of that dependency tree.
#
#              COST IS THE POINT OF THE `--yes` GATE. This service routes every
#              LLM call through C8/LiteLLM to a REAL provider, and an agent loop
#              bills once per turn, not once per run. `up` therefore refuses to
#              start without `--yes`: neither an operator reflex nor an agent
#              re-running a command from scrollback can bill by accident.
#              `build`, `logs`, `status` and `down` cost nothing and need no
#              flag.
#
#              Prerequisites `up` checks before spending anything:
#                - `<monorepo>/.env.secret` exists and carries the provider key
#                  + `C8_GATEWAY_API_KEY` (the proxy holds the provider key ;
#                  without it every route 401s).
#                - The dev stack is up, since C4 needs a reachable C8 proxy:
#                  `ay_platform_core/scripts/e2e_stack.sh dev`.
#
#              Usage (from the monorepo root or ay_platform_core/):
#                scripts/openhands_poc.sh build
#                scripts/openhands_poc.sh up --yes
#                scripts/openhands_poc.sh logs
#                scripts/openhands_poc.sh status
#                scripts/openhands_poc.sh down
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUBPROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MONOREPO_ROOT="$(cd "${SUBPROJECT_ROOT}/.." && pwd)"
COMPOSE_DIR="${SUBPROJECT_ROOT}/tests"
COMPOSE_FILE="${COMPOSE_DIR}/docker-compose.yml"
# MANDATORY, not optional: `c4_openhands` takes its dev env_file list
# (`.env.dev` + optional `.env.secret`) from the override, and the `litellm`
# service — the C8 proxy this engine routes through — is DECLARED there and
# nowhere else. Omitting it yields a service with no gateway and no keys.
DEV_OVERRIDE="${COMPOSE_DIR}/docker-compose.dev.override.yml"
ENV_FILE="${COMPOSE_DIR}/.env.test"
SECRET_FILE="${MONOREPO_ROOT}/.env.secret"
SERVICE="c4_openhands"
PROFILE="openhands"
# The compose container name, NOT a substring of it. `docker ps | grep litellm`
# also matches the KUBERNETES pod (`k8s_litellm_litellm-...`), which would let
# `up` proceed against a proxy the compose network cannot reach.
C8_CONTAINER="ay-litellm"

usage() {
    cat <<EOF
Usage: $(basename "$0") <command> [--yes]

Commands:
  build     build the C15-runner image (ay-c15-runner:local). No cost.
  up        start the OpenHands generate engine. REQUIRES --yes — this
            bills a real provider on every agent turn.
  logs      follow the engine's logs. No cost.
  status    show whether the engine container is running. No cost.
  down      stop and remove the engine container. No cost.
  -h        this message

The POC's pass criteria live in infra/docker/c15-runner-poc-runbook.md §3.
EOF
}

compose() {
    # The denied call, encapsulated (CLAUDE.md §5.3). Not matched by the
    # permission layer because it runs as a sub-process of this wrapper.
    #
    # The file + env-file set MIRRORS `e2e_stack.sh dev` exactly. It has to:
    # compose derives the project (and therefore which containers belong
    # together) from this invocation, so a different `-f` set would build a
    # parallel project instead of joining the dev stack. `--profile litellm`
    # rides along so the engine's C8 proxy is part of the same project.
    local secret_arg=()
    if [ -f "${SECRET_FILE}" ]; then
        secret_arg=(--env-file "${SECRET_FILE}")
    fi
    docker compose \
        --env-file "${ENV_FILE}" \
        "${secret_arg[@]}" \
        -f "${COMPOSE_FILE}" \
        -f "${DEV_OVERRIDE}" \
        --profile litellm --profile "${PROFILE}" "$@"
}

require_secrets() {
    if [ ! -f "${SECRET_FILE}" ]; then
        echo "ERROR: ${SECRET_FILE} not found." >&2
        echo "       The engine routes through C8/LiteLLM, which needs the" >&2
        echo "       provider key + C8_GATEWAY_API_KEY. Without them every" >&2
        echo "       route 401s and the run proves nothing." >&2
        exit 2
    fi
    if ! grep -q "C8_GATEWAY_API_KEY" "${SECRET_FILE}"; then
        echo "ERROR: C8_GATEWAY_API_KEY missing from ${SECRET_FILE}." >&2
        exit 2
    fi
}

require_c8_running() {
    # Exact container name. A substring match on "litellm" also matches the
    # Kubernetes pod (`k8s_litellm_litellm-...`), which is on a different
    # network entirely — the check would pass while the engine had no
    # reachable gateway, and every turn would 401 after billing nothing
    # useful.
    if ! docker ps --format '{{.Names}}' | grep -qx "${C8_CONTAINER}"; then
        echo "ERROR: compose container '${C8_CONTAINER}' (C8 proxy) is not running." >&2
        echo "       A running Kubernetes LiteLLM pod does NOT count — different" >&2
        echo "       network. Start the compose dev stack first:" >&2
        echo "         ay_platform_core/scripts/e2e_stack.sh dev" >&2
        exit 2
    fi
}

cmd_build() {
    echo "==> Building ay-c15-runner:local (ay-api + openhands extra)"
    compose build "${SERVICE}"
    echo "==> Build OK. Nothing is running and nothing was billed."
}

cmd_up() {
    if [ "${1:-}" != "--yes" ]; then
        echo "REFUSED: 'up' bills a real provider on every agent turn." >&2
        echo "         Re-run with --yes once you accept that cost:" >&2
        echo "           $(basename "$0") up --yes" >&2
        exit 2
    fi
    require_secrets
    require_c8_running
    echo "==> Starting ${SERVICE} (C4_GENERATE_ENGINE=openhands)"
    echo "    Every generate turn now bills through C8. Stop with: $(basename "$0") down"
    compose up -d "${SERVICE}"
    echo "==> Up. Follow with: $(basename "$0") logs"
}

cmd_logs() { compose logs -f "${SERVICE}"; }

cmd_status() {
    if docker ps --format '{{.Names}}' | grep -q "ay-c4-orchestrator-openhands"; then
        echo "RUNNING — ay-c4-orchestrator-openhands (billing on each turn)"
    else
        echo "stopped"
    fi
}

cmd_down() {
    echo "==> Stopping ${SERVICE}"
    compose rm -sf "${SERVICE}"
    echo "==> Down."
}

case "${1:-}" in
    build)  cmd_build ;;
    up)     shift; cmd_up "$@" ;;
    logs)   cmd_logs ;;
    status) cmd_status ;;
    down)   cmd_down ;;
    -h|--help) usage ;;
    *) usage >&2; exit 2 ;;
esac
