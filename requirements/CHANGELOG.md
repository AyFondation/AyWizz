# Changelog — Requirements Corpus

All notable changes to the requirements corpus in this directory are
documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Changes here track the **corpus evolution** (specs added, versions bumped,
entities introduced / superseded), per `meta/100-SPEC-METHODOLOGY.md` §10.

Per-release headings replace the `[Unreleased]` section at release time.

---

## [Unreleased]

### 2026-05-28 (later) — D-020 v2 optimisations (pre-strip spec amendments)

Following a critical-partner review of the D-020 v1 plan, five quality/cost
optimisations were validated by the operator and landed as spec amendments
BEFORE the session 2 strip starts. All are mandatory unless explicitly
tagged opt-in.

**Changed (semantic — version bumps)**
- `999-SYNTHESIS.md` v7 → **v8** — **D-020 v1 → v2** absorbs the
  optimisations. New mandatory sub-section "v2 optimisations" listing
  (A) 2-tier LLM gating for the decontextualiser via a Haiku screener,
  (B) mandatory `cache_control` prompt-marker structure for sliding-
  window agents, (C) mandatory intra-document image deduplication,
  (D) embeddings now produced by C13 (not C7), (E) opt-in batch API
  mode via `urgency: interactive|background`. Q-200-028 opens
  adaptive Phase 1 deferral.
- `100-SPEC-ARCHITECTURE.md` v14 → **v15** — **R-100-125 v1 → v2**
  §2 HTTP surface gains the `urgency` parameter; §5 LLM frugality
  gains mandatory image dedup + screener gating + prompt-caching
  structure; §6 storage isolation extended to embeddings (C13 writes
  `embeddings.jsonl` via C8 `/embeddings`).
- `400-SPEC-MEMORY-RAG.md` v6 → **v7**:
  - **R-400-220 v1 → v2** — artifact layout adds
    `02_chunks/embeddings.jsonl` (D), `02_chunks/screener_log.jsonl`
    (A), `urgency` field in `status.json` (E), image filenames use
    `img_{sha8}.json` for dedup (C).
  - **R-400-221 v1 → v2** — `RunManifest` adds `embedding_model` /
    `embedding_model_version` / `embedding_dimension` /
    `embedding_total_calls` (D, R-400-207 reproducible-rebuild), and
    `screener_stats` (A — operator visibility on 2-tier gating).
    `phases[*].origin` / `source_run` formalises resume.
  - **R-400-222 v1 → v2** — `ChunkRich` gains `embedding: list[float]`
    field; companion `embeddings.jsonl` stream specified.
  - **R-400-223 v1 → v2** — `/ingest-chunks` becomes a pure INSERT
    path: takes `embedding_model` + `embedding_dimension` request
    fields, uses the chunks' embeddings as-is, SHALL NOT invoke C7's
    own embedder. Transitional fallback for null embeddings
    documented (session 5 testing).
- `800-SPEC-LLM-ABSTRACTION.md` v3 → **v4**:
  - **R-800-131 v1 → v2** — decontextualiser routed to mid-tier
    (was: fast tier), invocation conditioned on screener YES verdict
    (D-020 v2 §A), mandatory `cache_control` prompt-marker placement
    documented (D-020 v2 §B).
  - **R-800-132 v1 → v2** — summariser prompt-marker placement
    likewise made normative.
  - §4.6 catalog gains `ayextract.decontextualizer_screener` row.
  - §8.1 sample `agent_routes:` updated (screener entry, mid-tier
    decontextualiser).

**Added**
- `800-SPEC-LLM-ABSTRACTION.md` **R-800-134** (new) — declares the
  `ayextract.decontextualizer_screener` agent (Haiku-class, ≤30
  tokens output, no prompt caching, fail-safe default YES on parse
  error). Cost ~$0.0001/call; break-even at ≥ 2% skip rate vs
  Sonnet decontextualiser.

**Notes**
- Still spec-only — **no `.py` modified, no workflow modified, no
  test added or removed**. Strip starts immediately after this entry.
- New open question `Q-200-028` (adaptive Phase 1 deferred to v2)
  tracked in `.claude/SESSION-STATE.md` §4.

### 2026-05-28 — D-020 AyExtractor adopted as dependency component C13 (spec-only session)

**Added**
- `999-SYNTHESIS.md` v6 → **v7** — new decision **`D-020`** (AyExtractor
  adopted as external extraction & chunking dependency, C13). Re-partitions
  the D-013 ingestion pipeline: C12 owns trigger + orchestration, **C13**
  owns extract + chunk + write MinIO artifacts, C7 owns embed + index only.
  Code-isolated component (zero `import` coupling with `ay_platform_core/`).
  v1 scope = Phase 1+2 only (Phase 3 KG / consolidator queued as Q-200-022).
  LLM-frugal stance: libraries / heuristics over LLM, image vision LLM-
  mandatory, decontextualiser / Refine / Chain of Density opt-in via
  `quality_tier` (default `minimal`). Operationalisation: 7 sessions
  (this spec session + 6 implementation sessions).
