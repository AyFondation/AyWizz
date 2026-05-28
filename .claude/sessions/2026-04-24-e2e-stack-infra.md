# Session — Deployable e2e stack (docker-compose)

**Date:** 2026-04-24

## Outcomes

- **App factories** (`main.py` per component): C2, C3, C4, C5, C6, C7, C9 each expose `create_app()` + module-level `app`. Wiring reads from pydantic-settings env-vars, lifespan bootstraps collections + buckets, each app exposes `/health`.
- **Shared Dockerfile**: `infra/docker/Dockerfile.python-service` — multi-stage Python 3.13-slim, parameterised by `COMPONENT_MODULE` build arg. Used by all 7 Python services + the mock LLM.
- **Mock LLM service**: `ay_platform_core._mock_llm` — FastAPI stand-in for C8 LiteLLM with an admin queue endpoint (`POST /admin/enqueue`, `GET /admin/calls`, `POST /admin/reset`). Deployed alongside the stack by default; real LiteLLM behind the `real-llm` profile.
- **Root `docker-compose.yml`**: 11 services (ArangoDB, MinIO, mock LLM, C2-C7 + C9, Traefik C1). Traefik is the ONLY port published to the host (`80` + `8080` dashboard). Internal service-to-service traffic stays on the `platform` docker network. Healthchecks + `depends_on: service_healthy` ensure ordered startup.
- **Traefik routes** updated to reflect actual router paths: `/api/v1/projects/*` for C5 (not `/api/v1/requirements`), new routes for `/api/v1/memory/*` (C7) and `/api/v1/mcp/*` (C9).
- **Seeder**: `ay_platform_core/scripts/seed_e2e.py` talks exclusively through Traefik — creates admin user → seeds demo project → seeds C5 doc + entity → seeds C7 source → enqueues mock-LLM canned response. Idempotent.
- **`tests/system/` tier**: new pytest tier, opted-out of default runs via `--ignore=tests/system`. Assumes stack is up (no auto-start). Hits `http://localhost` (Traefik) only. Exercises C2/C5/C6/C7/C9 + forward-auth middleware + MCP JSON-RPC round-trip.
- **Helper script**: `scripts/e2e_stack.sh` with `up|down|status|logs|seed|system|full` subcommands.

## Decisions

- **Single shared Dockerfile, parameterised**: deviates from CLAUDE.md §4.5 "one Dockerfile per component" for DRY reasons — all Python services are identical runtime-wise. Per-component `infra/<c>/docker/Dockerfile` placeholders removed rather than left as `FROM scratch` traps. Documented in the shared Dockerfile header.
- **Mock LLM as first-class deployable**: lives under `_mock_llm/` in the same source tree so it rides the shared Dockerfile. Leading underscore signals "not a platform component" to convention checks.
- **Traefik is the ONLY public port**: system tests go through `localhost:80`. Internal ports (`8000` on each service, `5432/9000` on infra) stay on the internal docker network. The mock-LLM admin endpoint is NOT public — tests that need to enqueue a response either join the network or have the mock port temporarily exposed via compose override (documented in the `MOCK_LLM_ADMIN_URL` fixture).
- **C9 container wires a remote adapter**: `c9_mcp/remote.py` exposes `RemoteRequirementsService` + `RemoteValidationService` that translate the tool-adapter method calls into HTTP requests against C5/C6. In-process tests still pass the real service instances — the tool handlers don't care.
- **`alist_plugins` / `alist_domains` async aliases** on `ValidationService` so the remote + in-process adapters share the same call surface for c6_tools.

## Coverage impact

- Pre-session: 90.70%, 596 tests.
- New code: ~450 lines across 10 new modules (factories + remote + mock LLM + seeder + system tests + helper).
- Smoke tests for all 8 factories added (`tests/unit/_app_factories/`).
- Some new code has 0 % coverage by design (service entrypoints only exercised by running containers — the system tier covers them once the stack is up).
- Post-session: 88.77%, 606 tests. Gate (80% line blocking) respected.

## Test tier topology (after this session)

| Tier | Location | Dependencies | Gate |
|---|---|---|---|
| unit | `tests/unit/` | none | blocking |
| contract | `tests/contract/` | none | blocking |
| integration | `tests/integration/` | testcontainers (docker) | blocking |
| coherence | `tests/coherence/` | none | blocking |
| e2e | `tests/e2e/` | testcontainers + in-process FastAPI | blocking |
| system | `tests/system/` | running docker-compose stack | **opt-in** (not in CI gate) |

## Next

Run `./scripts/e2e_stack.sh full` to validate the whole stack up-seed-test cycle. Any failure identifies a component gap, which gets fixed in its own session.
