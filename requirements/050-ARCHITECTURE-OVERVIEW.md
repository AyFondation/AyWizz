---
document: 050-ARCHITECTURE-OVERVIEW
version: 5
path: requirements/050-ARCHITECTURE-OVERVIEW.md
language: en
status: draft
audience: any-fresh-session, contributor-onboarding
---

# Architecture Overview — Read this first

> **Purpose.** A one-page snapshot of *how the platform is shaped today*,
> intended as the entry point for a fresh Claude Code session, a new
> contributor, or anyone who needs the load-bearing facts without
> wading through the full spec corpus.

> **This document is a summary.** It is authoritative on **shape**
> (topology, naming, conventions) but defers to the numbered specs
> (`100-`, `200-`, …) for the detailed normative requirements. Each
> bullet links to its source-of-truth requirement(s).

---

## 1. The big picture in one diagram

```
                         External clients
                                 │
                                 ▼
                  ┌──────────────────────────────┐
                  │   C1 — Reverse proxy         │   only public port: 80
                  │   (Traefik v3)               │   R-100-115
                  └──────────┬───────────────────┘
                             │ forward-auth via C2
              ┌──────────────┼──────────────┐
              ▼                              ▼
    ┌──────────────────┐           ┌─────────────────────┐
    │   API tier       │           │   UI tier (future)  │
    │                  │           │                     │
    │  C2 Auth         │           │  Next.js / React    │
    │  C3 Conversation │           │  Dockerfile.ui      │
    │  C4 Orchestrator │           │  (not yet present)  │
    │  C5 Requirements │           └─────────────────────┘
    │  C6 Validation   │
    │  C7 Memory       │
    │  C8 LLM Gateway  │           ▲ R-100-117 (tier-Dockerfiles)
    │  C9 MCP Server   │
    │                  │
    │  ALL FROM ONE    │
    │  IMAGE:          │
    │  ay-api:local    │
    │  Dockerfile.api  │
    │                  │
    │  ▶ N containers, │
    │    differ only   │
    │    by env var    │
    │    COMPONENT_    │
    │    MODULE        │
    │  R-100-114 v2    │
    └────────┬─────────┘
             │
             ▼
    ┌────────────────────────────────────────────────┐
    │   Backend tier (off-the-shelf images)          │
    │                                                │
    │   C10 MinIO (objects)    C11 ArangoDB (graph) │
    │   C12 n8n (workflows)    Ollama (embeddings) │
    │                                                │
    │   Plus test-tier sidecars (NOT in production): │
    │   _mock_llm   (scripted LLM responses)        │
    │   _observability (live log aggregator)        │
    │   R-100-120, R-100-121                        │
    └────────────────────────────────────────────────┘
```

**Observability runs through the platform itself** (R-100-104 v2 +
R-100-105 v2): a separate, production-grade module
`ay_platform_core/observability/` (no underscore) provides every
component with:

- structured **JSON logs** (one JSON object per line, mandatory
  fields: `timestamp`, `component`, `severity`, `trace_id`,
  `span_id`, `parent_span_id`, `tenant_id`, `message`),
- **W3C Trace Context** propagation via the shared
  `TraceContextMiddleware` (FastAPI) and the
  `make_traced_client(...)` factory (`httpx`),
- one **`event=span_summary`** record per request, carrying
  `method`, `path`, `status_code`, `duration_ms`, `parent_span_id`
  — the foundation for the upcoming workflow envelope synthesiser
  (Q-100-014).

Three logical tiers, top-down:

1. **Ingress** — single Traefik instance owns the public surface
   (R-100-115). All inter-component traffic from outside the cluster
   transits this edge.
2. **Application** — split in two parallel sub-tiers:
   - **API**: Python FastAPI components (C2–C9 + the test-only
     `_mock_llm` and `_observability` helpers), all packaged from
     **one** shared image, differentiated at runtime by
     `COMPONENT_MODULE`.
   - **UI**: future Next.js front-end (`ay_platform_ui/`, scaffold
     not yet present).
