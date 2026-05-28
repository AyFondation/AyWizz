---
document: 400-SPEC-MEMORY-RAG
version: 7
path: requirements/400-SPEC-MEMORY-RAG.md
language: en
status: draft
derives-from: [D-002, D-010, D-013, D-016, D-020]
---

# Memory & RAG Specification

> **STATUS: draft v2 — first populated pass.** Derives from D-002 (stack reuse: ArangoDB for both vector and graph), D-010 (graph-backed embeddings, text-only, no node2vec in v1), D-013 (external-source ingestion via C12 + C7). AyExtractor informs the structural patterns (dual-store mental model, chunking, decontextualization) but v1 is deliberately simpler: a single ArangoDB instance, text embeddings only, linear chunking, federated read across two logical indexes.
>
> Open questions in §7 gate any production deployment. Alignment with the `references/data-Extractor-specifications.md` sections §26 (RAG), §29 (embedding), §30.6/§30.7 (store interfaces) is the explicit source for future enrichments.
>
> **STATUS: draft v3 — D-016 evolution.** §4.9 adds the layered knowledge representation + iterative deterministic-first retrieval direction (D-016), staged: the **v1-compatible subset** (schema-guided L1 extraction `R-400-200`, provenance/confidence `R-400-201`, hybrid BM25+dense+RRF `R-400-202`, prompt-cached cumulative contextualisation `R-400-203`, C9 retrieval tool interface `R-400-204`) lands now; the **v2 scope** (L0–L3 layered graph `R-400-205`, iterative traversal + active pruning `R-400-206`) is recorded but gated by D-010's "GraphRAG deferred unless v1 is demonstrably insufficient". Evaluation of retrieval quality is owned by D-017 (see Q-400-011).
>
> **STATUS: draft v4 — reproducible rebuild.** `R-400-207` mandates that **all** processing outputs (parsed text, chunks, contextualised chunks, embeddings, extracted KG triples) be persisted as durable MinIO artifacts, so the vector store and the graph store rebuild by **replay** without re-invoking any embedding model or LLM. `E-400-003` (v2) adds the `embeddings.json` / `kg.json` source paths. This makes the databases projections of the artifact layer (D-018) and keeps the LLM-based KG extraction (R-400-200) reproducible.

> **STATUS: draft v6 — C13 ingestion (D-020).** §4.3 R-400-020/021/022 bumped to v2 to reflect the **three-step C12 → C13 → C7 pipeline** (per R-100-081 v2): C12 triggers + orchestrates, **C13 (AyExtractor)** owns extract + chunk + write MinIO artifacts, C7 owns embed + index only. New **§4.3bis** ships the AyExtractor-specific contracts (R-400-220..225): MinIO artifact layout, `RunManifest` schema, `ChunkRich` schema, C7 `POST /ingest-chunks` endpoint, project `quality_tier` setting (`minimal | standard | high`, default `minimal` = zero LLM calls when no images), resume-from-phase semantics. The legacy in-process parse + chunk pipeline of `c7_memory.service.ingest_uploaded_source` is marked **deprecated** in v1.5 and SHALL be removed in v2 per D-020 operationalisation session 7.

> **STATUS: draft v7 — D-020 v2 optimisations.** R-400-220 v2 adds `02_chunks/embeddings.jsonl` and `02_chunks/screener_log.jsonl` (audit trail of the decontextualiser screener verdicts) to the artifact layout. R-400-221 v2 adds `embedding_model`, `embedding_model_version`, `embedding_dimension` to `RunManifest` (byte-exact reproducibility of the embedding stage per R-400-207). R-400-222 v2 adds `embedding: list[float]` to `ChunkRich` (was: computed downstream by C7). R-400-223 v2 makes the C7 `/ingest-chunks` endpoint a **pure INSERT path** when the request carries embeddings; C7's own embedder is no longer invoked on this path. C7 retains its embedder only for the requirements-corpus write path. Aligns with R-400-207's reproducible-rebuild mandate (embeddings now in MinIO, not only in Arango).

---

## 1. Purpose & Scope

This document specifies the **Memory Service (C7)** and the ingestion pipeline that feeds it:

- Embedding computation model, storage schema, refresh cadence.
- Federated retrieval across two logical indexes: `requirements` (owned by C5) and `external_sources` (owned by C7) — per D-013.
- External source ingestion: parsing, chunking, embedding, indexing, orchestrated by C12 (n8n) and computed by C7 — per D-013.
- Short-term vs long-term memory boundaries within a conversational run.
- Public REST + MCP surfaces consumed by C3 (conversation), C4 (orchestrator agents), C9 (MCP tool server).

**Out of scope.**
- Write path for the requirements corpus (→ `300-SPEC-REQUIREMENTS-MGMT.md`, already v1-delivered).
- RAG query classification logic internals (implementation detail).
- Node-level graph embeddings (D-010 defers to v2+).
- Online fine-tuning and feedback-loop re-ranking (D-010 out of scope).
- Image OCR pipeline details (referenced by D-013, baseline library choice deferred to Q-400).

---

## 2. Glossary

| Term | Definition |
|---|---|
| **Embedding** | A fixed-length vector representation of a text fragment, produced by a sentence-transformers model. |
| **Chunk** | A bounded text fragment (typically a section / paragraph / fixed-window slice) that is embedded and stored as a unit. |
| **Source** | An external document (PDF, Markdown, TXT, image) uploaded by a user into a project's RAG corpus. |
| **Index** | A logical partition of embeddings. v1 has two: `requirements` (C5-owned) and `external_sources` (C7-owned). |
| **Federated retrieval** | A single retrieval call fans out to one or both indexes with explicit weighting, merges, returns the top-k. |
| **Embedding provider** | An adapter behind `EmbeddingProvider` protocol — local sentence-transformers model, or HTTP API. |
| **Refresh** | The operation that recomputes embeddings for content whose source has changed or whose model was upgraded. |

---

## 3. Relationship to Synthesis Decisions

| Decision | How this document operationalises it |
|---|---|
| D-002 (stack reuse) | ArangoDB hosts both the embeddings (vector) and the entity/source graph (unified). No ChromaDB / Qdrant / Neo4j in v1. |
| D-010 (graph-backed embeddings, approach A + α) | Text embeddings only; no graph neural embeddings. Refresh strategy α: periodic (cron-triggered or commit-triggered); no online fine-tuning. |
| D-013 (external source ingestion) | v1 formats = PDF, Markdown, TXT, images (with optional OCR). C12 receives uploads, dispatches parsing jobs; C7 computes embeddings and indexes. Federated retrieval with separated indexes preserves provenance. |
| D-016 (layered KG + iterative retrieval) | §4.9 operationalises the v1-compatible subset (schema-guided L1 extraction, provenance/confidence, hybrid BM25+dense+RRF, prompt-cached contextualisation, C9 retrieval tool interface); the L0–L3 layered graph and iterative traversal are recorded as v2 scope, gated by D-010. |

---

## 4. Functional Requirements

### 4.1 Embedding model & lifecycle

#### R-400-001

```yaml
id: R-400-001
version: 1
status: draft
category: functional
```

The Memory Service SHALL compute text embeddings via an abstract `EmbeddingProvider` interface (E-400-001). Concrete adapters SHALL be swappable via configuration without code changes. The v1 baseline adapter is a local sentence-transformers model (configurable via env var).

**Rationale.** Per D-010: embedding model choice is an operational decision, not architectural. Abstracting the provider lets teams swap between local models (CPU/GPU) and hosted APIs (OpenAI, Voyage, Cohere) based on latency/cost/privacy trade-offs.

#### R-400-002

```yaml
id: R-400-002
version: 1
status: draft
category: functional
```

Every embedding record SHALL carry the `model_id` that produced it (e.g. `sentence-transformers/all-mpnet-base-v2`). Records produced by different models SHALL NOT be mixed in a single retrieval — the retriever SHALL reject or re-rank when the query model differs from stored records' model.

**Rationale.** Cosine similarity is only meaningful within the same embedding space. Silent cross-model retrieval yields garbage results.