- `100-SPEC-ARCHITECTURE.md` v13 → **v14** — new **R-100-125** §10.9
  declaring C13 (Extraction & Chunking Service, dependency type): HTTP-only
  surface (`POST /analyze`, `GET /status/{run_id}`, `GET /healthz`),
  vendored monorepo + local wheel build, C8 routing via env switch
  (`OPENAI_BASE_URL`), LLM frugality invariants, physical removal of 10+
  AyExtractor modules duplicating AyWizz stack (`rag/{retriever,vector_store,
  graph_store,enricher,indexer,embeddings}`, `consolidator/`, `batch/`,
  `graph/`, Phase 3 agents, `cache/{sqlite,redis,arangodb}_*`,
  `llm/adapters/{anthropic,google,openrouter}_*`,
  `storage/{local_writer,s3_writer}`), failure handling, resource limits.
  §4.2 component table extended to **4 dependency components**
  (C10/C11/C12/C13).
- `400-SPEC-MEMORY-RAG.md` v5 → **v6** — new **§4.3bis** "AyExtractor (C13)
  ingestion contracts" with five new entities:
  - **R-400-220** MinIO artifact layout
    (`{bucket}/{tenant}/{project}/{source}/runs/{run_id}/{00_metadata,
    01_extraction,02_chunks,status.json}`).
  - **R-400-221** `RunManifest` schema (version + git sha + per-agent
    `llm_assignments` + `prompt_hashes` + per-phase timing + tokens +
    cost — byte-stable for reproducibility + run diff).
  - **R-400-222** `ChunkRich` schema (text [= decontextualised if
    `high`], original_text?, context_summary?, global_summary?,
    section_path, char offsets, references / images / tables / token_count,
    extraction_run_id).
  - **R-400-223** C7 endpoint `POST /memory/projects/{pid}/sources/{sid}/
    ingest-chunks` (rich chunk shape, quota-enforced, project_editor+).
  - **R-400-224** per-project `quality_tier ∈ {minimal | standard | high}`
    (default `minimal`, zero LLM cost when no images).
  - **R-400-225** resume-from-phase semantics (carried_from / fresh
    phase manifests).
- `800-SPEC-LLM-ABSTRACTION.md` v2 → **v3** — §4.6 catalog extended with
  four AyExtractor agents:
  - **R-800-130** `ayextract.image_analyzer` (vision-capable mid-tier,
    mandatory when images present).
  - **R-800-131** `ayextract.decontextualizer` (fast tier, prompt
    caching, opt-in `high` tier only).
  - **R-800-132** `ayextract.summarizer` (mid-tier, prompt caching,
    `standard` + `high`).
  - **R-800-133** `ayextract.densifier` (mid-tier, long context +
    prompt caching, `high` only).
  §8.1 sample `agent_routes:` updated.

**Changed (semantic — version bumps)**
- `100-SPEC-ARCHITECTURE.md` **R-100-081 v1 → v2** — re-partitions
  ingestion ownership across three components (C12 / C13 / C7) instead
  of two; supersedes the v1 "C12 owns parsing + chunking" assignment.
- `400-SPEC-MEMORY-RAG.md` **R-400-020 v1 → v2** — three-step pipeline
  becomes four-step (upload → extract+chunk[C13] → index[C7] → optional
  KG[C7]); supersedes v1.
- `400-SPEC-MEMORY-RAG.md` **R-400-021 v1 → v2** — format list extended
  to PDF/EPUB/DOCX/MD/TXT/PNG/JPG/JPEG/WEBP (was PDF/MD/TXT/PNG/JPEG);
  ownership moved to C13.
- `400-SPEC-MEMORY-RAG.md` **R-400-022 v1 → v2** — chunking becomes
  structure-aware with fixed-window fallback (was fixed-window only);
  ownership moved to C13.

**Deprecated**
- `c7_memory.service.ingest_uploaded_source` (parse + chunk in-process)
  marked deprecated in v1.5. SHALL be removed in v2 per D-020
  operationalisation session 7.
- `infra/c12_workflow/workflows/{ingest_text_source,chunk_and_track}.json`
  workflows marked deprecated. SHALL be removed in session 6 in favour
  of `extract_and_ingest.json` v1 (orchestrates C12 → C13 → C7 chain).