3. **Backend** — storage and dependency services (C10 MinIO, C11
   ArangoDB, C12 n8n, Ollama). Off-the-shelf images, no maintained
   Dockerfile under `infra/`.

---

## 2. The Python tier in 60 seconds

**One package, N processes.**

- `ay_platform_core/` is a single Python package containing every
  FastAPI sub-app (`c2_auth/main.py`, `c3_conversation/main.py`, …,
  `c9_mcp/main.py`, plus `_mock_llm/main.py` and
  `_observability/main.py`).
- Every sub-module exposes its own `app: FastAPI` via a `create_app()`
  factory.
- `infra/docker/Dockerfile.api` builds **one image** (`ay-api:local`)
  from `pyproject.toml`. The image's `CMD` is:
  ```
  exec uvicorn "ay_platform_core.${COMPONENT_MODULE}.main:app" \
       --host 0.0.0.0 --port 8000
  ```
- At runtime, every container sets `COMPONENT_MODULE` to the module
  whose `app` it should serve. **No build-arg, no per-component
  image, no `--reload` baked in** (the dev compose overrides
  `command:` to add `--reload`).

This is **not microservices** (no per-component codebase, no per-
component CI), and **not a monolith** (still N processes, N
containers, fault isolation, independent scaling). It is a
*monorepo of code + microservices of execution*.

