# Session — Ollama default + C5 import + UI scaffold + traceability back-fill

**Date:** 2026-04-24

## Outcomes

### Traceability back-fill (prerequisite)
- Fixed orphan reference `E-100-012` → `E-100-002` in `c9_mcp/tools/c5_tools.py`.
- Back-filled `@relation` markers in 22 previously-unmarked modules:
  - 5 `config.py` files (C2, C4, C5, C7, C9) cite `R-100-111` + `R-100-112`.
  - 6 `main.py` app factories cite `R-100-114`.
  - 2 `null_publisher.py` modules use the `@relation ignore-module` sentinel with documented rationale (D-008 — NATS deferred).
  - `c7_memory/retrieval/similarity.py` cites `R-400-011`.
  - `c9_mcp/models.py`, `remote.py`, `tools/base.py` cite `R-100-015`.
- Before: 227 markers, 1 orphan, 22 missing modules. After: 251 markers, 0 orphan, 0 missing, **125 distinct entity references in src/**.

### Pass 1 — Ollama as default embedder
- `tests/docker-compose.yml` v7: new `ollama` service + `ollama_model_seed` one-shot pulling `all-minilm`. C7 depends on `ollama_model_seed` completion.
- `.env.test`: flipped `C7_EMBEDDING_ADAPTER` from `deterministic-hash` to `ollama`, `C7_EMBEDDING_DIMENSION=384`, `C7_EMBEDDING_MODEL_ID=all-minilm`.
- `tests/integration/c7_memory/conftest.py` v2: `c7_embedder` now yields a real `OllamaEmbedder` via the `ollama_container` fixture (probes dimension at fixture setup). The deterministic hash embedder stays available as `c7_deterministic_embedder` for tests that need vector reproducibility.
- `test_storage_verified.py`: dimension + model_id assertions now read runtime values from the embedder fixture instead of hardcoded `64` / `"deterministic-hash"`.
- C7 integration tests remain green against real Ollama semantics.

### Pass 2 — C5 bulk import endpoint (R-300-080..083)
- `c5_requirements/models.py`: new `ImportDocument`, `ImportRequest`, `ImportConflictMode`, `ImportSummary`, `ImportReport`.
- `c5_requirements/service.py`: new `import_corpus(project_id, payload, actor)` method. Three-phase algorithm: parse+validate all → conflict check → sequential write with partial-write rollback report on mid-batch failure.
- `c5_requirements/router.py`: `POST /api/v1/projects/{pid}/requirements/import` wired to the service, requires `project_editor`/`project_owner`/`admin`. Query params: `format=md|reqif` (reqif returns 501), `on_conflict=fail|replace`. Returns 201 with `ImportReport`.
- `tests/integration/c5_requirements/test_import.py`: 7 tests covering happy path, 409 on existing-slug with default mode, replace overwrite, 422 on malformed document (atomic batch abort), 422 on slug/frontmatter mismatch, 403 without editor role, 501 on `format=reqif`.
- Removed obsolete `test_import_still_deferred_to_v2` from `test_crud_flow.py` (§10.4 case B — test was expressing the pre-v1.5 contract).

### Pass 3 — `ay_platform_ui/` scaffold
New top-level directory `ay_platform_ui/` (user ratified earlier for this specific dir). Minimum-viable Next.js 15 + React 19 + Tailwind v4 + Biome + TypeScript scaffold:
- `package.json`, `tsconfig.json`, `next.config.ts` (rewrites `/auth/*` + `/api/platform/*` to `NEXT_PUBLIC_PLATFORM_BASE_URL`), `postcss.config.mjs`, `biome.json`, `.env.example`, `.gitignore`.
- `app/layout.tsx`, `app/globals.css` (Tailwind v4 CSS-first config with `@theme`), `app/page.tsx` (server-rendered landing page fetching `/auth/config` through the gateway).
- `lib/platform.ts`: `fetchAuthConfig()` helper, typed response, graceful degradation when gateway unreachable.

**Scope constraint**: NO chat UI, NO requirements UI, NO auth flow — just the framework + a landing page that proves the UI → Traefik → C2 path lights up. Feature work remains gated on end-to-end server stack validation per earlier session directive.

## Audit

- **739 tests** (+11 from previous), coverage **90.75%** (stable — new C5 import code covered by new tests).
- mypy + ruff green.
- `.env.example` + `.env.test` still in key-lockstep (completeness coherence test green).
- 91 config-override contract tests green (including 2 new for `C7_EMBEDDING_OLLAMA_URL` + `C7_EMBEDDING_OLLAMA_TIMEOUT_S`).

## Next

User-directed. Natural continuations:
- Exercise the full compose stack (`./scripts/e2e_stack.sh full`) now that Ollama + C5 import + n8n workflow seed are all wired.
- UI: next layer (login flow via C2 local-mode + a basic requirements list page consuming C5).
- C15 sub-agent runtime.
- ReqIF import (R-300-080 second format, v2).