#### R-400-003

```yaml
id: R-400-003
version: 1
status: draft
category: functional
```

Embedding dimensions SHALL be declared in the model metadata and validated at write time. Any mismatch between the embedding vector length and the declared dimension SHALL cause a 422 rejection.

**Rationale.** Prevents silent drift on model upgrades.

#### R-400-004

```yaml
id: R-400-004
version: 1
status: draft
category: functional
```

On model upgrade (change of `model_id`), the service SHALL schedule a **re-embedding pass** for every affected index. During the pass, old records remain queryable; new records co-exist tagged with the new `model_id`. Once the pass completes, old records MAY be deleted per tenant retention policy.

**Rationale.** Per D-010 refresh strategy α. Upgrades are rare but disruptive; graceful coexistence avoids downtime.

---

### 4.2 Storage schema (ArangoDB)

#### R-400-010

```yaml
id: R-400-010
version: 1
status: draft
category: functional
```

The Memory Service SHALL own exactly two document collections and one edge collection (per R-100-012):

- `memory_chunks` — one record per embedded chunk (external source chunk OR requirements entity embedding).
- `memory_sources` — one record per uploaded external document (parent of its chunks).
- `memory_links` (edge) — edges from `memory_chunks` to canonical entities in `req_entities` (C5-owned) when a chunk cites or references a requirements entity.

The detailed schema is in Appendix 8.1.

**Rationale.** Minimal schema aligned with D-002 (ArangoDB unifies vector + graph). The edge collection enables "which chunks support this requirement?" queries without joining at retrieval time.

#### R-400-011

```yaml
id: R-400-011
version: 1
status: draft
category: functional
```

Embeddings SHALL be stored as fixed-length `float32[]` arrays in the `vector` field of `memory_chunks`. v1 SHALL NOT use ArangoDB's native vector index (experimental as of the baseline deployment); it SHALL implement cosine similarity via a server-side AQL function with a cap of 50 000 chunks per query scope. Beyond that cap, the query SHALL be progressively narrowed by metadata filters (project, source type, date).

**Rationale.** Native vector indexes in ArangoDB 3.12 are experimental. AQL-based cosine works up to tens of thousands of chunks with acceptable latency. The cap forces filter-first retrieval discipline, avoiding degenerate full-corpus scans.

#### R-400-012

```yaml
id: R-400-012
version: 1
status: draft
category: functional
```

Every `memory_chunks` record SHALL carry provenance metadata: `project_id`, `source_id` (for external) OR `entity_id` + `entity_version` (for requirements), `chunk_index`, `content_hash`, `model_id`, `model_dim`. Fields not matching the schema SHALL be rejected at write time.

**Rationale.** Provenance is the basis for retrieval ranking, citation, and invalidation. No chunk without provenance.

#### R-400-013

```yaml
id: R-400-013
version: 1
status: draft
category: functional
```

The `memory_sources` record SHALL reference the MinIO path of the original uploaded file so that re-parsing is always possible from the source of truth. The record SHALL also carry: `tenant_id`, `project_id`, `uploaded_by`, `upload_timestamp`, `content_mime_type`, `size_bytes`, `parse_status` (one of `pending`, `parsed`, `failed`), `chunk_count`.

**Rationale.** Parse is idempotent from the MinIO source; re-parse on schema upgrade or parser upgrade becomes a single job per source, not a re-upload.

---

### 4.3 External source ingestion (D-013)

#### R-400-020

```yaml
id: R-400-020
version: 2
status: draft
category: functional
derives-from: [D-013, D-020]
```

External source ingestion SHALL be a **four-step pipeline**, orchestrated end-to-end by C12 (n8n):

1. **Upload** — the user POSTs the file to C12's `/uploads/*` webhook (mediated by C1 Gateway). C12 stores the raw bytes in MinIO under `sources/<tenant_id>/<project_id>/<source_id>/raw.<ext>` and emits NATS `ingestion.source.uploaded`.
2. **Extract + chunk (C13)** — C12 HTTP-triggers C13 via `POST /analyze` with `{tenant_id, project_id, source_id, raw_object_key, quality_tier, config_overrides}`. C13 acknowledges immediately with `{run_id}` and processes asynchronously, writing all phase outputs to MinIO under the layout defined in **R-400-220**. C12 polls `GET /status/{run_id}` until `status ∈ {completed, failed}` (or subscribes to a NATS completion signal when wired). On completion, C12 emits `ingestion.source.parsed`.
3. **Index (C7)** — C12 reads `chunks.jsonl` + `run_manifest.json` from MinIO and POSTs them to C7's `/memory/projects/{project_id}/sources/{source_id}/ingest-chunks` endpoint (R-400-223). C7 computes embeddings on each chunk and writes the records to `memory_chunks` + `memory_sources`. On completion, C7 emits `ingestion.source.indexed`.
4. **(optional) KG extraction (C7)** — if the project's `auto_extract_kg_on_upload` flag is enabled and the LLM client is configured, C7 SHALL trigger its existing schema-guided extractor (`R-400-200`) on the freshly indexed source as a best-effort follow-up. A failure here SHALL NOT cascade to the ingestion status.

Each step SHALL be idempotent and individually re-runnable from the MinIO artifacts. On failure of step 2, C12 SHALL surface the error (`ingestion.source.failed`) to the UI and SHALL NOT attempt step 3.

**Rationale.** Per **D-020**: re-partitions the v1 split (which made C7 the parser) so that parsing + chunking happen in a dedicated dependency component (C13, AyExtractor) — keeping C7 focused on embeddings + retrieval and giving the pipeline a natural file-based handover at each stage. The four-step model is what n8n actually orchestrates: webhook → C13 trigger → poll → C7 post.

**Supersedes** R-400-020 v1 (which delegated parsing to C7).

#### R-400-021

```yaml
id: R-400-021
version: 2
status: draft
category: functional
derives-from: [D-013, D-020]
```

