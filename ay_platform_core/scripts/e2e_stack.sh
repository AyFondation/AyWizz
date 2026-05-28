#!/usr/bin/env bash
# =============================================================================
# File: e2e_stack.sh
# Version: 8
# Path: ay_platform_core/scripts/e2e_stack.sh
# Description: One-stop helper for the system-test stack.
#              Wraps `docker compose` + seed + `pytest tests/system/`.
#
#              v8 (2026-05-27): adds the `build` subcommand — builds the
#              platform images (ay-api:local + ay-ui:local) WITHOUT starting
#              any container, so a separate runtime (the local k8s overlay)
#              can consume them from the shared Docker image store.
#
#              v7 (2026-05-24): `dev` (the manual USER-TEST stack) now
#              starts the `litellm` profile — the LiteLLM proxy + C8 cost
#              receiver — so the platform matches production reality
#              (C8 → LiteLLM → Claude), per the operator decision. `up` /
#              `full` (the CI-ish flows) stay litellm-FREE so CI keeps
#              using mock_llm / Ollama and never bills a real provider.
#              `dev` REQUIRES `<monorepo>/.env.secret` with
#              ANTHROPIC_API_KEY + C8_GATEWAY_API_KEY ; absent → the proxy
#              starts but every route 401s. `status` is litellm-aware.
#
#              v5: `dev` subcommand now runs `seed_demo_ux.py` after
#              compose `up` so the UX demo sources (Phase C) are
#              ingested before the operator opens the browser. The
#              seeder polls /ux/config until ready (which transitively
#              waits for C2's lifespan demo seed) so we don't race.
#              v4: adds the `dev` subcommand for manual browser-driven
#              testing. `dev` layers `<monorepo>/.env.dev` on top of
#              `.env.test` (multiple --env-file, later overrides
#              earlier) and brings up the same compose stack with
#              the demo seed enabled (C2_DEMO_SEED_ENABLED=true) +
#              UX dev mode (C2_UX_DEV_MODE_ENABLED=true). The
#              tenant + 4 users + project + grants are seeded by
#              C2's lifespan ; no separate seed step needed.
#              v3: pass `--env-file <ENV_FILE>` to every `docker compose`
#              invocation so Compose's ${VAR} substitution reads from
#              the test env file (R-100-118 v2). Without this, the root
#              credentials referenced by the `arangodb` and `minio`
#              services would not resolve.
#              v2: moved from /workspace/scripts/ to
#              ay_platform_core/scripts/. Compose file lives alongside the
#              tests at ay_platform_core/tests/docker-compose.yml.
#
# Usage (from anywhere — this script resolves its own location):
#   ./ay_platform_core/scripts/e2e_stack.sh up        # build + start (test env)
#   ./ay_platform_core/scripts/e2e_stack.sh dev       # build + start (test+dev env, demo seed)
#   ./ay_platform_core/scripts/e2e_stack.sh down      # tear down + volumes
#   ./ay_platform_core/scripts/e2e_stack.sh seed      # inject test data
#   ./ay_platform_core/scripts/e2e_stack.sh system    # pytest tests/system/
#   ./ay_platform_core/scripts/e2e_stack.sh full      # up + seed + system
#   ./ay_platform_core/scripts/e2e_stack.sh status    # compose ps
#   ./ay_platform_core/scripts/e2e_stack.sh logs <svc> # tail service logs
# =============================================================================

set -euo pipefail

# Resolve absolute paths from the script's own location so the helper works
# regardless of the caller's cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AY_CORE="$(cd "$SCRIPT_DIR/.." && pwd)"                   # .../ay_platform_core
MONOREPO_ROOT="$(cd "$AY_CORE/.." && pwd)"                # .../<monorepo>
COMPOSE_FILE="$AY_CORE/tests/docker-compose.yml"
ENV_FILE="$AY_CORE/tests/.env.test"
DEV_OVERRIDE="$AY_CORE/tests/docker-compose.dev.override.yml"

STACK_BASE_URL="${STACK_BASE_URL:-http://localhost:56000}"

# When running inside a Docker container (devcontainer), `localhost`
# points at the container itself — not the host where the compose
# stack publishes its ports. Resolve the "internal" URL by rewriting
# localhost → host.docker.internal. The user-facing URL stays
# `localhost` for browser instructions (the user opens on the host).
_internal_url() {
  if [[ -f /.dockerenv ]]; then
    echo "${STACK_BASE_URL//localhost/host.docker.internal}"
  else
    echo "$STACK_BASE_URL"
  fi
}

_require_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker CLI not found on PATH" >&2
    exit 1
  fi
}

cmd_up() {
  _require_docker
  echo "==> Building images + starting stack"
  echo "    compose file: $COMPOSE_FILE"
  echo "    build ctx:    $MONOREPO_ROOT"
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --build
  echo "==> Stack is starting; services will report healthy shortly"
  echo "    Public API:        $STACK_BASE_URL          # R-100-122 BASE+0"
  echo "    Traefik dashboard: http://localhost:56080   # R-100-122 BASE+80"
  echo "    Mock-LLM admin:    http://localhost:59800   # R-100-122 BASE+9800 (test only)"
  echo "    Observability:     http://localhost:59900   # R-100-122 BASE+9900 (test only)"
}

cmd_build() {
  # Build the platform images (ay-api:local + ay-ui:local) WITHOUT starting
  # any container. Used to feed a separate runtime (e.g. the local k8s
  # overlay) the freshly-built images via the shared Docker image store.
  _require_docker
  echo "==> Building images only (no containers started)"
  echo "    compose:  $COMPOSE_FILE"
  echo "    override: $DEV_OVERRIDE  (defines the ay-ui build)"
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" -f "$DEV_OVERRIDE" build
  echo "==> Build OK : ay-api:local + ay-ui:local are in the local image store"
}