**Notes**
- Session 1 = spec only. **No `.py` modified. No workflow modified. No
  test added or removed.** Implementation in 6 subsequent sessions
  (strip → adapters → HTTP wrapper → C7 endpoint → n8n workflow →
  cleanup).
- Open questions opened: `Q-200-022` (Phase 3 KG adoption timing),
  `Q-200-023` (cache fingerprint backend), `Q-200-024` (AyExtractor
  versioning model — vendored vs separate repo), `Q-200-025` (C13
  integration test strategy — testcontainers vs unit-only). Tracked
  in `.claude/SESSION-STATE.md` §4.

### 2026-04-24 — Test & config foundation (P1–P6)

**Added**
- `100-SPEC-ARCHITECTURE.md` **§10 Configuration & Deployment** — 7 new
  `R-100-1NN` entities covering: single `.env` file as source of truth,
  `env_prefix="c<n>_"` naming convention, `PLATFORM_ENVIRONMENT`
  cross-cutting variable, completeness + override coherence tests,
  shared `Dockerfile.python-service`, docker-compose layout with the
  single public port on Traefik, mock LLM for CI.
- `meta/100-SPEC-METHODOLOGY.md` **§11 Test tier topology** — six-tier
  taxonomy formalised (unit, contract, integration, e2e, system,
  coherence) plus filename conventions (`test_*_real_chain.py`,
  `test_*_real_llm.py`, `test_*_storage_verified.py`) and fixture
  discipline (session-scoped testcontainers + orphan wipe +
  cleanup-with-verify helpers).

**Changed**
- `700-SPEC-VERTICAL-COHERENCE.md` bumped to v3 — `version-drift`
  (R-700-026) and `cross-layer-coherence` (R-700-028) promoted from
  STUB to real implementations (severity `blocking`). Only #3
  interface-signature-drift and #8 data-model-drift remain stubs, both
  deferred pending machine-readable `E-*` entity signature specs.
- `999-SYNTHESIS.md` §6 Document Mapping statuses refreshed — 200/400/
  700 now listed as **delivered**; 500/600 remain planned.

### 2026-04-23/24 — Implementation of C1–C9 backbone

**Added**
- `200-SPEC-PIPELINE-AGENT.md` v2 — 24 `R-200-*` entities, 12 `Q-200-*`
  resolved, five-phase pipeline fully specified (brainstorm → spec →
  plan → generate → review, three hard gates, sub-agent escalation).
- `400-SPEC-MEMORY-RAG.md` v2 — 28 `R-400-*` / `E-400-*` entities, 11
  `Q-400-*`; embedding lifecycle, dual-index schema
  (`requirements` / `external_sources`), federated retrieval, quota.
- `700-SPEC-VERTICAL-COHERENCE.md` v2 — Validation Pipeline Registry
  (C6) plugin contract, Finding model, run lifecycle, 9 MUST checks
  under `R-700-020..028` (see v3 above for stubs closure).

**Notes**
- Scaffolds `500-SPEC-UI-UX.md` and `600-SPEC-CODE-QUALITY.md` are
  unchanged in this cycle — UI and code-domain quality engine are
  scheduled beyond the backbone.

### 2026-04-22 — Initial corpus scaffold

**Added**
- `meta/100-SPEC-METHODOLOGY.md` v2 — authoring conventions, ID scheme,
  frontmatter schemas, tailoring syntax, `@relation` markers.
- `999-SYNTHESIS.md` v4 — cross-cutting decisions `D-001` through
  `D-013`, guiding principles, roadmap (v1..v4+).
- `100-SPEC-ARCHITECTURE.md` v2 — platform component decomposition
  (C1..C15), contracts, scaling model, failure domains.
- `300-SPEC-REQUIREMENTS-MGMT.md` v1 — Requirements Service (C5)
  storage, CRUD, versioning, tailoring.
- `800-SPEC-LLM-ABSTRACTION.md` v1 — LLM Gateway (C8) LiteLLM proxy,
  routing, cost tracking, eval hooks.
- `200-SPEC-PIPELINE-AGENT.md` v1 — scaffold.
- `400-SPEC-MEMORY-RAG.md` v1 — scaffold.
- `500-SPEC-UI-UX.md` v1 — scaffold.
- `600-SPEC-CODE-QUALITY.md` v1 — scaffold.
- `700-SPEC-VERTICAL-COHERENCE.md` v1 — scaffold.
- `references/simplechat-specification_backtend.md` — prior internal
  work (FastAPI chat backend reference implementation).
- `references/simplechat-specification_frontend.md` — prior internal
  work (Next.js chat frontend reference implementation).
- `references/data-Extractor-specifications.md` — prior internal work
  (multi-agent document analyzer, chunking + graph + RAG).
