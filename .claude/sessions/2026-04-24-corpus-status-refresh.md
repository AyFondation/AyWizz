# Session — Requirements corpus status refresh

**Date:** 2026-04-24

## Outcomes

Corpus alignment with the implemented state (post P1–P6 test & config
foundation). Three passes:

### Passe A — Status refresh (no new requirements)
- `CHANGELOG.md`: full rewrite, 3 dated sections (2026-04-22 initial
  scaffold, 2026-04-23/24 C1–C9 backbone, 2026-04-24 P1–P6 test &
  config foundation).
- `999-SYNTHESIS.md` §6 Document Mapping: `planned` → `delivered`
  status updates for 200-SPEC (v2), 400-SPEC (v2), 700-SPEC (v3),
  800-SPEC (v1). `meta/100-SPEC-METHODOLOGY.md` noted as v3.

### Passe B — 100-SPEC §10 Configuration & Deployment (7 new entities)
`100-SPEC-ARCHITECTURE.md` bumped to v3:

| ID | Subject |
|---|---|
| R-100-110 | Single `.env`-style file as source of truth; all variants share one key set |
| R-100-111 | `env_prefix="c<n>_"` naming convention per component |
| R-100-112 | `PLATFORM_ENVIRONMENT` cross-cutting (no prefix, via `validation_alias`) |
| R-100-113 | Completeness + override coherence tests pin Settings ↔ env-file bijection |
| R-100-114 | Shared `Dockerfile.python-service` + pyproject-driven deps + src bind-mount |
| R-100-115 | Traefik = only public host port (test-only ports explicitly marked) |
| R-100-116 | Mock LLM for CI + `real-llm` compose profile |

### Passe C — meta/100-SPEC-METHODOLOGY §13 Test Tier Topology (10 new entities)
`meta/100-SPEC-METHODOLOGY.md` bumped to v3:

| ID | Subject |
|---|---|
| R-M100-200 | Tier boundaries — what each tier may / may not import |
| R-M100-201 | Coherence tests SHALL stay pure-functional (no network / containers) |
| R-M100-202 | System tests opt-in via `--ignore=tests/system` |
| R-M100-210 | Filename conventions: `_real_chain`, `_real_llm`, `_storage_verified` |
| R-M100-220 | Testcontainers session-scoped by default; UUID-per-test isolation |
| R-M100-221 | Orphan-wipe at session start (crashed-run residue) |
| R-M100-222 | `cleanup_arango_database` / `cleanup_minio_bucket` helpers with retry+verify; no `contextlib.suppress` |
| R-M100-223 | `*_fresh` function-scoped variants for rare isolation cases |
| R-M100-230 | Test env files live under `ay_platform_core/tests/`, key-synced with `.env.example` |

## Verification

- All 16 coherence tests still pass.
- Full test suite: 728 tests, coverage 90.90%.
- YAML frontmatter of every touched file validates.
- Version bumps: `100-SPEC v2→v3`, `meta/100 v2→v3`. 999 v4 kept (§6
  refresh is cosmetic / table-level, no decision changed).

## Next

User-directed — the implementation-side next steps proposed earlier
remain: C15 sub-agent runtime, C5 import endpoint (R-300-080 v2
roadmap), `ay_platform_ui/` frontend (user gated on "server validated"),
or additional coverage on existing components.