cmd_dev() {
  # Manual-test stack with demo seed + UX dev mode. Layers an override
  # compose file (`docker-compose.dev.override.yml`) on top of the
  # base compose ; the override adds `<monorepo>/.env.dev` to c2's
  # `env_file:` list. docker compose's env_file directive is REPLACED
  # (not merged) by overrides, so the override re-states both files.
  _require_docker
  local dev_env="$MONOREPO_ROOT/.env.dev"
  local dev_override="$AY_CORE/tests/docker-compose.dev.override.yml"
  if [[ ! -f "$dev_env" ]]; then
    echo "ERROR: $dev_env not found. Cannot start dev stack." >&2
    exit 1
  fi
  if [[ ! -f "$dev_override" ]]; then
    echo "ERROR: $dev_override not found. Cannot start dev stack." >&2
    exit 1
  fi
  # Build-version stamp = ISO timestamp. Compose reads it via
  # `${BUILD_VERSION:-dev}` in both api + ui build.args blocks, the
  # Dockerfiles forward it into a runtime ENV that C2 exposes through
  # /ux/config and Next.js bakes into the client bundle as
  # NEXT_PUBLIC_BUILD_VERSION. Visible in the UX footer to confirm a
  # rebuild actually shipped.
  export BUILD_VERSION="${BUILD_VERSION:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
  echo "==> Building images + starting DEV stack (demo seed enabled)"
  echo "    compose:   $COMPOSE_FILE"
  echo "    override:  $dev_override"
  echo "    env:       $ENV_FILE  +  $dev_env (via override env_file)"
  echo "    profile:   litellm (proxy + C8 cost receiver ; needs .env.secret)"
  echo "    version:   $BUILD_VERSION"
  docker compose \
    --env-file "$ENV_FILE" \
    -f "$COMPOSE_FILE" \
    -f "$dev_override" \
    --profile litellm up -d --build
  echo "==> Demo seed will run on C2 startup ; ready in ~10-20s"

  # Run the UX demo data seeder (Phase C+). Non-fatal — if the seed
  # fails (e.g. Ollama still warming), the operator can still test
  # auth + nav and re-run `seed_demo_ux.py` manually later.
  local internal_url
  internal_url="$(_internal_url)"
  echo "==> Waiting for stack readiness ($internal_url), then seeding UX demo data…"
  (cd "$AY_CORE" && \
    python scripts/seed_demo_ux.py --base-url "$internal_url" --timeout-s 180) \
    || echo "==> WARNING: demo-ux seed failed ; non-fatal, see logs above"

  echo ""
  echo "    Open: $STACK_BASE_URL    # login page surfaces 4 demo creds"
  echo ""
  echo "    Demo accounts (also visible on /login when stack is up) :"
  echo "      superroot       / dev-superroot   (tenant_manager super-root)"
  echo "      tenant-admin    / dev-tenant      (admin of tenant-test)"
  echo "      project-editor  / dev-editor      (editor on project-test)"
  echo "      project-viewer  / dev-viewer      (viewer on project-test)"
}

cmd_down() {
  _require_docker
  echo "==> Tearing down stack + volumes"
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" down -v --remove-orphans
}

cmd_status() {
  _require_docker
  # Include the dev override + `litellm` profile so the proxy + cost
  # receiver show up when the dev stack is running (harmless no-op for
  # the litellm-free `up`/`full` flows — compose ps only lists what runs).
  docker compose --env-file "$ENV_FILE" \
    -f "$COMPOSE_FILE" -f "$DEV_OVERRIDE" --profile litellm ps
}

cmd_logs() {
  _require_docker
  local service="${1:-}"
  if [[ -z "$service" ]]; then
    echo "usage: $0 logs <service-name>" >&2
    exit 2
  fi
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" logs -f "$service"
}

cmd_seed() {
  echo "==> Seeding test data via $STACK_BASE_URL"
  # Invoke seed_e2e.py as a script (not via `python -m`) — `scripts/`
  # is intentionally NOT a Python package (no __init__.py: it mixes
  # bash + Python), so `python -m ay_platform_core.scripts.seed_e2e`
  # raises ModuleNotFoundError. Direct script invocation is the
  # contract.
  (cd "$AY_CORE" && \
    STACK_BASE_URL="$STACK_BASE_URL" \
    python scripts/seed_e2e.py --base-url "$STACK_BASE_URL")
}

cmd_system() {
  echo "==> Running system tests against $STACK_BASE_URL"
  (cd "$AY_CORE" && \
    STACK_BASE_URL="$STACK_BASE_URL" \
    python -m pytest tests/system -v --no-cov)
}

cmd_full() {
  cmd_up
  echo "==> Waiting 5 s for images to settle..."
  sleep 5
  cmd_seed
  cmd_system
}

main() {
  local sub="${1:-}"
  case "$sub" in
    build)  cmd_build ;;
    up)     cmd_up ;;
    dev)    cmd_dev ;;
    down)   cmd_down ;;
    status) cmd_status ;;
    logs)   shift; cmd_logs "$@" ;;
    seed)   cmd_seed ;;
    system) cmd_system ;;
    full)   cmd_full ;;
    "")
      echo "usage: $0 {build|up|dev|down|status|logs <svc>|seed|system|full}" >&2
      exit 2
      ;;
    *)
      echo "unknown subcommand: $sub" >&2
      exit 2
      ;;
  esac
}

main "$@"