v1 supported input formats SHALL be (as exposed by C13's `POST /analyze`):

- `text/plain` (`.txt`) — pass-through.
- `text/markdown` (`.md`) — frontmatter stripped, structural headings preserved as chunk-boundary hints.
- `application/pdf` (`.pdf`) — text + tables + embedded images via `PyMuPDF` (and `camelot` / `tabula-py` for structured tables).
- `application/vnd.openxmlformats-officedocument.wordprocessingml.document` (`.docx`) — text + tables + embedded images via `python-docx`.
- `application/epub+zip` (`.epub`) — text + images via `ebooklib`.
- `image/png`, `image/jpeg`, `image/webp` (`.png`, `.jpg`, `.jpeg`, `.webp`) — image-as-input mode: LLM Vision provides the text via the C8-routed `ayextract.image_analyzer` agent (R-800-130). If the project's `image_vision_enabled` flag is off, image ingestion SHALL fail with a clear error.

Other formats SHALL return HTTP 415 at the C12 upload webhook (enforced before C13 is invoked).

**Rationale.** Format support is the union of AyExtractor's v1 extractor inventory (`extraction/{pdf,docx,epub,md,txt,image_input}_extractor.py`) and v1 platform format scope (D-013 option (i) plus DOCX/EPUB unlocked by C13 adoption). PPTX / XLSX / HTML / CSV / JSON / URL crawling / Git remain deferred to v2+.

**Supersedes** R-400-021 v1 (which limited v1 to text/markdown/PDF/PNG/JPEG and assigned format support to C7).

#### R-400-022

```yaml
id: R-400-022
version: 2
status: draft
category: functional
derives-from: [D-013, D-020]
```

Chunking SHALL be performed by **C13** using a **structure-aware strategy**: chunks are aligned to the document's structural backbone (sections, headings) when the structure detector returns sufficient confidence (heuristic-based, R-100-125 §5); otherwise fall back to a fixed-window strategy with `CHUNK_TOKEN_SIZE` tokens (default 512) and `CHUNK_OVERLAP` tokens (default 64). Tables and image blocks SHALL remain atomic within a single chunk (no split mid-block). The token count SHALL be computed with the tokenizer of the configured embedding model (resolved at C7 ingest time, not at chunking time).

**Rationale.** Structure-aware chunking is materially better for RAG quality on technical documents (sections preserve semantic boundaries) and AyExtractor implements it cheaply (no LLM call, deterministic heuristics over the document's parsed structure). The fixed-window fallback handles unstructured documents (plain text, opaque PDFs) and is the v1 R-400-022 baseline.

**Supersedes** R-400-022 v1 (which mandated fixed-window only).

#### R-400-023

```yaml
id: R-400-023
version: 1
status: draft
category: functional
```

Ingestion SHALL be **scoped to one project**: an upload declares its `project_id` and the resulting embeddings are only ever retrieved for queries targeting that project. Cross-project retrieval is explicitly prohibited in v1.

**Rationale.** Per D-013 and R-100-083. Cross-project contamination is a larger security/privacy concern than the convenience of "find this everywhere".

#### R-400-024

```yaml
id: R-400-024
version: 1
status: draft
category: functional
```

Per-project storage quota SHALL be enforced at upload time. Default: 1 GB per project, configurable per tenant. Exceeding the quota SHALL return HTTP 413 at C12. The current usage SHALL be queryable via `GET /api/v1/memory/projects/{project_id}/quota`.

**Rationale.** Prevents runaway embedding costs and storage bills. Per-tenant override supports regulated contexts with larger retention needs.

---

### 4.3bis AyExtractor (C13) ingestion contracts (D-020)

This subsection operationalises the C13 dependency declared in `100-SPEC-ARCHITECTURE.md` R-100-125. It defines (a) the MinIO artifact layout C13 SHALL produce, (b) the `RunManifest` schema enabling reproducibility + run diff, (c) the `ChunkRich` shape C7 indexes, (d) the C7 `/ingest-chunks` endpoint, (e) the per-project `quality_tier` setting, and (f) the resume-from-phase semantics. All entities below derive from D-020 and supersede the in-process C7 parse + chunk path (deprecated, removed in v2).

#### R-400-220

```yaml
id: R-400-220
version: 2
status: draft
category: architecture
derives-from: [D-020, R-100-125, R-400-207]
```

C13 SHALL write all outputs as MinIO objects under a **stable, versioned layout**:

```
{bucket}/{tenant_id}/{project_id}/{source_id}/runs/{run_id}/
  00_metadata/
    run_manifest.json        # R-400-221 v2 (now stamps embedding_model + dim)
    input_fingerprint.json   # {sha256, size_bytes, format, filename, mime_type}
  01_extraction/
    enriched_text.md         # Phase 1 final text (image + table descriptions inlined)
    structure.json           # Detected sections / TOC / footnotes / bibliography
    references.json          # Citations + cross-references (refextract + regex)
    images/                  # per-image LLM Vision analysis, deduplicated by sha256
      img_{sha8}.json        # (filename = first 8 hex of image sha256)
    tables/                  # structured table extraction (one .json per table)
      tbl_{NNN}.json
  02_chunks/
    chunks.jsonl             # one ChunkRich per line — R-400-222 v2
    embeddings.jsonl         # one {chunk_id, embedding: list[float]} per line (R-400-222 v2)
    chunk_index.json         # ordered list of {chunk_id, char_offsets, section_path}
    dense_summary.md         # Chain of Density output (only if quality_tier=high)
    screener_log.jsonl       # one {chunk_id, verdict: YES|NO, reason} per line
                             # (only when quality_tier=high; audit trail for R-100-125 §5)
  status.json                # {status: running|completed|failed, urgency: interactive|background,
                             #  phases_completed: [...], errors: [...]}
```

**v2 changes vs v1.** (a) Added `02_chunks/embeddings.jsonl` — embeddings now produced by C13 via C8 `/embeddings`, not by C7 (D-020 v2 §B1, R-400-222 v2). (b) Added `02_chunks/screener_log.jsonl` — decontextualiser screener audit trail per R-100-125 §5 (only present when `quality_tier=high`). (c) `status.json` now includes `urgency` so C12 can display "batched run" UX when applicable.

The bucket name SHALL be a single platform-level value (default `c13-extractor-artifacts`), distinct from the C10 source-raw bucket (`sources` under R-400-020 v2 step 1). The `run_id` SHALL be unique per C13 invocation (format `{YYYYMMDD}_{HHMM}_{uuid5_12}`); reprocessing the same `source_id` produces a NEW `run_id` (immutable runs, see R-400-225).

C13 SHALL update `status.json` atomically at each phase transition. C12 polls this object to detect completion / failure.

**Rationale.** A stable file-based contract decouples C13's internal evolution from AyWizz's consumers. The path hierarchy `tenant_id/project_id/source_id/runs/run_id` mirrors the AyExtractor native layout while embedding the AyWizz multi-tenant scope. Immutable runs (per AyExtractor §4.2) enable diff between runs without surgery on prior artifacts.

#### R-400-221

```yaml
id: R-400-221
version: 2
status: draft
category: architecture
derives-from: [D-020, R-400-220, R-400-207]
```

The `run_manifest.json` written by C13 SHALL conform to the following minimum schema (Pydantic-serialisable, extra fields allowed for forward compatibility):

```python
class RunManifest(BaseModel):
    run_id: str                                  # see R-400-220
    ayextractor_version: str                     # = ayextractor.__version__ at build time
    monorepo_git_sha: str                        # short sha of the build commit
    source: SourceFingerprint                    # {sha256, size_bytes, format, filename, mime_type}
    config: RunConfig                            # {quality_tier, decontextualization_enabled,
                                                 #  summarization_enabled, densification_enabled,
                                                 #  image_vision_enabled, chunk_token_size,
                                                 #  chunk_overlap, urgency}
    llm_assignments: dict[str, str]              # agent_name -> "provider:model" (from C8 resolution)
    prompt_hashes: dict[str, str]                # agent_name -> sha256(prompt_template)
    # --- v2 additions: embedding stage reproducibility (R-400-207) ---
    embedding_model: str                         # e.g. "voyage-3" or "sentence-transformers/all-mpnet-base-v2"
    embedding_model_version: str                 # provider-reported version pin (or commit/sha for local)
    embedding_dimension: int                     # vector length (used for Arango index sizing)
    embedding_total_calls: int                   # number of C8 /embeddings invocations
    # --- end v2 additions ---
    phases: dict[str, PhaseRecord]               # phase_key -> {started_at, completed_at,
                                                 #               output_hash, status, error?,
                                                 #               origin: "fresh"|"carried_from",
                                                 #               source_run?: str}
    tokens_used: dict[str, TokenCount]           # agent_name -> {input, output, cached}
    screener_stats: ScreenerStats | None         # v2: {total, decontext_yes, decontext_no, skip_rate}
                                                 # only present when quality_tier=high
    cost_estimate_usd: float                     # sum across all agents + embedding (informational)
    status: Literal["running", "completed", "failed"]
    created_at: datetime
    completed_at: datetime | None
```

The manifest SHALL be byte-stable for an unchanged input + config (modulo `created_at` / `completed_at` timestamps), enabling **run diff**: two runs of the same source with different prompts, model versions, or **embedding model** produce different `prompt_hashes` / `llm_assignments` / `embedding_model_version` and therefore different `output_hash` values per phase, surfacing what changed without re-running.

**v2 changes vs v1.** (a) Added `embedding_model` / `embedding_model_version` / `embedding_dimension` / `embedding_total_calls` — embedding stage now runs in C13 and SHALL be stamped here for byte-exact reproducibility (R-400-207). (b) Added `screener_stats` recording the decontextualiser screener's verdict distribution per run (operator visibility into the 2-tier gating). (c) `config.urgency` carries the `interactive | background` flag. (d) `phases[*].origin` / `source_run` formalises resume-from-phase (R-400-225 was implicit on this).

**Rationale.** Traceability + reproducibility per D-020. The embedding stage being inside C13 (D-020 v2 §B1) means the manifest is now the single answer to "why did this run produce different chunks AND embeddings than the previous one?" — without it, the embedding model identity was implicit and a silent dependency upgrade would corrupt RAG retrieval without any audit trail.

#### R-400-222

```yaml
id: R-400-222
version: 2
status: draft
category: architecture
derives-from: [D-020, R-400-220, R-400-207]
```

The `02_chunks/chunks.jsonl` file produced by C13 SHALL contain one **`ChunkRich`** record per line, conforming to:

```python
class ChunkRich(BaseModel):
    chunk_id: str                              # stable id: "{source_id}:{seq:04d}"
    seq: int                                   # 0-indexed position in the document
    text: str                                  # The text used for embedding (= decontextualized
                                               # variant if quality_tier=high AND screener=YES,
                                               # else == original_text)
    original_text: str | None                  # The pre-decontextualization chunk text;
                                               # present only when text != original
    context_summary: str | None                # Cumulative Refine summary up to and including
                                               # this chunk (quality_tier in {standard, high})
    global_summary: str | None                 # Chain of Density output for the whole document;
                                               # duplicated across all chunks (quality_tier=high)
    section_path: list[str]                    # e.g. ["Chapter 2", "2.3 Architecture"]
    char_start: int                            # char offset in 01_extraction/enriched_text.md
    char_end: int
    token_count: int                           # tokens in `text` (per ayextractor's tokenizer)
    references: list[str]                      # ref ids resolved by reference_extractor (refextract)
    images: list[str]                          # image hashes (sha8) cited inline in this chunk
    tables: list[str]                          # table ids cited inline in this chunk
    extraction_run_id: str                     # = RunManifest.run_id (R-400-221)
    # --- v2 addition: embedding produced by C13, not by C7 ---
    embedding: list[float] | None              # vector of `embedding_dimension` floats
                                               # (see R-400-221 v2 manifest stamp); MUST be
                                               # present unless the embedding stage failed
                                               # (in which case `status.json` reflects the failure)
```

A companion file `02_chunks/embeddings.jsonl` SHALL ALSO be written, with one `{chunk_id: str, embedding: list[float]}` record per line, redundant with the `embedding` field in `chunks.jsonl` but provided as a separate stream so consumers needing only the vectors (e.g. an offline replay tool re-indexing from MinIO into a fresh Arango per R-400-207) can avoid the full chunk payload.

C7 SHALL store all `ChunkRich` fields (including `embedding`) in ArangoDB `memory_chunks` records. C7's retrieval response SHALL include `context_summary` and `section_path` so the consumer (C3 conversation, C9 MCP) can present chunks with their local context.

**v2 changes vs v1.** (a) Added `embedding` field — C13 (not C7) computes embeddings via C8 `/embeddings` and writes them into the artifact set. R-400-207 reproducible-rebuild mandate is now fully honoured (embedding stage is replayable from MinIO without re-invoking the embedder). (b) Companion `embeddings.jsonl` stream for cheaper consumers. (c) `images` field switched from arbitrary ids to image sha8 (first 8 hex of sha256) — consistent with the deduplicated image filenames in `01_extraction/images/img_{sha8}.json` (R-400-220 v2).

**Rationale.** The rich shape + co-located embedding is the entire ROI of D-020 v2: the artifact set is now self-contained (no external state needed to reproduce a chunk's vector representation), C7's `/ingest-chunks` becomes a pure INSERT path, and re-indexing into a fresh Arango requires only reading MinIO.

#### R-400-223

```yaml
id: R-400-223
version: 2
status: draft
category: functional
derives-from: [D-020, R-400-020, R-400-222, R-400-207]
```

C7 SHALL expose a new endpoint:

```
POST /api/v1/memory/projects/{project_id}/sources/{source_id}/ingest-chunks
```

Request body:

```json
{
  "extraction_run_id": "20260528_0950_abc123def456",
  "manifest_object_key": "c13-extractor-artifacts/{tenant}/{project}/{source}/runs/{run}/00_metadata/run_manifest.json",
  "embedding_model": "voyage-3",
  "embedding_model_version": "2024-01-15",
  "embedding_dimension": 1024,
  "chunks": [ <ChunkRich with `embedding` populated>, ... ],
  "uploaded_by": "<user_sub>",
  "mime_type": "<source mime>"
}
```

C7's handler SHALL:

1. Validate the `ChunkRich` list against R-400-222 v2. Each chunk SHALL carry a non-null `embedding` of length `embedding_dimension`.
2. Validate `embedding_model` / `embedding_model_version` / `embedding_dimension` consistency: all chunks SHALL share the same dimension; the manifest reference SHALL match the request fields (defence in depth against partial uploads).
3. Enforce the per-project quota (R-400-024) against the cumulative `token_count`.
4. **Pure INSERT path** — copy every `ChunkRich` field (including the `embedding`) into `memory_sources` + `memory_chunks` records. C7 SHALL NOT invoke its own embedder on this path; the embeddings are taken from the request.
5. Stamp each chunk with `processing_version` (R-400-208) including the embedding model identity so downstream re-indexing detects staleness when AyExtractor's embedding model changes.
6. Return `SourcePublic` (existing model) with `chunk_count = len(chunks)`.

**Backward-compat fallback (transitional).** If a request omits the `embedding` field on the chunks (or sends `embedding: null`), C7 SHALL fall back to its existing embedder to populate the vectors. This fallback is transitional for session 5 testing (when n8n wiring lands before the embedding-in-C13 path is implemented) and SHALL be removed in v2.

The endpoint SHALL be authenticated via the platform's forward-auth headers (X-User-Id / X-Tenant-Id / X-User-Roles) and gated to `project_editor`+ per E-100-002.

**v2 changes vs v1.** (a) Embeddings are taken from the request, not computed by C7 — pure INSERT path per D-020 v2 §B1. (b) `embedding_model` / `embedding_model_version` / `embedding_dimension` are top-level request fields (validated against the manifest). (c) Transitional fallback documented so session 5 can land without a hard dependency on session 4's embedding wiring.

**Rationale.** Pure INSERT path: C7 ingest becomes quasi-instantaneous (no LLM/embedding compute wait), R-400-207 reproducible-rebuild mandate fully honoured (replay from MinIO produces byte-exact Arango state), embedding model identity is stamped in both the manifest AND C7's `processing_version` so staleness detection works across the upgrade boundary.

#### R-400-224

```yaml
id: R-400-224
version: 1
status: draft
category: functional
derives-from: [D-020, R-100-125]
```

Each project SHALL carry a per-project `quality_tier` setting controlling the LLM intensity of C13's pipeline:

| Tier | LLM agents enabled | Behavior |
|---|---|---|
| `minimal` (default) | `image_analyzer` only (and only when images are present) | Pure-Python extract + structure-aware chunk. Zero LLM cost for documents without images. |
| `standard` | `image_analyzer` (if images) + `summarizer` (Refine, one call per chunk) | Adds the cumulative `context_summary` field on every chunk. Cost: ~1 LLM call per chunk. |
| `high` | All of `standard` + `decontextualizer` (one call per chunk) + `densifier` (Chain of Density, 5 iterations on the whole doc) | Adds decontextualised `text` + `original_text` + `global_summary` fields. Cost: ~2 LLM calls per chunk + 5 calls per document. |

The default tier SHALL be `minimal`. The tier SHALL be settable per-project via the platform's project-config surface (UI + API); a per-document override SHALL also be accepted by C13's `POST /analyze` (`config_overrides.quality_tier`) for ad-hoc upgrades.

A change of `quality_tier` on an existing project SHALL NOT retroactively reprocess existing sources — re-ingestion is explicit (operator triggers reprocess per R-400-208).

**Rationale.** Cost containment is a hard operator constraint (D-020). Defaulting to `minimal` keeps the platform's ingestion cost equivalent to the legacy C7 path (no LLM call) for the common case (text-only documents); operators opt in to better quality for cases that warrant it (technical specs with complex coreference, long reports needing macro summaries). The three tiers mirror AyExtractor's native config flags (`decontextualization_enabled`, `summarization_enabled`, `densification_enabled`) for a clean mapping.

#### R-400-225

```yaml
id: R-400-225
version: 1
status: draft
category: functional
derives-from: [D-020, R-400-220]
```

C13 SHALL support **resume-from-phase**: after a partial-failure run (e.g. Phase 2 crashed but Phase 1 completed), a re-trigger via `POST /analyze` with `{resume_from_run: <prior_run_id>}` SHALL create a NEW immutable run that copies Phase 1's artifacts from the prior run (no re-execution, hence no LLM re-spend on image vision) and re-runs only the failed and subsequent phases. The `run_manifest.json` of the new run SHALL record each carried phase as `{origin: "carried_from", source_run: <prior_run_id>, output_hash: <same hash>}` and each fresh phase as `{origin: "fresh", ...}`.

C12 SHALL surface this capability to the operator: when an ingestion fails with a recoverable error (e.g. C8 transient 429), the UI SHALL offer "Retry from last completed phase" alongside "Restart from scratch".

**Rationale.** Without per-phase resume, every transient failure costs the full pipeline re-run (the entire image_analyzer pass on a 50-image PDF is expensive). The artifact-based handover (R-400-220) makes resume essentially free: the new run is a directory of symbolic-copy + fresh-output files.

---

### 4.4 Requirements-corpus embedding (write side)

#### R-400-030

```yaml
id: R-400-030
version: 1
status: draft
category: functional
```

Every time an entity is created or materially updated in C5, C7 SHALL receive a NATS event (`requirements.*.entity.created|updated`) and re-embed the entity's body. The resulting `memory_chunks` record SHALL carry `entity_id`, `entity_version`, `content_hash`, and SHALL be scoped to the `requirements` index.

**Rationale.** Per D-010: embeddings are kept fresh by event-driven re-compute on write-through. No polling.

#### R-400-031

```yaml
id: R-400-031
version: 1
status: draft
category: functional
```

Entity versions SHALL co-exist in the `requirements` index: embedding records for `v1` SHALL NOT be deleted when `v2` arrives. Retrieval SHALL by default return only the latest version of each entity; a flag `include_history=true` SHALL expose prior versions.

**Rationale.** Historical traceability per R-M100-091; fresh default prevents the retriever from surfacing stale text.

#### R-400-032

```yaml
id: R-400-032
version: 1
status: draft
category: functional
```

Requirements entities with `status = deprecated` SHALL be retained in the index with a flag; retrieval SHALL NOT return them unless `include_deprecated=true`.

**Rationale.** Per R-M100-091 deprecated entities remain discoverable by auditors but are out of the default retrieval set for agents.

---

### 4.5 Federated retrieval (D-013)

#### R-400-040

```yaml
id: R-400-040
version: 1
status: draft
category: functional
```

The retrieval API SHALL expose `POST /api/v1/memory/retrieve` accepting:

- `project_id` (required).
- `query` (required, text).
- `indexes` (required): non-empty subset of `{"requirements", "external_sources"}`.
- `top_k` (optional, default 10, max 50).
- `weights` (optional): per-index multiplier applied to the similarity score; defaults to `{requirements: 1.0, external_sources: 1.0}`.
- `filters` (optional): `{status, category, domain, source_id, ...}` — the accepted keys depend on the index.
- `include_history` (optional, default False).
- `include_deprecated` (optional, default False).

Response: `RetrievalResponse` with a merged, weighted, re-ranked list of up to `top_k` records, each carrying full provenance (entity_id/source_id, chunk_index, score, index, snippet).

**Rationale.** Federated retrieval per D-013 with explicit per-index weighting avoids the contamination issue (an external PDF snippet being treated as a requirement) while still giving callers one call site.

#### R-400-041

```yaml
id: R-400-041
version: 1
status: draft
category: nfr
```

Retrieval latency SHALL be under 200 ms p95 for `top_k ≤ 10` on corpora up to 10 000 chunks per index. Beyond that scale, the caller SHALL narrow the query via filters before hitting the retriever, or accept degraded latency until v2 introduces indexed search.

**Rationale.** Agents (C4) depend on retrieval on the hot path of every LLM call. Sub-200 ms keeps the LLM-driven latency dominant.

#### R-400-042

```yaml
id: R-400-042
version: 1
status: draft
category: functional
```

The retriever SHALL reject requests where the query model and the stored `model_id` differ, returning HTTP 409 with guidance to re-embed or query with the matching model. No automatic cross-model re-ranking in v1.

**Rationale.** Per R-400-002. Prevents silent quality degradation.

#### R-400-043

```yaml
id: R-400-043
version: 1
status: draft
category: functional
```

The retrieval response SHALL include a `retrieval_id` (UUID) and the full set of input parameters (for debugging and reproducibility), and SHALL emit a NATS event `memory.retrieval.completed` carrying the same id, the `top_k` snippets' chunk_ids, and the resulting scores.

**Rationale.** Observability for evaluating retrieval quality (input → output pair) and for future eval harness correlation.

---

### 4.6 Short-term vs long-term memory

#### R-400-050

```yaml
id: R-400-050
version: 1
status: draft
category: functional
```

**Short-term memory** is the conversational context held by C3 (message history) — NOT part of C7. C7 memory is exclusively long-term: persisted embeddings of external sources and requirements.

**Rationale.** Avoid feature creep. Conversational short-term context is already a first-class concern of C3 and does not share semantics with RAG.

#### R-400-051

```yaml
id: R-400-051
version: 1
status: draft
category: functional
```

When a pipeline run (C4) needs conversational context beyond what C3 holds, it SHALL NOT push conversation turns into C7's indexes automatically. An explicit user-initiated action ("remember this conversation") is required to promote a conversation summary to C7 storage — this action is deferred to v2.

**Rationale.** Auto-indexing conversations creates strong privacy and retention obligations that exceed the v1 scope.

---

### 4.7 Refresh & invalidation (D-010 strategy α)

#### R-400-060

```yaml
id: R-400-060
version: 1
status: draft
category: functional
```

C7 SHALL expose an admin endpoint `POST /api/v1/memory/projects/{project_id}/refresh` that triggers re-embedding of everything in `external_sources` for the project. Requirements entities are covered by event-driven refresh (R-400-030) and do not need an explicit trigger.

**Rationale.** Per D-010 strategy α: periodic recomputation, not online learning. An admin-initiated refresh handles model upgrades and corpus migrations.

#### R-400-061

```yaml
id: R-400-061
version: 1
status: draft
category: functional
```

Refresh SHALL be an asynchronous job (same pattern as C5 reindex) with `GET /api/v1/memory/refresh/{job_id}` for status polling. In v1 it is admin-only; per-tenant scheduling (cron) is deferred to v2.

**Rationale.** Mirrors C5's reindex job model (R-300-070) for operator familiarity.

---

### 4.8 RBAC & quotas

#### R-400-070

```yaml
id: R-400-070
version: 1
status: draft
category: security
```

Every request to C7 SHALL carry identity via the Traefik forward-auth headers (`X-User-Id`, `X-User-Roles`, `X-Tenant-Id`). Per-project roles from E-100-002 apply:

- `project_viewer` / `project_editor` / `project_owner` can retrieve from the project's indexes.
- `project_editor` / `project_owner` / `admin` can upload new sources (via C12).
- `project_owner` / `admin` can trigger refresh or delete sources.

**Rationale.** Consistent with the rest of the platform's RBAC model.

#### R-400-071

```yaml
id: R-400-071
version: 1
status: draft
category: security
```

Cross-tenant retrieval is PROHIBITED. A query scoped to a project SHALL only consider embeddings whose `tenant_id` matches the caller's tenant; mismatch SHALL return HTTP 404 (not 403) to avoid leaking tenant existence.

**Rationale.** Privacy-first default for multi-tenant deployments.

---

### 4.9 Layered knowledge representation & iterative retrieval (D-016)

> This subsection operationalises **D-016**. `R-400-200`..`R-400-204` are the **v1-compatible subset** (no graph-ML, no community detection — quality improvements over the flat v1 path). `R-400-205`..`R-400-206` record the **v2 scope** (layered graph + iterative loop), gated by D-010. They are stated here so the v1 contracts (extraction schema, provenance fields, retrieval tool interface) are designed forward-compatibly, not so they are implemented in v1.

#### R-400-200

```yaml
id: R-400-200
version: 2
status: draft
category: functional
derives-from: [D-016]
```

The **structural (L1) extractor that operates over artifacts whose ontology is known a priori** — primarily the project's own `code` and `requirements` corpus — SHALL constrain extraction to a **closed, domain-specific ontology** (for the `code` domain: the entity and relation types declared in E-400-006), validated against a Pydantic schema. An extraction whose entity type or relation type is not in the ontology SHALL be rejected, NOT silently coerced or stored as a free-form type.

**Rationale.** Open-domain extraction fragments semantically equivalent relations (the `KILLS` / `KILLED` / `SLAYS` problem observed in the Iliad open-vs-schema study), which degrades both queryability and downstream community detection. Where the ontology is known (the project's own code/requirements structural graph), schema-guided extraction is the correct choice.

**Scope note.** The C7 **external-source extractor** (`c7_memory/kg/extractor.py`), which extracts knowledge from *arbitrary uploaded documents* whose domain is not known a priori, is a **distinct component** and is NOT governed by this requirement: per the same open-vs-schema guidance, open-domain extraction (or the hybrid two-pass: open-domain discovery → ontology refinement → schema-guided pass) is the appropriate choice there. R-400-200 governs the known-ontology structural extractor over the project's own artifacts; it is therefore a **new component**, not a migration of the existing document extractor.

#### R-400-201

```yaml
id: R-400-201
version: 1
status: draft
category: functional
derives-from: [D-016]
```

Every extracted knowledge node and edge SHALL carry a `provenance` field with value in `{EXTRACTED, INFERRED}` and a `confidence` field in `[0.0, 1.0]`. Deterministically extracted records (AST / structural parsing) SHALL be tagged `EXTRACTED` with `confidence = 1.0`; LLM-inferred records SHALL be tagged `INFERRED` with the model-reported or calibrated confidence.

**Rationale.** Epistemic honesty by construction: a consumer (and an auditor) must be able to distinguish a fact the system extracted from one it guessed. This is the node/edge analogue of R-400-012's chunk provenance and the basis for the future lint/audit pass.

#### R-400-202

```yaml
id: R-400-202
version: 1
status: draft
category: functional
derives-from: [D-016]
```

The default retrieval mode SHALL be **hybrid**: a lexical arm (BM25 via ArangoSearch) and a dense arm (the cosine similarity of R-400-011) SHALL be computed and merged by reciprocal rank fusion (RRF). Exact-token queries (identifiers, entity IDs such as `R-400-202`, file paths) SHALL be reachable through the lexical arm even when the dense arm misses them.

**Rationale.** Dense similarity alone misses exact matches (IDs, names, rare terms); RRF combines lexical recall with semantic understanding without tuning a score-fusion weight. This augments — it does not replace — the dense path of R-400-011, which remains the dense arm.

#### R-400-203

```yaml
id: R-400-203
version: 1
status: draft
category: functional
derives-from: [D-016]
```

During ingestion, each chunk SHALL be augmented with a short **contextualisation** that situates it within its source document and resolves anaphora, generated by a configurable model with the document (or prior-context) prefix supplied via prompt caching. The embedded text SHALL be the contextualised chunk, not the raw chunk alone. The contextualisation model SHALL be configurable via C8 and SHOULD be a small, low-cost model (Haiku-class hosted, or a local Ollama model where privacy or cost requires it).

**Rationale.** Contextual retrieval (cumulative, ambiguity-resolving per-chunk context) materially improves retrieval recall; supplying the shared document prefix via prompt caching keeps the per-chunk cost low, which is why a small model suffices. The model is a config choice within D-011 level 1 (one active provider, swappable), not task-routing (D-011 level 2, v2).

#### R-400-204

```yaml
id: R-400-204
version: 1
status: draft
category: functional
derives-from: [D-016]
```

C9 SHALL expose a stable **retrieval tool interface** (E-400-007) with four operations — `search` (hybrid retrieval per R-400-202), `grep` (deterministic regex over chunk text), `read_document` (full-source retrieval by id), and `prune` (remove an item from the caller's working set). Any retrieval backend (the AQL-native implementation in v1, a dedicated retrieval sub-agent later) SHALL be swappable behind this interface without changing the tool contract.

**Rationale.** An interface-first design lets a future specialised retrieval backend be adopted (or dropped) without rework, and keeps `grep` / `read_document` as pure deterministic tools. The four-operation shape mirrors the dedicated-retrieval-agent pattern (Chroma Context-1) so that pattern remains a drop-in v2+ option.

#### R-400-205

```yaml
id: R-400-205
version: 1
status: draft
category: functional
derives-from: [D-016]
```

**(v2.)** The knowledge graph SHALL be organisable into four vertically-linked abstraction layers — L0 (verbatim), L1 (structural symbols/relations), L2 (semantic entities + topological communities with per-community summaries), L3 (cross-cutting themes + recursive summaries) — where every L1/L2/L3 record carries `derived-from` edges to the L0 evidence it abstracts. L2/L3 summaries SHALL NOT be returned to a consumer without their `derived-from` provenance trail.

**Rationale.** The abstraction hierarchy doubles as the traceability tree (Principle 2), so a thematic answer can always be drilled down to verbatim evidence. Per D-010 this is **v2** scope (community detection + LLM summaries = GraphRAG), gated by demonstrated v1 insufficiency.

#### R-400-206

```yaml
id: R-400-206
version: 1
status: draft
category: functional
derives-from: [D-016]
```

**(v2.)** The retriever SHALL support an **iterative traversal** mode that enters at the layer matching the query (thematic → L3, specific → L1/L2), then descends and traverses neighbour-to-neighbour, expanding the working set with deterministic graph algorithms (graph traversal / personalized-PageRank-style propagation in AQL) and invoking an LLM only to arbitrate ambiguous descend/stop decisions, while actively pruning irrelevant items from the working set. The loop SHALL be bounded by a maximum depth, a maximum hop count, and a confidence threshold.

**Rationale.** Iterative, structure-guided retrieval (the "research-in-a-library" model) retrieves a small focused subgraph progressively rather than dumping many chunks, raising precision and cutting token cost; deterministic propagation keeps the LLM a last-resort arbiter. Per D-010 this is **v2** scope.

#### R-400-207

```yaml
id: R-400-207
version: 1
status: draft
category: functional
derives-from: [D-013, D-016]
```

All processing outputs of the ingestion + knowledge-extraction pipeline — parsed text, chunk boundaries, contextualised chunks (R-400-203), embeddings, and the extracted L1 knowledge triples (R-400-200) — SHALL be persisted as durable MinIO artifacts (alongside the raw blob and the `parsed.txt` / `chunks.json` of R-400-020), and the vector store and graph store in ArangoDB SHALL be fully reconstructible by **replaying** these artifacts WITHOUT re-invoking any embedding model or LLM. Each artifact SHALL record the `model_id` (embeddings) and the extraction `model_id` + ontology version (KG triples) that produced it, so a replay reproduces the exact stored state and a model upgrade is an explicit re-processing decision rather than an implicit side effect of a rebuild.

**Rationale.** The L1 KG extraction is LLM-based (R-400-200): recomputing it on a rebuild would be costly AND non-deterministic, silently mutating the graph and breaking traceability (Principle 2). Embedding recomputation is deterministic but expensive. Persisting both as replayable artifacts makes a DB rebuild a pure, free, reproducible load — the databases become projections of the artifact layer (D-018), not the source of truth. This generalises the per-step idempotency of R-400-020 to the embedding and extraction stages, which today write only to ArangoDB.

#### R-400-208

```yaml
id: R-400-208
version: 1
status: draft
category: functional
derives-from: [D-016]
```

Each ingested source SHALL be stamped with a `processing_version` — a deterministic descriptor of the pipeline that produced its chunks (at minimum: the chunk window/overlap and the embedding `model_id`). The source status (`GET .../sources/{id}`) SHALL expose `processing_version` and a computed `is_stale` flag (stored version ≠ the pipeline's current version). A `POST .../sources/{id}/reprocess` endpoint SHALL re-run the pipeline for a single source from its persisted raw bytes and re-stamp the current version; it SHALL return 409 when the source has no stored raw bytes to reprocess from.

**Rationale.** Operators — and the n8n ingestion workflow — need to see which processing a source received and re-trigger only what is stale, rather than blindly re-embedding a whole project (R-400-060). Per-source granularity plus an explicit version make re-processing intentional and observable. Generalises R-400-004 (re-embed on model upgrade) from the embedding model alone to the full pipeline descriptor.

#### R-400-209

```yaml
id: R-400-209
version: 1
status: draft
category: functional
derives-from: [D-019]
```

Knowledge-graph records (entities and relations) SHALL be **bi-temporal**,
carrying two independent, nullable time axes : **valid time** (`valid_from`,
`valid_to` — when the fact holds in the modelled domain ; null `valid_to` =
currently valid) and **transaction time** (`recorded_at` — when first
asserted ; `superseded_at` — when the assertion ended). Correcting a fact
SHALL be **append-only** : the prior record's transaction interval is closed
(`superseded_at` stamped, `superseded_by` set) and a new record inserted —
no record is deleted. The retriever / KG traversal SHALL support **as-of**
filtering on either axis : `valid_at(t)` returns records whose valid interval
contains `t` ; `known_as_of(s)` returns records whose transaction interval
contains `s`. Records with null intervals are timeless (backward-compatible
with R-400-200 records that predate this requirement). The schema-guided L1
KG (R-400-200 / E-400-006) is in scope first ; the open-domain extractor and
chunk-level valid time are follow-ons.

**Rationale.** An evolving agentic memory must separate "a fact became false
in the world" from "the system learned it was wrong", retain corrected facts
as a tamper-evident lineage (ISO 21434 / ASPICE traceability), and never
destructively overwrite the "what we knew when" trail. Append-only
bi-temporality (D-019) delivers all three ArangoDB-natively — extra fields +
AQL interval filters — with no second graph store, preserving D-002.

---

## 5. Non-Functional Requirements

### 5.1 Performance

#### R-400-100

```yaml
id: R-400-100
version: 1
status: draft
category: nfr
```

Embedding a single 512-token chunk with the baseline sentence-transformers model SHALL take under 100 ms p95 on a CPU-only baseline deployment footprint (R-100-106). GPU acceleration is optional and not assumed.

**Rationale.** Baseline model choice balances quality and CPU inference speed. GPU is a deploy-time optimisation, not a v1 assumption.

#### R-400-101

```yaml
id: R-400-101
version: 1
status: draft
category: nfr
```

Ingestion throughput SHALL be at least 100 chunks per minute sustained on the baseline footprint (measured end-to-end from `ingestion.source.uploaded` to `ingestion.source.indexed`).

**Rationale.** Caps the upload-to-available window at reasonable minutes for typical-size documents. Lower throughput is a capacity-tuning concern, not a v1 correctness concern.

### 5.2 Consistency

#### R-400-110

```yaml
id: R-400-110
version: 1
status: draft
category: nfr
```

Requirements-entity embeddings SHALL be consistent with the entity state within 30 seconds of a C5 write (event-driven refresh). External-source embeddings SHALL be consistent with the source within the end-to-end ingestion window (R-400-101).

**Rationale.** Bounds on "how stale can a retrieved snippet be" — critical for agents that decide based on the result.

### 5.3 Observability

#### R-400-120

```yaml
id: R-400-120
version: 1
status: draft
category: nfr
```

C7 SHALL emit Prometheus metrics covering at minimum: embedding latency per model, ingestion queue depth, retrieval latency percentiles per index, per-project chunk count, refresh job duration, parse failure rate per MIME type.

**Rationale.** Surfaces the operational fault lines most likely to degrade retrieval quality.

---

## 6. Interfaces & Contracts

### 6.1 REST API

Public surface (rooted under `/api/v1/memory/` behind C1 forward-auth):

```
POST   /api/v1/memory/retrieve            — federated retrieval
GET    /api/v1/memory/projects/{pid}/sources           — list uploaded sources
GET    /api/v1/memory/projects/{pid}/sources/{sid}     — single source metadata
DELETE /api/v1/memory/projects/{pid}/sources/{sid}     — remove a source + its chunks
GET    /api/v1/memory/projects/{pid}/quota             — storage quota
POST   /api/v1/memory/projects/{pid}/refresh           — trigger refresh (admin)
GET    /api/v1/memory/refresh/{job_id}                 — job status
GET    /api/v1/memory/health                           — liveness + model availability
```

The source-upload endpoint itself lives on C12 (`POST /uploads/...`) and forwards to C7 via NATS; C7 does not expose an HTTP upload surface directly in v1.

Full OpenAPI schema in E-400-005.

### 6.2 NATS subjects

```
ingestion.source.uploaded         (published by C12, consumed by C7)
ingestion.source.parsed           (published by C7)
ingestion.source.indexed          (published by C7)
ingestion.source.failed           (published by C7 on parse/embed failure)
requirements.<pid>.entity.created (consumed by C7 — triggers re-embed)
requirements.<pid>.entity.updated (consumed by C7)
requirements.<pid>.entity.deprecated (consumed by C7 — flags as deprecated)
memory.retrieval.completed        (published by C7 after every retrieve)
memory.refresh.started|completed|failed
```

Event envelope follows E-300-003 (reused); per-event payload in E-400-004.

### 6.3 Contract-critical entities

#### E-400-001: `EmbeddingProvider` protocol

```yaml
id: E-400-001
version: 1
status: draft
category: architecture
```

Python `Protocol` every embedding adapter satisfies. Two methods:

```python
async def embed_one(self, text: str) -> list[float]: ...
async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...
```

Plus metadata:

```python
model_id: str         # e.g. "sentence-transformers/all-mpnet-base-v2"
dimension: int        # vector length this adapter produces
max_input_tokens: int # largest single input the adapter accepts
```

Concrete adapters shipped in v1: `DeterministicHashEmbedder` (test baseline, zero deps, reproducible) and `SentenceTransformersEmbedder` (production baseline, requires `sentence-transformers` at deploy time — optional extra in `pyproject.toml`).

#### E-400-002: `memory_chunks` collection schema

```yaml
id: E-400-002
version: 1
status: draft
category: architecture
```

```json
{
  "_key": "<tenant_id>:<project_id>:<chunk_id>",
  "tenant_id": "<tenant-id>",
  "project_id": "<project-id>",
  "index": "requirements | external_sources",
  "source_id": "<source-id-or-null>",
  "entity_id": "<entity-id-or-null>",
  "entity_version": 3,
  "chunk_index": 0,
  "content": "<verbatim text>",
  "content_hash": "sha256:...",
  "vector": [0.01, 0.42, ...],
  "model_id": "sentence-transformers/all-mpnet-base-v2",
  "model_dim": 768,
  "created_at": "2026-04-23T12:00:00Z",
  "status": "active | deprecated | superseded",
  "metadata": { "category": "functional", "domain": "code", ... }
}
```

Indexes:
- Persistent on `(tenant_id, project_id, index)` for retrieval scoping.
- Persistent on `entity_id` to support version co-existence lookup.
- Persistent on `source_id` for source deletion cascades.

#### E-400-003: `memory_sources` collection schema

```yaml
id: E-400-003
version: 2
status: draft
category: architecture
```

```json
{
  "_key": "<tenant_id>:<project_id>:<source_id>",
  "tenant_id": "<tenant-id>",
  "project_id": "<project-id>",
  "source_id": "<source-id>",
  "minio_raw_path": "sources/<pid>/<sid>/raw.pdf",
  "minio_parsed_path": "sources/<pid>/<sid>/parsed.txt",
  "minio_chunks_path": "sources/<pid>/<sid>/chunks.json",
  "minio_embeddings_path": "sources/<pid>/<sid>/embeddings.json",
  "minio_kg_path": "sources/<pid>/<sid>/kg.json",
  "mime_type": "application/pdf",
  "size_bytes": 423412,
  "uploaded_by": "<user-id>",
  "uploaded_at": "2026-04-23T12:00:00Z",
  "parse_status": "pending | parsed | failed",
  "parse_error": null,
  "chunk_count": 42,
  "model_id": "sentence-transformers/all-mpnet-base-v2"
}
```

#### E-400-004: NATS event payloads

```yaml
id: E-400-004
version: 1
status: draft
category: architecture
```

Envelope per E-300-003. Payload examples:

- `ingestion.source.uploaded`: `{"source_id": "...", "project_id": "...", "mime_type": "application/pdf", "size_bytes": 423412}`
- `ingestion.source.indexed`: `{"source_id": "...", "chunk_count": 42, "model_id": "..."}`
- `memory.retrieval.completed`: `{"retrieval_id": "...", "top_k": 10, "indexes": ["requirements"], "chunk_ids": ["..."], "latency_ms": 87}`

#### E-400-005: REST API OpenAPI reference

```yaml
id: E-400-005
version: 1
status: draft
category: architecture
```

Canonical path: `api/openapi/memory-service-v1.yaml`. Every endpoint in §6.1 SHALL be declared with request/response schemas, auth requirements (bearer JWT), and error examples.

#### E-400-006: `CodeKnowledgeOntology` (schema-guided L1 extraction)

```yaml
id: E-400-006
version: 1
status: draft
category: architecture
derives-from: [D-016, D-004]
```

The closed entity/relation type set the L1 extractor (R-400-200) is constrained to for the `code` domain. Expressed as Pydantic `Literal` types so `mypy --strict` and runtime validation both reject out-of-ontology types.

```python
EntityType = Literal[
    "MODULE", "CLASS", "FUNCTION", "METHOD",
    "REQUIREMENT", "DECISION", "TEST", "CONTRACT",
]
RelationType = Literal[
    "IMPORTS", "CALLS", "DEFINES", "INHERITS_FROM",
    "IMPLEMENTS", "VALIDATES", "DERIVES_FROM", "REFERENCES",
]
```

Both vocabularies are extensible per future production domain (a `documentation` domain would register its own ontology); extension requires a version bump of this entity. The relation verbs `IMPLEMENTS` / `VALIDATES` / `DERIVES_FROM` align with the `@relation` marker verbs of `meta/100-SPEC-METHODOLOGY.md` §8 and the C6 traceability checks, so the L1 graph and the coherence engine share one vocabulary.

#### E-400-007: Retrieval tool interface

```yaml
id: E-400-007
version: 1
status: draft
category: architecture
derives-from: [D-016]
```

The four-operation retrieval contract exposed by C9 (R-400-204). Backend-agnostic.

```python
async def search(query: str, *, project_id: str, indexes: list[str],
                 top_k: int = 10, filters: dict | None = None) -> list[RetrievedItem]:
    """Hybrid BM25 + dense + RRF retrieval (R-400-202)."""

async def grep(pattern: str, *, project_id: str, max_results: int = 5) -> list[RetrievedItem]:
    """Deterministic regex over chunk text. No LLM, no embedding."""

async def read_document(source_id: str, *, project_id: str) -> Source:
    """Full-source retrieval by id (drill to L0 verbatim)."""

async def prune(item_ids: list[str]) -> int:
    """Remove items from the caller's working set; returns the count removed."""
```

`RetrievedItem` carries full provenance (entity_id/source_id, chunk_index, score, index, snippet, layer) so a caller can always trace a result to its origin. The interface is the seam at which a dedicated retrieval sub-agent (v2+) can replace the AQL-native backend without changing callers.

---

## 7. Open Questions

| ID | Question | Owning decision | Target resolution |
|---|---|---|---|
| Q-400-001 | PDF parser library: `pypdf`, `pdfplumber`, `docling`, PyMuPDF? | D-013 | v1 (baseline: `pypdf` for text-only PDFs; upgrade to `docling` when tables/images are needed) |
| Q-400-002 | OCR library: Tesseract, PaddleOCR, cloud API? | D-013 | v1 (baseline: Tesseract via `pytesseract`, CPU-only; feature flag `OCR_ENABLED`) |
| Q-400-003 | Baseline sentence-transformers model choice — `all-mpnet-base-v2` (768d, general-purpose) vs `bge-small-en-v1.5` (384d, faster) vs `bge-large-en-v1.5` (1024d, higher quality)? | D-010 | v1 (baseline: `all-mpnet-base-v2`, revisit after first real-world corpus measurements) |
| Q-400-004 | ArangoDB native vector index: when it becomes non-experimental, drop the AQL-based cosine path? | D-002, D-010 | v2 (triggered by ArangoDB 3.13+ stability announcement) |
| Q-400-005 | Chunk overlap strategy — fixed token count vs sentence-aware? | D-010 | v2 (structure-aware chunking per format when AyExtractor patterns land) |
| Q-400-006 | Graph-propagation re-ranking (D-010's "approach (α)"): which signals propagate through `memory_links` / `req_relations`? | D-010 | v2 (requires link construction at ingest — deferred) |
| Q-400-007 | Auto-indexing of conversation summaries into C7 — which privacy controls? Opt-in per project? Retention per tenant? | — | v2 (R-400-051 defers the feature; privacy review gates the implementation) |
| Q-400-008 | Refresh cadence — fully admin-triggered vs per-tenant cron? | D-010 | v2 (admin-only in v1; cron arrives with Redis-backed scheduler) |
| Q-400-009 | Source deletion semantics — hard delete vs soft delete with 30-day grace? | — | v1 (baseline: hard delete on user action; chunks and source are removed from indexes immediately, MinIO `_deleted/` holds raw for 30 days) |
| Q-400-010 | Multi-language embedding — per-project model selection? | D-009 | v2 (corpus is English-by-default; multi-language RAG deferred) |
| Q-400-011 | Eval harness for retrieval quality — golden query set per project? | D-017 | v2 (direction now owned by D-017 — graded three-tier verdicts; the retrieval golden-set design lands with the C6 eval harness in 600/700, provider eval in 800) |

---

## 8. Appendices

### 8.1 ArangoDB collections (indicative summary)

| Collection | Owner | Kind | Purpose |
|---|---|---|---|
| `memory_chunks` | C7 | document | Embedded text with provenance (external sources AND requirements entities). |
| `memory_sources` | C7 | document | Metadata about uploaded external documents. |
| `memory_links` | C7 | edge (`memory_chunks` → `req_entities`) | "This chunk cites this requirement" links, built opportunistically during ingestion or by a later backfill pass. |

Indexes:
- `memory_chunks`: persistent on `(tenant_id, project_id, index)`, on `entity_id`, on `source_id`.
- `memory_sources`: persistent on `(tenant_id, project_id)`.
- `memory_links`: edge indexes on `_from` and `_to`.

### 8.2 Cosine similarity AQL (indicative)

Cosine of two float arrays of equal length:

```aql
FUNCTION UTILS::COSINE(a, b) = (
  SUM(FOR i IN 0..LENGTH(a)-1 RETURN a[i] * b[i])
  / (SQRT(SUM(FOR x IN a RETURN x*x)) * SQRT(SUM(FOR y IN b RETURN y*y)))
)
```

Registered once at ensure-collections time; invoked by the retrieval query:

```aql
FOR c IN memory_chunks
  FILTER c.tenant_id == @tenant AND c.project_id == @project
     AND c.index IN @indexes AND c.model_id == @model_id
     AND (c.status == 'active' OR @include_deprecated)
  LET score = UTILS::COSINE(c.vector, @query_vector) * @weights[c.index]
  SORT score DESC
  LIMIT @top_k
  RETURN { chunk_id: c._key, score, content: c.content, metadata: c.metadata }
```

Beyond 50 000 chunks per scope, this query degrades; filters (source_id, category, etc.) are expected to narrow the scan before the SORT.

---

**End of 400-SPEC-MEMORY-RAG.md v7 (D-020 v2 optimisations — embeddings@C13, screener trail, manifest stamps).**
