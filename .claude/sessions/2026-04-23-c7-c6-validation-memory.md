# Session — C7 Memory + C6 Validation Pipeline Registry

**Date:** 2026-04-23
**Duration:** cross-session (continued after auto-compaction)

## Outcomes

- **C7 Memory Service** implemented end-to-end:
  - `400-SPEC-MEMORY-RAG.md` populated (v2, 28 entities R-400-*/E-400-*, 11 Q-400-*).
  - Module `c7_memory/`: config, models, deterministic-hash embedder (zero-dep baseline), fixed-window chunker, MIME-dispatching parser (PDF/image stubs), ArangoDB repository, client-side cosine retrieval, service facade, 10 REST endpoints.
  - Unit + contract + integration tests against real ArangoDB testcontainer.

- **C6 Validation Pipeline Registry** implemented end-to-end:
  - `700-SPEC-VERTICAL-COHERENCE.md` populated (v2, 28 entities R-700-*/E-700-*/Q-700-*).
  - Module `c6_validation/`: plugin Protocol via `describe()`, process-global registry (build-time import discovery), built-in `code` domain plugin with 9 MUST checks (5 real: `req-without-code`, `code-without-requirement`, `test-absent-for-requirement`, `orphan-test`, `obsolete-reference` + 4 stubs: `interface-signature-drift`, `version-drift`, `data-model-drift`, `cross-layer-coherence`), ArangoDB repository (c6_runs, c6_findings), MinIO snapshot store (`validation-reports/<project>/<run>.json`), service facade with in-process async execution, 7 REST endpoints.
  - 80 tests green (46 unit + 10 contract + 17 integration w/ real containers + 7 others).

## Decisions

- **v1 plugin discovery = build-time import**. Runtime hot-reload deferred to v2 (R-700-002).
- **Plugin Protocol exposes metadata via `describe() -> PluginDescriptor`** rather than bare attributes. Keeps parallel-definition coherence check happy: plugin shape lives in exactly one place (the PluginDescriptor Pydantic contract).
- **v1 run execution = in-process `asyncio.create_task`**. NATS/worker queue deferred to v2 (R-700-011).
- **Check configurability = env-var per check**: `C6_CHECK_<UPPER>_ENABLED=false` to skip. Per-project config deferred (Q-700-002).
- **C7 embedder = deterministic-hash-v1** (zero dep, reproducible for tests). sentence-transformers + OpenAI adapters behind optional extras.
- **C7 vector storage = ArangoDB document collection + client-side cosine**. Native vector index (experimental in ArangoDB 3.12) skipped for v1.

## Bugs caught & fixed in-session

- **C5 yaml enum serialisation bug** — surfaced by a new C5 integration test (`test_patch_entity_status_bumps_version`). `yaml.safe_dump` cannot represent `StrEnum` instances. Fix: `payload.model_dump(exclude_none=True, mode="json")` in `c5_requirements/service.py:314`.
- **C7 deprecated Starlette status** — `HTTP_413_REQUEST_ENTITY_TOO_LARGE` → `HTTP_413_CONTENT_TOO_LARGE`.
- **C7 history filter** — default AQL only covered `status == 'active' OR @include_deprecated`; `superseded` was silently dropped. Added explicit `(c.status == 'superseded' AND @include_history)` clause.

## Coverage

- Pre-C6: 82.64% global.
- Post-C6 + targeted C5/C2 coverage boosts: **90.04% global**, 544 tests passing, mypy + ruff clean.
- Files below 80% per file: `c4_orchestrator/service.py` 77.08%, `c4_orchestrator/dispatcher/in_process.py` 76.92%, `c5_requirements/service.py` 76.54%, `c5_requirements/storage/minio_storage.py` 77.78%, `c7_memory/service.py` 77.93%, `c6_validation/storage/minio_storage.py` 74.47% — all within the non-blocking-per-file band (CLAUDE.md §11.1).

## Next

C9 MCP Server — thin wrapper over C5 + C6 APIs. No dedicated spec to populate.