References: R-100-114 v2, R-100-117, CLAUDE.md §4.5 ("tier
Dockerfiles").

---

## 3. Configuration — one env file, three credential classes

**One env file per environment**, no internal duplication. Variables
that are platform-wide-identical live unprefixed and are read by
every Settings class via `validation_alias`. Per-component knobs keep
the `C{N}_` prefix and only appear when the value legitimately
differs between components.

References: R-100-110 v2, R-100-111 v2, R-100-118 v2.

| File | Purpose | Editable by Claude |
|---|---|---|
| `.env.example` (monorepo root) | template + code-side defaults | yes |
| `ay_platform_core/tests/.env.test` | test-stack values | yes |
| `.env`, `.env.prod`, `.env.local`, `.env.secret` | dev / production secrets | **no** (CLAUDE.md §4.6 tier 2) |

**Credential classes** (R-100-118 v2):

| Class | Variables | Consumed by |
|---|---|---|
| (a) Backend bootstrap admin | `ARANGO_ROOT_USERNAME`, `ARANGO_ROOT_PASSWORD`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` | `arangodb` & `minio` Docker images at first boot, plus the `*_init` one-shots. Whitelisted in the env coherence test (no Pydantic Settings field). |
| (b) App runtime | `ARANGO_USERNAME=ay_app`, `ARANGO_PASSWORD`, `MINIO_ACCESS_KEY=ay_app`, `MINIO_SECRET_KEY` | every backbone component at runtime, via `validation_alias`. NEVER root. |
| (c) App admin | `C2_LOCAL_ADMIN_USERNAME`, `C2_LOCAL_ADMIN_PASSWORD` | C2 lifespan when `auth_mode=local`. Bootstraps an ADMIN user in `c2_users`. Ignored otherwise. |

**Bootstrap responsibility** lives in three init containers / lifespans:

- `arangodb_init` (compose one-shot) → creates `platform` DB and the
  `ay_app` user (rw on DB and on every collection).
- `minio_init` (compose one-shot) → creates the four buckets, declares
  the `ay-app-readwrite` policy, creates the `ay_app` user, attaches
  the policy.
- C2 lifespan `_ensure_local_admin()` → creates the application admin
  in `auth_mode=local` (idempotent).

**Tested**: `tests/integration/_credentials/` exercises both Arango
and MinIO bootstrap paths against testcontainers; positive (CRUD
works) and negative (foreign database / foreign bucket / wrong
password are rejected). C2 admin bootstrap is covered by
`tests/integration/c2_auth/test_local_admin_bootstrap.py`.

---

## 4. ArangoDB & MinIO — single namespaces, scoped by collection / bucket

R-100-012 v3:

- **All components share one ArangoDB database** (`platform` by
  convention). Isolation is enforced at the **collection** level:
  collections are prefixed by their owner (`c2_users`, `c2_tenants`,
  `c4_runs`, `c5_requirements`, `c7_chunks`, etc.) and the runtime
  user `ay_app` has `rw` on the database — but no component should
  reach into another's collections (rule enforced by code review +
  the dedicated user policy).
- **MinIO**: each component owns its own bucket
  (`orchestrator`, `requirements`, `validation`, `memory`, …). The
  `ay-app-readwrite` policy grants `s3:*` on the platform's buckets
  only; everything else is denied.

---

## 5. Resource limits — declared everywhere

R-100-106 v2 + R-100-119: every long-running container declares both
`limits` and `reservations`. One-shots are exempt. Baseline budget:

- **Internal tier** (C1–C9 + mock_llm): ≈ 3.5 vCPU / 4.3 GB peak.
  Cap: 4 vCPU / 8 GB.
- **Backend tier**: ArangoDB 1.5/1.5G, MinIO 0.5/0.5G, Ollama 2/2G,
  n8n 0.5/1G.
- **Total**: ≈ 7 vCPU / 9 GB peak. Cap: 8 vCPU / 16 GB platform-wide.

In Kubernetes, the same values map 1:1 onto `resources.requests`
(reservations) and `resources.limits` on each Deployment /
StatefulSet.

---

## 6. Test-tier observability

R-100-120 + R-100-121: a small `_observability` helper rides on the
shared `ay-api:local` image (`COMPONENT_MODULE=_observability`).
Subscribes to live Docker log streams from every `ay-*` container,
buffers per service in memory, exposes a small HTTP API on host
port `8002`:

- `GET /logs?service=&since=&min_severity=&limit=` — filtered tail
- `GET /errors?since=&limit=` — `min_severity=ERROR` shorthand
- `GET /digest` — counts per service per severity
- `GET /services` — services seen since startup
- `POST /clear` — drop the buffer

**Test-tier only**. R-100-121 forbids deploying any underscore-
prefixed module (`_mock_llm`, `_observability`) in staging or
production.

---

## 7. Compose stack — entry points

The deployable test stack lives at
`ay_platform_core/tests/docker-compose.yml`. The wrapper
`ay_platform_core/scripts/e2e_stack.sh` orchestrates:

```
e2e_stack.sh up        # build + start
e2e_stack.sh status    # docker compose ps
e2e_stack.sh down      # tear down + volumes
e2e_stack.sh seed      # inject test data (POST /auth/login etc.)
e2e_stack.sh system    # pytest tests/system/
e2e_stack.sh full      # up + seed + system
e2e_stack.sh logs <s>  # tail one service's logs
```

The wrapper passes `--env-file ay_platform_core/tests/.env.test` to
every Compose invocation so the bootstrap-admin credentials AND the
host-port scheme resolve.

**Default host ports** (R-100-122, `PORT_BASE=56000`):

| URL | Role |
|---|---|
| `http://localhost:56000` | Public API ingress (Traefik) — production-grade |
| `http://localhost:56080` | Traefik dashboard — dev only |
| `http://localhost:59800` | `_mock_llm` admin — test only |
| `http://localhost:59900` | `_observability` (logs, traces, workflows) — test only |

Deterministic offsets — `c5` direct (debug override) is **56500**,
`c9` direct is **56900**. Cn → `BASE + n*100` is the rule; "test"
sidecars take `BASE + 9000+`. Override `PORT_C1_PUBLIC` etc. in the
env file when 56xxx collides on a specific operator's machine — the
spec only requires the SCHEME to be respected, not the absolute base.

---

## 8. Where to look next

| Need to know about… | Read |
|---|---|
| Per-requirement implementation status (which `R-*` is implemented / tested / not-yet) | [060-IMPLEMENTATION-STATUS.md](060-IMPLEMENTATION-STATUS.md) (auto-generated) |
| Component decomposition + scaling + auth + topology | [100-SPEC-ARCHITECTURE.md](100-SPEC-ARCHITECTURE.md) |
| Pipeline & sub-agent orchestration | [200-SPEC-PIPELINE-AGENT.md](200-SPEC-PIPELINE-AGENT.md) |
| Requirements service (C5) data model + APIs | [300-SPEC-REQUIREMENTS-MGMT.md](300-SPEC-REQUIREMENTS-MGMT.md) |
| Memory / RAG / external sources | [400-SPEC-MEMORY-RAG.md](400-SPEC-MEMORY-RAG.md) |
| UI/UX intent | [500-SPEC-UI-UX.md](500-SPEC-UI-UX.md) (scaffold) |
| Code-quality engine (C6 plug-ins) | [600-SPEC-CODE-QUALITY.md](600-SPEC-CODE-QUALITY.md) (scaffold) |
| Vertical-coherence checks (C6 plugin contract) | [700-SPEC-VERTICAL-COHERENCE.md](700-SPEC-VERTICAL-COHERENCE.md) |
| LLM Gateway / LiteLLM config | [800-SPEC-LLM-ABSTRACTION.md](800-SPEC-LLM-ABSTRACTION.md) |
| Cross-cutting decisions (D-001…D-013) + roadmap | [999-SYNTHESIS.md](999-SYNTHESIS.md) |
| Conventions, workflow, file headers, hooks, … | `CLAUDE.md` (monorepo root) |
| Current state, recent decisions, next planned action | `.claude/SESSION-STATE.md` |
| Past sessions journal | `.claude/sessions/` |

---

## 9. What is implemented vs. specified — quick map

The platform's spec corpus has more requirements than today's code
honours. This map is **not** a substitute for a full spec ↔ code
audit (a separate workstream) but it tells you where the gaps are
big enough to matter for a fresh session.

| Spec area | State |
|---|---|
| C1–C9 backbone components | implemented; covered by unit + contract + integration tests |
| Single env file + validation_alias (R-100-110/111 v2) | implemented; covered by `tests/coherence/test_env_completeness.py` |
| Three credential classes (R-100-118 v2) | implemented; covered by `tests/integration/_credentials/` and `tests/integration/c2_auth/test_local_admin_bootstrap.py` |
| Resource limits (R-100-106 v2, R-100-119) | implemented in compose; K8s manifests TBD |
| Tier-Dockerfile (R-100-117) | `Dockerfile.api` implemented; `Dockerfile.ui` reserved |
| Test-tier observability (R-100-120) | implemented (`_observability/` v2 — Docker events subscription captures containers started after the collector itself) |
| Structured JSON logs (R-100-104 v2) | implemented — `ay_platform_core/observability/JSONFormatter`; every component emits JSON lines with mandatory fields + `event=span_summary` per request |
| W3C Trace Context (R-100-105 v2) | implemented — `TraceContextMiddleware` on all 8 components; `make_traced_client` injects `traceparent` on outbound httpx; tested front→back |
| Workflow envelope synthesiser (Q-100-014) | implemented — `_observability/synthesis.py` (pure functions, storage-agnostic) + `GET /workflows/<trace_id>` and `GET /workflows?recent=N` endpoints. Validated end-to-end on the live stack |
| K8s production synthesis (Q-100-015) | NOT implemented — synthesiser is portable; needs a Loki/ES adapter to replace the local ring buffer |
| Trace propagation into K8s Jobs / C15 sub-agents (Q-100-016) | NOT implemented — depends on C15 landing |
| Production K8s manifests (R-100-060) | NOT implemented for v1 — local compose only |
| C5 import endpoint (R-300-080) | stub returns 501 — v2 |
| C6 stubs #3 & #8 | not implemented — depend on machine-readable `E-*` specs |
| C15 sub-agent runtime | in-process dispatcher only; real K8s Jobs deferred |
| `ay_platform_ui/` (Next.js scaffold) | absent — after backend validation |

---

**End of 050-ARCHITECTURE-OVERVIEW.md v1.**
