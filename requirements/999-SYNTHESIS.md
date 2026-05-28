---
document: 999-SYNTHESIS
version: 8
path: requirements/999-SYNTHESIS.md
language: en
status: draft
---

# Platform Synthesis — Requirements-Driven Conversational Artifact Generation

> **Purpose of this document.** Anchor the cross-cutting decisions that constrain all detailed specs (`100`–`800`) and `meta/`. This is the single source of truth for platform-wide trade-offs. Detailed technical specifications live in their respective documents; this document holds the **why** and the **what**, not the **how**.

> **Version 4 changes.** Clarification of `D-001` (bumped to `version: 2`): StrictDoc is adopted as a **tooling library** (validation, traceability matrices, export), not as the **native storage format**. The native storage format is Markdown with YAML frontmatter, already used by the delivered specs. This clarification resolves an ambiguity surfaced during the `300-SPEC-REQUIREMENTS-MGMT` design discussion; no other decision is impacted.

> **Version 5 changes.** New `D-014`: CI/CD platform decision — GitHub Actions on `push` to `main` for the test/coherence pipeline + GHCR (`ghcr.io/<owner>/aywizz-api`) for the API image, with image publication gated on test success via `workflow_run`. Cross-cuts §5 by introducing the supply-chain decision that was implicit until now.

> **Version 6 changes.** New `D-016` (layered knowledge representation + iterative deterministic-first retrieval, **extending** `D-010`), `D-017` (evaluation as a first-class instrument), and `D-018` (two-tier intent/evidence artifact layer on MinIO). These record the architecture-evolution direction validated during the May-2026 research review. Only the **v1-compatible subset** of `D-016` (schema-guided extraction, hybrid BM25+dense+RRF retrieval, prompt-cached cumulative chunk contextualisation) lands now and is operationalised in `400` (v3); the layered-graph L2/L3 layers and the full iterative-retrieval loop remain **v2** per `D-010` staging. `D-017`/`D-018` are recorded as direction; their spec operationalisation (`600`/`700`/`800` and `100`/`050`) is queued.

> **Version 7 changes.** New `D-020` (AyExtractor adopted as external extraction & chunking dependency, **C13**). Re-partitions the D-013 ingestion pipeline: C13 owns Phase 1 (extract) + Phase 2 (chunk + optional decontextualise / summarise / densify) and writes MinIO artifacts (markdown + jsonl + run manifest); C12 (n8n) owns trigger + status tracking; C7 owns embed + index only. Zero code coupling (`ay_platform_core/` does not `import ayextractor`). v1 scope = Phase 1+2 only; KG / consolidator / cross-doc layers are explicitly out of scope (queued via Q-200-022). LLM-frugal stance: libraries / heuristics prioritised; LLM calls reserved for image vision (mandatory) and opt-in `quality_tier` upgrades (`minimal | standard | high`, default `minimal`). Cross-cuts `100` (R-100-081 v2, R-100-125), `400` (R-400-020/021/022 v2 + R-400-220..225) and `800` (R-800-130..133 agent_routes). Operationalises in sessions 2-7 (see CHANGELOG).

> **Version 8 changes.** `D-020` bumped to **v2** with five quality/cost optimisations validated during the May-28 review: (a) **2-tier LLM gating** for the decontextualiser — a cheap Haiku "screener" decides per-chunk whether the full Sonnet decontextualisation is needed (catches semantic ambiguities a regex would miss while saving 30-50% of expensive calls). (b) Mandatory **prompt-caching structure** for sliding-window prompts in decontextualiser + summariser (60-80% cost reduction on cached input tokens). (c) Mandatory **intra-document image deduplication** by sha256 before image_analyzer (skips redundant Vision calls). (d) **Embeddings produced by C13**, not C7: C13 writes `embeddings.jsonl` next to `chunks.jsonl` so the artifact set is byte-exact reproducible per R-400-207, and C7 `/ingest-chunks` becomes a pure Arango INSERT (no compute). (e) Opt-in **batch API mode** (`urgency: interactive|background`) routing Phase 2 LLM calls through provider batch endpoints (Anthropic Batch API → -50% cost, 1-24 h latency). New `Q-200-028` opens adaptive Phase 1 (skip tables / vision when not needed) deferred to v2 under measured eval. Cross-cuts `100` (R-100-125 v2), `400` (R-400-220..223 v2), `800` (R-800-131/132 v2 + new R-800-134 screener).

---

## 1. Context & Vision

The platform enables users to produce software artifacts — and, progressively, other kinds of structured deliverables — by **conversing with AI under a disciplined engineering process**, not by directly typing the artifact. It replaces the "vibe coding" / "vibe authoring" paradigm — fast but unverified — with a **requirements-driven conversational generation** model where every produced artifact is traceable to an explicit, versioned requirement, and every requirement is continuously validated against its implementation.

The platform targets users who want AI-accelerated delivery **without** sacrificing the rigor expected in regulated or high-stakes contexts (automotive cybersecurity, ASPICE, safety-critical systems). It is deployed on Kubernetes (local via Docker Desktop, production via AKS), reusing an existing stack of open-source components (MinIO, ArangoDB, n8n).

**Production domain model.** The platform operates on a notion of **production domain**: a class of artifact for which a dedicated generation and validation pipeline exists. v1 implements the `code` domain exclusively. Subsequent versions progressively add other domains (`documentation`, `presentation`, ...) by registering new pipelines against the same architectural backbone.

**Primary user flow** (domain-agnostic):

1. The user describes a need through a structured conversational UI.
2. The platform decomposes the need into **requirements** (stored as Markdown with YAML frontmatter, indexed via StrictDoc tooling), refines them through human-validated iterations, and commits them to a versioned corpus.
3. A pipeline of specialised AI agents drafts design, plans implementation, generates the target artifacts and their domain-specific validation artifacts (tests for `code`, acceptance checks for other domains), and performs dual review.
4. Continuous **vertical coherence checks** (parameterised per production domain) enforce bidirectional traceability between requirements and produced artifacts.
5. Released artifacts are persisted to MinIO and optionally pushed to an external destination via n8n (Git remote for `code`, file share for `documentation`, etc.).

**External source ingestion.** Users provide documents, images, and other sources to enrich the platform's RAG context (project-specific knowledge base). The ingestion pipeline is a first-class platform capability, aligned with prior work done on the simplechat project.

---

## 2. Glossary

| Term | Definition |
|---|---|
| **Entity** | Any trackable object with a stable identifier and YAML frontmatter (requirement, decision, validation artifact, etc.). |
| **Requirement (`R-XXX`)** | A unit of expected behaviour, constraint, or property. Lives in a `SPEC-*.md` file. |
| **Decision (`D-XXX`)** | A cross-cutting architectural or methodological choice. Lives in `999-SYNTHESIS.md`. |
| **Validation artifact (`T-XXX`)** | Executable or verifiable acceptance criterion tied to one or more requirements. For `code` domain: a test. For future domains: a checklist, an approval gate, an automated content check. |
| **Production domain** | A registered class of artifact with its dedicated generation pipeline and validation engine (e.g. `code`, `documentation`, `presentation`). |
| **Platform level** | Requirements that are invariant across all projects hosted on the platform. |
| **Project level** | Requirements scoped to a specific user project, potentially tailoring platform-level requirements. |
| **Tailoring** | The act of overriding or refining a parent requirement with an explicit marker and justification. |
| **Vertical coherence** | Bidirectional, machine-checkable alignment between the requirements corpus and the produced artifact corpus. Check set is parameterised per domain. |
| **Sub-agent** | A fresh, context-isolated AI worker dispatched to perform a single bounded task within the pipeline. |
| **Hard gate** | A non-negotiable checkpoint in the pipeline that cannot be bypassed without explicit human override. Formulation is domain-agnostic. |
| **`@relation` marker** | Inline artifact annotation declaring that a block implements, validates, or otherwise relates to a specific entity. Syntax per artifact format; defined in `meta/100-SPEC-METHODOLOGY.md` §8. |
| **External source** | A user-uploaded document (PDF, DOCX, image, etc.) ingested to enrich the project's RAG context. Distinct from produced artifacts. |
| **Storage format** | The native on-disk format for requirement documents: Markdown with YAML frontmatter. |
| **Tooling library** | StrictDoc used as a library for validation, traceability matrices, and HTML export, without adopting its native `.sdoc` format. |

---

## 3. Guiding Principles

1. **Rigor before helpfulness.** The platform prefers refusing or pausing over generating unverified work. Speed is a consequence of discipline, not a trade-off against it.
2. **Traceability by construction.** No requirement exists without a path to a produced artifact and its validation; no produced artifact exists without a path to at least one requirement.
3. **Open source reuse over reinvention.** Every platform capability must first be evaluated against existing mature OSS projects, including prior internal work (simplechat, AyExtractor). Custom development is justified only when no acceptable alternative exists.
4. **Human-in-the-loop at gates.** Autonomy is maximized within phases; human approval is mandatory at phase transitions (spec approval, plan approval, release).
5. **Provider-agnostic by design.** The platform abstracts LLM providers so that any agent can run on any compatible model without application code changes.
6. **Simplicity first.** When two designs satisfy the same requirements, the simpler one wins. Complexity must be justified by a concrete requirement, not by speculative future needs.
7. **Sub-agents over monolithic context.** Long-running tasks are decomposed into fresh, context-isolated sub-agents to preserve focus and auditability.
8. **Domain-extensible backbone, domain-specific pipelines.** The architectural backbone (orchestration, requirements management, memory, LLM abstraction) is domain-agnostic. Generation and validation logic is packaged per production domain and registered against the backbone.

---

## 4. High-Level Architecture

```mermaid
flowchart TB
    User([User])

    subgraph UI["UI Layer (500)"]
        ChatUI[Conversational UI]
        ExpertMode[Expert Mode Panel]
        Upload[Source Upload]
    end

    subgraph Orchestrator["Pipeline Orchestrator (200)"]
        Architect[Architect Agent]
        Planner[Planner Agent]
        Generator[Generator Agent<br/>domain-specific]
        SpecReviewer[Spec Compliance Reviewer]
        QualityReviewer[Artifact Quality Reviewer]
    end

    subgraph Core["Core Services"]
        ReqMgmt["Requirements Mgmt<br/>MD+YAML / StrictDoc (300)"]
        Validation["Validation Engine<br/>domain-specific (700)"]
        Memory["Memory & RAG<br/>(400)"]
        Quality["Artifact Quality<br/>Engine (600)"]
    end

    subgraph Infra["Infrastructure (100)"]
        K8s[Kubernetes]
        EphemeralPods[Ephemeral Pods<br/>per sub-agent]
        MinIO[(MinIO)]
        ArangoDB[(ArangoDB<br/>vector + graph)]
        n8n[n8n Workflows<br/>+ ingestion]
    end

    subgraph LLM["LLM Abstraction (800)"]
        LiteLLM[LiteLLM Proxy]
        Providers[Anthropic / OpenAI /<br/>Google / local / ...]
    end

    User --> ChatUI
    User --> Upload
    ChatUI -.expert toggle.-> ExpertMode
    ChatUI --> Orchestrator
    Upload --> n8n
    n8n -->|ingested content| Memory
    Orchestrator --> Core
    Orchestrator --> LLM
    Core --> Infra
    LLM --> Providers
    Orchestrator -.dispatches.-> EphemeralPods
```

The orchestrator drives the five-phase pipeline (brainstorm → spec → plan → generate → dual review), parameterised by the active production domain. Each phase may spawn ephemeral sub-agent pods with isolated context. All LLM calls route through the LiteLLM proxy. The requirements corpus, generated artifacts, and ingested sources live in MinIO and ArangoDB; n8n handles ingestion, post-release workflows, and external sync.

---

## 5. Cross-cutting Decisions

Each decision below is a trackable entity. Detailed specs reference these identifiers via `derives-from:` or `impacts:` fields.

### 5.1 StrictDoc adoption

```yaml
id: D-001
version: 2
status: approved
category: tooling
impacts: [R-300-*, R-600-*, R-700-*]
```

**Decision.** StrictDoc is adopted as a **tooling library** for requirements management. StrictDoc provides validation, traceability matrices, relationship graph analysis, and HTML export. The platform **does NOT** adopt StrictDoc's native `.sdoc` format as its on-disk storage. The native storage format is **Markdown with YAML frontmatter**, as used by the specs already delivered (`999-SYNTHESIS.md`, `meta/100-SPEC-METHODOLOGY.md`, `100-SPEC-ARCHITECTURE.md`).

**Rationale.**
- Markdown + YAML is already the format used throughout the platform's own specs; consistency matters.
- Markdown renders natively in Git forges, IDEs, and documentation viewers without tooling.
- YAML frontmatter is parseable by every language ecosystem and supports arbitrary custom fields (needed for `derives-from`, `impacts`, `tailoring-of`, `override`, `supersedes`, `superseded-by`, `deprecated-reason`, `domain`) without inventing a custom DSL.
- StrictDoc's value is in its validation rules, matrix generation, and export — all of which can operate on a projected in-memory model built from Markdown + YAML.
- Avoids maintaining a custom StrictDoc grammar file to express our extended entity schema.

**Alternatives considered.**
- Native `.sdoc` format with custom grammar: rejected; higher maintenance cost, breaks git/IDE rendering parity.
- Sphinx-needs: rejected in v1 of this decision (heavier, Sphinx-coupled).
- Doorstop: rejected (less active, weaker tooling).
- Custom format without any library: rejected; reinvents traceability matrices and coherence rules.

**Consequences.**
- `300-SPEC-REQUIREMENTS-MGMT.md` specifies the Markdown + YAML schema and the adapter that converts it to StrictDoc's in-memory model.
- StrictDoc is consumed as a Python dependency of C5 (Requirements Service) and C6 (Validation Pipeline Registry).
- Extensions or bug fixes to StrictDoc are contributed upstream where possible.
- The MCP Server (C9) exposes CRUD tools that operate on the Markdown + YAML format; StrictDoc is an implementation detail behind the API.

---

### 5.2 Existing stack reuse

```yaml
id: D-002
version: 1
status: approved
category: infrastructure
impacts: [R-100-*, R-400-*]
```

**Decision.** The platform reuses the existing stack: Kubernetes (Docker Desktop local, AKS production), MinIO for object storage, ArangoDB as unified vector + graph store, n8n for ingestion and automation workflows. No substitution is planned for v1.

**Rationale.** Existing operational familiarity; ArangoDB uniquely combines vector and graph in one engine; n8n already operational for automation.

**Risk flagged.** ArangoDB native vector search is younger than Qdrant/pgvector (available since 3.12, late 2024). Acceptable for expected volumes; plan B (pgvector or Qdrant as satellite) is documented in `100-SPEC-ARCHITECTURE.md`.

**Consequences.** Infrastructure specs assume these components as primitives. No abstraction layer is introduced to allow swapping them in v1.

---

### 5.3 Core library + three invocation surfaces

```yaml
id: D-003
version: 1
status: approved
category: architecture
impacts: [R-600-*, R-700-*]
```

**Decision.** Validation and related platform capabilities are implemented as a **single Python core library** with three invocation surfaces:

1. **CLI** — for Git hooks, CI/CD, n8n automation, and power users.
2. **MCP server** — for LLM agents (internal pipeline agents and external tools like Claude Code or Cursor).
3. **Direct Python import** — for the platform backend, avoiding unnecessary serialization.

**Rationale.** Single source of truth; three native integration points; no duplicated logic. Inverse pattern (MCP-only with CI calling via MCP) adds a network hop without benefit.

**Consequences.** The core library has strict API stability requirements. MCP and CLI are thin wrappers.

---

### 5.4 tree-sitter for multi-language AST parsing

```yaml
id: D-004
version: 1
status: approved
category: tooling
impacts: [R-700-*]
```

**Decision.** `tree-sitter` is adopted as the default AST backend for validation checks on the `code` production domain. For Python-only analysis, the `ast` standard library plus `libcst` remains acceptable within the core library.

**Rationale.** De facto standard for multi-language parsing (used by Neovim, Atom, GitHub); uniform API across languages; required for the platform's multi-language roadmap within the `code` domain.

**Consequences.** Supported languages are declared explicitly per check in `700-SPEC-VERTICAL-COHERENCE.md`. Adding a language = shipping a tree-sitter grammar and its check plugins. Non-code domains will register their own parsing backends (e.g. PPTX structure parser for `presentation`).

---

### 5.5 Hierarchy platform/project with explicit tailoring

```yaml
id: D-005
version: 1
status: approved
category: methodology
impacts: [R-300-*, R-700-*]
```

**Decision.** Requirements follow a two-level hierarchy: `platform` (invariant across projects) and `project` (user-specific). A project-level requirement MAY diverge from a platform-level parent **only via an explicit `override: true` marker with a textual justification**. Silent divergence is a coherence violation.

**Rationale.** Option retained after trade-off analysis: stricter rule (no divergence ever) was too rigid; detection-only was too permissive. Explicit marker aligns with ISO 21434 tailoring practice and provides auditable rationale.

**Consequences.** The vertical coherence engine (`check #9`, cross-layer) treats missing `override: true` as a blocking finding. The user-facing UI surfaces tailoring markers distinctly from regular edits. Operational syntax defined in `meta/100-SPEC-METHODOLOGY.md` §7.

---

### 5.6 Vertical coherence scope

```yaml
id: D-006
version: 1
status: approved
category: functional-scope
impacts: [R-700-*]
```

**Decision.** The vertical coherence engine ships with a staged scope, initially populated for the `code` production domain.

**MUST (v1, blocking in CI)** — `code` domain:
1. Requirement without code
2. Code without requirement
3. Interface signature divergence (caller vs callee)
4. Test absent for requirement
5. Orphan test (no requirement referenced)
6. Obsolete reference (dangling path after rename/deletion)
7. Requirement ↔ code version drift
8. Data model drift (Pydantic, dataclasses, JSON Schema)
9. Cross-layer coherence (platform ↔ project with explicit `override`)

**SHOULD (v2, advisory — non-blocking)** — `code` domain:
10. Real execution coverage (integration with `coverage.py`)
11. Requirement dependency cycles
12. Semantic duplication of requirements (embedding-based)
13. SMART quality scoring of requirements (LLM-based)

**COULD (roadmap)** — `code` domain:
14. NFR coverage by appropriate test type
15. ADR ↔ requirements linkage

**Rationale.** MUST is fully deterministic (static analysis). SHOULD introduces AI-based, non-deterministic checks: advisory by design, never blocking. Explicit staging avoids scope creep in v1.

**Consequences.** v2 introduces a hard dependency on the embeddings and LLM stack (see D-010, D-011). The engine ships with a per-check enable/disable configuration. Future domains (`documentation`, `presentation`) register their own check suites against the same engine API.

---

### 5.7 Staff-engineer pattern adoption

```yaml
id: D-007
version: 1
status: approved
category: pipeline-design
impacts: [R-200-*, R-600-*]
```

**Decision.** The pipeline orchestrator is inspired by the `claude-code-staff-engineer` pattern (FareedKhan-dev). The v1 scope retains the following subset:

- **Five-phase pipeline**: brainstorm → spec → plan → generate → dual review (spec compliance then artifact quality).
- **Three hard gates** (formulated domain-agnostically):
  - no artifact generation before design approval;
  - no production-grade artifact without its validation artifact failing first (domain-appropriate form: failing test for `code`, unmet acceptance checklist for `documentation`, etc.);
  - no completion claim without fresh verification evidence.
- **Fresh sub-agents per task** with isolated context, materialized as ephemeral Kubernetes pods.
- **Four escalation statuses**: `DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, `BLOCKED`.
- **Three-fix rule**: after three failed fix attempts, the pipeline halts and surfaces an architectural-review request to the human.

**Deferred to v2.** Fine-grained model selection per task complexity; forensic debugging workflow (4-phase, 5-level root-cause); skill academy (self-authoring skills); dedicated Git worktree management layer.

**Excluded.** Visual companion (browser-side mockup renderer) — the platform's own UI supersedes this need.

**Rationale.** The pattern encodes battle-tested engineering discipline as an orchestrator contract. Adapted for a multi-user web platform rather than a single-developer CLI tool. Phase-level discipline is domain-agnostic; generation and validation logic per phase is domain-specific.

**Consequences.** `200-SPEC-PIPELINE-AGENT.md` becomes the most substantial functional spec. Each agent's LLM feature requirements (see D-011) are declared explicitly per phase. Domain-specific agent implementations register against a domain-agnostic pipeline contract.

---

### 5.8 Agent exposure: hybrid (invisible + expert mode)

```yaml
id: D-008
version: 1
status: approved
category: ux
impacts: [R-500-*, R-200-*]
```

**Decision.** The pipeline agents (Architect, Planner, Generator, Reviewers) are **hidden by default** from the end user, who experiences a single coherent conversation. A togglable **expert mode** exposes which agent is currently active, the pipeline phase, intermediate artifacts, and sub-agent dispatches.

**Rationale.** Default UX matches user mental model ("I'm talking to the platform"). Expert mode serves debugging, auditability, and power users (our target persona in regulated contexts).

**Consequences.** UI spec (`500`) defines both modes. Internal events (phase transitions, dispatches, escalations) must be emitted on a bus consumable by the UI regardless of mode, to avoid coupling agent logic to presentation.

---

### 5.9 Default language: English, overridable per document

```yaml
id: D-009
version: 1
status: approved
category: methodology
impacts: [all SPEC-*.md]
```

**Decision.** All platform-authored requirements documents default to English (`language: en` in frontmatter). Any document may override via a per-file `language:` field. User-authored project requirements follow the user's preference with no translation pipeline.

**Rationale.** English aligns with ISO 21434 and international conventions; per-document override accommodates native-language authoring where it improves precision.

**Consequences.** Tooling must not assume uniform language across the corpus. The vertical coherence engine operates on identifiers and structural relations, not natural-language content — language-agnostic by construction for MUST checks.

---

### 5.10 Graph-backed embeddings on ArangoDB — approach (A) + (α)

```yaml
id: D-010
version: 1
status: approved
category: memory-rag
impacts: [R-400-*, R-700-*]
```

**Decision.** The memory and RAG layer uses text embeddings (approach **A**: standard sentence-transformers applied to entity and source content) stored in ArangoDB, with similarity propagation leveraging the existing graph structure. Refresh strategy **α**: periodic recomputation (cron or commit-triggered). No node2vec/GraphSAGE in v1; no online fine-tuning; no feedback-loop re-ranker.

**Rationale.** Maximises value while minimising ML complexity. Node embeddings (B) and hybrid GraphRAG (C) are deferred to v2/v3 only if v1 performance is demonstrably insufficient. Feedback-loop improvement (β) is deferred to v2; continuous fine-tuning (γ) is out of scope.

**Consequences.** v2 SHOULD-scope vertical coherence checks (semantic duplication, SMART scoring) consume this layer. Both the requirements corpus and external sources (see D-013) are embedded; retrieval is federated across separated indexes. Specific embedding model choice and refresh cadence are detailed in `400-SPEC-MEMORY-RAG.md`.

---

### 5.11 Multi-LLM abstraction via LiteLLM

```yaml
id: D-011
version: 1
status: approved
category: architecture
impacts: [R-100-*, R-200-*, R-800-*]
```

**Decision.** All LLM calls route through a **single LiteLLM proxy** deployed in the Kubernetes cluster. Every agent uses the OpenAI-compatible endpoint (`/v1/chat/completions`) exposed by the proxy. Provider keys, routing rules, rate limits, and budget caps are centralised in the proxy configuration.

**Scoped staging**:
- **Level 1 — portability (MUST v1)**: one active provider at a time, swappable via configuration without code changes.
- **Level 2 — task-based routing (SHOULD v2)**: different providers/models per agent or per task complexity.
- **Level 3 — ensemble/fallback (COULD roadmap)**: parallel invocation and result aggregation for critical decisions.

**Rationale.** LiteLLM is the de facto open-source standard (100+ providers); proxy mode yields centralised observability, cost tracking, and zero application-code churn on provider change. Replaceable by any OpenAI-compatible alternative (Portkey, vLLM gateway) should it become necessary.

**Caveats (explicit).**
- **Feature parity is not automatic**: prompt caching, structured outputs, vision, tool calling, context window vary across providers. Per-agent LLM feature requirements are declared explicitly in `200-SPEC-PIPELINE-AGENT.md`.
- **Prompts are not portable for free**: v1 uses generic prompts with per-provider regression testing; v2 may introduce a per-provider prompt library if degradation is observed.
- **Quality monitoring**: a systematic eval harness across active providers is scheduled for v2.

**Consequences.** `800-SPEC-LLM-ABSTRACTION.md` is a first-class spec document. The orchestrator never embeds provider-specific logic.

---

### 5.12 Production domain extensibility

```yaml
id: D-012
version: 1
status: approved
category: architecture
impacts: [R-100-*, R-200-*, R-300-*, R-600-*, R-700-*]
```

**Decision.** The platform architecture is organised around the notion of **production domain**: a pluggable unit comprising a generation pipeline and a validation pipeline for a given class of artifact. The v1 implementation targets exclusively the `code` domain. The architectural backbone (Gateway, Auth, Conversation, Orchestrator, Requirements, Memory, LLM Gateway, MCP Server) SHALL be domain-agnostic. Generation and validation logic SHALL be packaged per domain and registered against the backbone.

**Roadmap:**
- **v1**: `code` domain only.
- **v2**: `documentation` domain (Markdown, HTML, PDF outputs with narrative coherence checks, completeness checks, reading-level scoring).
- **v3**: `presentation` domain (PPTX with slide-level structure, brand compliance, narrative arc).
- **v4+**: on-demand, based on user needs.

**Constraints on v1 design:**
- No backbone component SHALL hard-code "code", "test", "function", "class" or similar code-specific vocabulary in its public contracts. Internally, `code`-domain components may use such terms.
- Hard gates SHALL be formulated in terms of abstract concepts ("artifact", "validation artifact") rather than concrete code terms ("code", "test").
- The `@relation` marker mechanism SHALL be portable to non-code artifacts (Markdown docstrings, DOCX properties, PPTX slide notes, YAML sidecar files).
- Validation engine API SHALL be parametric on the domain, with check plugins registered per domain.

**Rationale.** Option B (full generalisation at v1) chosen over option A (minimal D-012 + refactor at v2). Justification: the refactor cost at v2 would exceed the v1 generalisation cost, as decisions on naming, contracts, and data model are cheaper to get right once than to revisit. Risk acknowledged: premature abstraction. Mitigation: no abstraction without a concrete v1 user (the `code` domain) exercising it.

**Alternatives considered.** Option A was the safer default per YAGNI principle but was rejected because:
- The refactor scope at v2 would touch every major spec.
- Several naming decisions (component names, entity type semantics, hard gate formulations) are effectively irreversible in v2 without breaking change.
- The user's medium-term intent is explicitly multi-domain; anchoring this now reduces uncertainty.

**Consequences.**
- `999-SYNTHESIS.md`, `meta/100-SPEC-METHODOLOGY.md`, and `100-SPEC-ARCHITECTURE.md` are refactored in sync with this decision (v3, v2, v2 respectively).
- The former "Analysis Engine" (C6) becomes a **Validation Pipeline Registry** hosting domain-specific validation plugins.
- The notion of "test" at methodology level is replaced by "validation artifact" (`T-` entity type retained with broader semantics).
- Open question: registration mechanism for new domains at runtime vs at build time (deferred to v2 when the second domain lands).

---

### 5.13 External source ingestion

```yaml
id: D-013
version: 1
status: approved
category: memory-rag
impacts: [R-100-*, R-400-*, R-500-*]
```

**Decision.** The platform SHALL provide a first-class capability for users to upload external sources (documents, images, structured data) into a project's RAG context. Ingestion is handled by the combination of:

- **C12 (Workflow Engine, n8n)** — upload reception, per-type parsing, job orchestration (retry, progress, monitoring).
- **C7 (Memory Service)** — embedding computation and indexing into ArangoDB (both vector and graph collections).

No new component is introduced. Parsing, chunking strategy, and schema details SHALL align with the prior work done on the **simplechat** and **AyExtractor** projects to avoid reinventing the ingestion pipeline.

**v1 supported formats (option (i) — minimum set):**
- PDF
- Markdown (`.md`)
- Plain text (`.txt`)
- Images (PNG, JPG) with optional OCR

**Deferred to later versions:**
- v2: DOCX, PPTX, XLSX (office suite)
- v3: HTML, JSON/YAML/CSV, URL crawling
- v4: external Git repository cloning and ingestion

**Retrieval model.** The RAG retrieval is **federated across separated indexes**:
- Index `requirements`: owned by C5, contains the versioned requirements corpus.
- Index `external_sources`: owned by C7, contains ingested user-provided sources.
- Retrieval API supports querying either index, or both with explicit weighting.

**Rationale.**
- First-class ingestion is a user-facing value (users bring their own documentation, standards, legacy specs).
- Minimum v1 format set (option (i)) covers the highest-value cases without ballooning parsing library dependencies. Progressive enrichment per version.
- Reuse of simplechat / AyExtractor work respects Principle 3 (OSS reuse, including prior internal work).
- Federated retrieval with separated indexes prevents contamination (a PDF snippet being treated as a requirement).

**Pending.** Technical alignment with the simplechat and AyExtractor codebases is pending — the specification files have not yet been accessed. Specific decisions on parsing library selection (docling as current candidate baseline, pending confirmation), chunking strategy (size, overlap, structure-aware), deduplication mechanism, and ArangoDB collection schema are deferred to `400-SPEC-MEMORY-RAG.md` once alignment is done.

**Consequences.**
- `100-SPEC-ARCHITECTURE.md` (v2) adds requirements on C7 and C12 for ingestion.
- `400-SPEC-MEMORY-RAG.md` becomes a first-class spec (not only internal memory): it covers the ingestion pipeline, the parsing per format, the chunking and embedding strategies, and the federated retrieval API.
- `500-SPEC-UI-UX.md` includes the upload UX (drag-and-drop, progress, error handling, source management).
- RBAC extension: per-project source visibility and ownership, aligned with existing project roles (E-100-002).
- Quotas: storage quota per project/tenant for external sources (deferred details to Q-100 / `400`).

### D-014 — CI/CD platform: GitHub Actions + GHCR

```yaml
id: D-014
version: 1
status: approved
category: tooling
impacts: [R-100-100, R-100-117, R-100-123]
```

**Decision.** The platform's continuous integration and image publication SHALL run on **GitHub Actions** with images published to **GitHub Container Registry (GHCR)** under `ghcr.io/<owner>/aywizz-<tier>`.

**Pipeline shape.**
- `ci-tests.yml` triggered on `push` to `main`. Two parallel jobs: (a) `tests` — installs `ay_platform_core[all]`, invokes `ay_platform_core/scripts/run_tests.sh ci`, surfaces line-coverage and pytest summary in `$GITHUB_STEP_SUMMARY`, uploads `reports/latest/` as artifact, optionally publishes a coverage badge to a gist; (b) `coherence` — invokes `run_coherence_checks.sh`. Both jobs are blocking. The `--cov-fail-under=80` gate is enforced by `pyproject.toml` (R-100-123) — the workflow does not re-encode the threshold.
- `ci-build-images.yml` triggered by `workflow_run` of `ci-tests` (`conclusion == 'success'`). Builds `infra/docker/Dockerfile.api` with the monorepo root as context (per CLAUDE.md §4.5) and pushes `:latest`, `:main`, and `:sha-<short>` tags. The future `Dockerfile.ui` will follow the same pattern.

**Rationale.**
- GitHub Actions and GHCR are integrated with the repository, eliminate third-party credentials, and run on free minutes for public repos.
- Coupling image publication to test success via `workflow_run` keeps `:latest` from ever being published from a broken commit, without forcing a single-workflow design that would block the build job artificially.
- The CI invokes the same wrappers (`run_tests.sh`, `run_coherence_checks.sh`) that local development uses, preserving the parity goal of the wrapper-script pattern (CLAUDE.md §5.3).

**Consequences.**
- New top-level `.github/workflows/` directory in the monorepo (the only legitimate exception to the top-level whitelist of CLAUDE.md §5.2, imposed by the GitHub Actions runtime).
- A new non-functional requirement `R-100-123` in `100-SPEC-ARCHITECTURE.md` formalises the gate semantics and the image-naming convention.
- Production deployment to AKS is **out of scope** for this decision — it requires Azure credentials and is deferred until a deployment push.
- Coverage badge on the README depends on a one-time operator setup (gist + PAT in `secrets.GIST_SECRET` + gist id in `vars.COVERAGE_GIST_ID`). Until configured, the workflow status badges (native, zero-setup) are sufficient.

### D-015 — DocGen tool-use in v1 via chat-direct (hybrid path, with v2 migration)

```yaml
id: D-015
version: 1
status: approved
category: architecture
impacts: [R-200-130, R-200-131, R-200-150, R-200-151, D-007, D-011]
references: [analyses/aywiz-architecture-synthesis-v4.md]
```

**Decision.** The v1 documentation-generation flow SHALL be implemented as **chat-direct tool-use** : the conversation in C3 invokes a small catalogue of MCP-style tools (`create_document`, `update_document`, `read_document`, `list_documents`, `delete_document`) that mutate the project's MinIO tree directly through new C4 endpoints, **bypassing the 5-phase pipeline** (brainstorm → spec → plan → generate → review). The CodeGen flow continues to use the pipeline.

The platform commits to **migrate DocGen to the pipeline path in v2**, per the design recorded in `references/aywiz-architecture-synthesis-v4.md` (OpenHands SDK embedded in C15, routed through C8/LiteLLM, with C6 `docs` domain plugin), once the POC criteria of that document's Q13 are validated.

**Rationale.**

1. **Time-to-demo for DocGen** : the synthesis-v4 path requires standing up OpenHands, a C15 sub-agent pod template, an in-pod `aywiz_working` MCP server, a `docs` C6 plugin (link-check + markdown-lint), and the LiteLLM/Databricks egress wiring. Cumulative effort ~6-10 weeks before the first usable DocGen demo. The chat-direct path delivers a working DocGen flow in ~1-2 weeks.

2. **The user-facing experience the operator asked for** is conversational : *"l'IA via des tools décide/propose de créer un document directement dans MinIO, tout se fait via une conversation et des questions posées à l'utilisateur"*. The pipeline path (brainstorm → ... → review gates) introduces structural friction that's appropriate for code (where Gate B "tests must fail first" makes sense) but ill-fitting for iterative document drafting (where there's no "validation artifact" in the TDD sense).

3. **The synthesis-v4 path is not yet validated** : it explicitly requires a 2-week POC (Q13) before architecture amendment. v1 cannot block on that POC.

4. **The two paths are structurally separable** : the tools `create_document` / `update_document` / `read_document` / `list_documents` / `delete_document` are CRUD operations on the same MinIO + Gitea artifact surface that already exists (R-200-130..147). They can be invoked from chat-direct in v1 and from OpenHands in C15 in v2 — the underlying mutation API is the same. The migration is a re-wiring of the *caller*, not a rewrite of the surface.

**Scope of the chat-direct v1 path.**

- New C4 endpoint set `/api/v1/projects/{pid}/documents/{path}` (POST=create, PUT=update, GET=read, DELETE=delete) backed by `ArtifactsService.put_file` / `get_blob` / a new `delete_file`.
- A deterministic per-project artifact run id (`live-docs`) that holds the document corpus, status=RUNNING, file-count grows on each mutation. No `mark_completed` ; the run is never closed.
- **Per-mutation Gitea push** : each `put_file` triggers an immediate Gitea `create_or_update_file` (and a per-file commit). Replaces the batch-on-complete push of R-200-146.
- C3 chat receives tool definitions in its system prompt (OpenAI tool-call format) for projects with `profile=docgen`. The C3 server detects `tool_calls` in the LLM response, executes them by calling the new C4 endpoints, returns tool results, and loops until the LLM produces a final message.
- The UX ChatSidebar (already shipping) is extended to render tool-call invocations inline (e.g. `Editor: created docs/proposal.md`).

**Migration conditions to D-015 → synthesis-v4 (v2 path).**

The platform SHALL migrate DocGen to the pipeline path when ALL of the following hold :
1. POC Q13 of `aywiz-architecture-synthesis-v4.md` passes (OpenHands+LiteLLM+Databricks task completion ≥70%, latency p95 <500ms overhead, cost reporting unified).
2. The C6 `docs` domain plugin is specified (verifier = link-check + markdown-lint per §11.2 of synthesis-v4).
3. The C15 pod image with `openhands-ai` + tree-sitter is built and signed.
4. The `aywiz_working` in-pod MCP server (§6.3.6 of synthesis-v4) exposes at minimum `list_documents`, `read_document`, `create_document`, `update_document`, `delete_document` — same shapes as the chat-direct v1 tools, so the prompt + UX stay stable across the migration.

When all four are true, a new ADR (D-NNN) records the migration ; D-015 is marked `superseded`.

**Tech debt explicitly accepted.**

- Two paradigms coexist (pipeline for CodeGen, chat-direct for DocGen) until v2. Operators and reviewers must understand the split.
- Cost tracking for DocGen runs through C3 → C8 LLM calls (single egress preserved per R-100-011), but the per-run aggregation surface that exists for C4 pipeline runs (`c4_runs.cost`) has no analogue for chat-direct ; cost is queried at the C3 / C8 level.
- No Gate B / Gate C semantics for DocGen v1 ; quality validation will land with the C6 `docs` plugin in v2.
- The "live-docs" run model concentrates all docs in one artifact run per project. Historical snapshots (per-conversation, per-commit) are recoverable via Gitea history but not via the artifacts surface itself.

**Validation of the migration path.**

The synthesis-v4 design explicitly notes that the migration target (OpenHands + LiteLLM + Databricks) is "encapsulated behind a `pipeline/generate_engine.py` abstraction in C4 ; if OpenHands stops matching aywiz' needs ... the harness can be swapped". D-015 inherits this property : the DocGen v1 path is similarly encapsulated (the new `/documents` endpoints are stable across both paths), so v2 migration is a controller swap, not a surface rewrite.

### D-016 — Layered knowledge representation + iterative deterministic-first retrieval

```yaml
id: D-016
version: 1
status: draft
category: memory-rag
impacts: [R-400-*, R-700-*]
```

**Decision.** The memory & knowledge layer SHALL evolve from a flat embedding index toward a **stratified knowledge graph** in ArangoDB with four vertically-linked abstraction layers, queried by an **iterative, deterministic-first retrieval loop** rather than a single-shot top-k similarity lookup:

- **L0 — verbatim**: raw chunks / spans / entity bodies, stored without LLM rewriting, provenance `EXTRACTED`, confidence 1.0.
- **L1 — structural**: deterministically extracted symbols and relations (AST for the `code` domain via D-004's tree-sitter; frontmatter + headings for specs), provenance `EXTRACTED`.
- **L2 — semantic**: entities, relations, and topological communities with LLM-generated per-community summaries, provenance `INFERRED`.
- **L3 — thematic**: cross-cutting themes and recursive summaries, provenance `INFERRED`.

Vertical `derived-from` edges link every higher layer back to its L0 evidence, so the abstraction hierarchy is itself the traceability tree (Principle 2). Retrieval enters at the layer matching the query (thematic → L3, specific → L1/L2), then descends and traverses neighbour-to-neighbour, expanding the working set with **deterministic graph algorithms** (graph traversal / personalized-PageRank-style propagation in AQL) and invoking an LLM only to arbitrate ambiguous descend/stop decisions. The retriever **actively prunes** its working set as it traverses (context-rot mitigation).

**Relationship to D-010.** This decision **extends** D-010; it does not contradict it. D-010 keeps v1 deliberately simple (text embeddings + periodic refresh; "hybrid GraphRAG deferred to v2/v3 only if v1 performance is demonstrably insufficient"). D-016 records that GraphRAG-style direction as the v2+ target and authorises the **v1-compatible subset** to land early, because it improves quality without adding graph-ML complexity:

- **(a) Schema-guided extraction** — the L1 extractor SHALL use a closed entity/relation ontology for the `code` domain (`Function`, `Class`, `Module`, `Requirement`, `Decision`, `Test`, …) instead of the current open-domain extraction, which fragments semantically equivalent relations (the `KILLS`/`KILLED`/`SLAYS` problem). Schema-guided is the right call wherever the ontology is known (per the Iliad open-vs-schema study).
- **(b) Hybrid retrieval** — BM25 (ArangoSearch) + dense vector, merged by reciprocal rank fusion, replacing the pure-cosine v1 path (R-400-011). BM25 is deterministic and recovers exact matches (IDs, names, `R-NNN`) that dense search misses.
- **(c) Prompt-cached cumulative chunk contextualisation** — per-chunk context that situates each chunk in its document and resolves anaphora, generated with a small model (Haiku-class) and a cached document prefix (Anthropic "Contextual Retrieval" pattern).

The community/summary layers (L2/L3) and the full iterative loop remain **v2** per D-010's staging gate.

**Rationale.** Specs and code are inherently structured and hierarchical; flat top-k similarity loses global context and cross-references (the well-documented RAG chunking and re-derivation failures). A layered graph traversed iteratively retrieves a small focused subgraph progressively (the "research-in-a-library" model) instead of dumping many chunks at once, which both raises precision and cuts token cost. Anchoring the deterministic layers (L0/L1) and deterministic traversal first keeps the LLM as a last-resort arbiter, consistent with Principle 6 (simplicity) and the operator's deterministic-first preference.

**Consequences.**
- `400-SPEC-MEMORY-RAG.md` (v3) operationalises the v1-compatible subset (a/b/c) and records the L0–L3 layers + iterative loop as v2 requirements.
- C7's `kg/extractor.py` migrates from open-domain to schema-guided extraction (v1 dev item).
- C9 exposes a retrieval tool interface (`search` / `grep` / `read_document` / `prune`) so any retrieval backend (AQL-native today, a dedicated retrieval sub-agent later) is swappable behind a stable contract.
- Active-pruning and PageRank-style propagation depend on clean L1 edges; schema-guided extraction is therefore a hard prerequisite.

### D-017 — Evaluation as a first-class instrument (graded, provenance-tagged verdicts)

```yaml
id: D-017
version: 1
status: draft
category: architecture
impacts: [R-600-*, R-700-*, R-800-*]
```

**Decision.** The validation layer (C6) SHALL be extended from binary coherence findings to a **three-tier evaluation harness** emitting **graded verdicts**:

- **T1 — deterministic / reference-free**: compile, type-check, lint, tests, coverage, schema conformance, and the existing traceability-coherence checks — graded into a score, not only a pass/fail finding.
- **T2 — reference-based**: comparison against held-out, versioned golden datasets (structural/semantic match).
- **T3 — LLM-as-judge**: subjective quality (clarity, completeness, coherence) with rubrics and pairwise comparison.

Every verdict SHALL carry `score ∈ [0,1]`, `confidence`, a `method` provenance tag (`DETERMINISTIC | REFERENCE | JUDGED`), a `rationale`, and cited `evidence`. C6 SHALL persist per-entity (`R-NNN`) quality time series to enable **regression detection** (quality drift between runs), not just point-in-time gates. The T3 judge SHALL run on a **different model family** than the generator under test, exploiting D-011's provider-agnosticism to neutralise self-preference bias. Golden datasets SHALL be held-out and versioned and SHALL NOT be tuned against (anti "teaching-to-the-test").

**Relationship to prior decisions.** Operationalises D-011's "systematic eval harness … scheduled for v2" and gives a home to the direction of Q10 (synthesis) and Q-400-011 (memory). Provider-quality evaluation lives in `800`; generation- and coherence-quality evaluation **extends the C6 plugin contract** (D-003, D-006) in `600`/`700`. The eval anti-gaming rules mirror CLAUDE.md §10/§11.

**Rationale.** Without measurement, every architecture-evolution pattern is an unfalsifiable opinion, and a regulated platform cannot present quality evidence to an auditor (ISO 21434 / ASPICE require measurable validation). A graded, provenance-tagged verdict makes the epistemic status of each measurement explicit (a deterministic coverage number ≠ a judged clarity score) and turns the pattern backlog into a measured, prioritised one.

**Consequences.**
- `600-SPEC-CODE-QUALITY.md` / `700-SPEC-VERTICAL-COHERENCE.md` (queued) extend the `ValidationPlugin` contract with a graded `Verdict` alongside the existing binary `Finding`.
- `800-SPEC-LLM-ABSTRACTION.md` (queued) hosts the provider-quality eval harness (resolving Q10).
- C6 gains a quality-time-series persistence surface (ArangoDB + MinIO evidence per D-018).

### D-018 — Artifact layer: two-tier intent/evidence store on MinIO

```yaml
id: D-018
version: 1
status: draft
category: architecture
impacts: [R-100-*, R-400-*]
```

**Decision.** The MinIO artifact surface SHALL distinguish two artifact classes with different lifecycles:

- **Intent artifacts** (specs, plans, skills, guidance files): **versioned** (bucket versioning enabled), mutable-with-history, long-lived. Reviewed and refactored like code.
- **Evidence artifacts** (test results, eval verdicts, CI logs, review records, execution traces): **write-once / immutable** (object lock / WORM + retention) — the tamper-evident audit trail.

Every artifact object SHALL carry **provenance metadata** (originating `R-NNN`, producing run id, model/agent, eval verdict reference) that joins it to the ArangoDB knowledge graph (D-016): MinIO holds the blob, ArangoDB holds the edges, the object key is the join. Evidence artifacts feed the evaluation/regression loop (D-017).

**Relationship to prior decisions.** Extends D-013 (which introduced external-source ingestion to MinIO) and the existing artifact surface (R-200-130..147, D-015's `/documents` endpoints). It does not change those flows; it adds the intent/evidence lifecycle distinction and the WORM evidence guarantee on top.

**Rationale.** When agents implement, intent ("why") evaporates unless externalised durably (the "3-month wall"). Splitting intent (versioned, editable) from evidence (immutable) gives both a clean refactor story for intent and a tamper-evident audit trail for evidence — the latter is a hard requirement for ISO 21434 / ASPICE traceability, which WORM object-lock satisfies natively.

**Consequences.**
- `100-SPEC-ARCHITECTURE.md` / `050-ARCHITECTURE-OVERVIEW.md` (queued) document the two-tier bucket policy and the WORM evidence bucket.
- `400-SPEC-MEMORY-RAG.md` provenance metadata (R-400-012) is the template for the join keys.
- Object-lock requires MinIO buckets created with versioning + retention configured at bootstrap (`minio_init`).

### D-019 — Bi-temporal knowledge (valid-time + transaction-time, ArangoDB-native)

```yaml
id: D-019
version: 1
status: draft
category: memory-rag
impacts: [R-400-*]
```

**Decision.** The knowledge graph (D-016) SHALL support **bi-temporal**
records along two independent time axes, so the memory can answer both "what
was true in the world at time *t*" and "what did the system know as of time
*s*", and can correct facts without destroying history:

- **Valid time** (world / event time): `valid_from` and `valid_to` on KG
  records (nullable ; a null `valid_to` = currently valid). When the fact
  holds in the modelled domain.
- **Transaction time** (system / assertion time): `recorded_at` (when the
  system first asserted the record) and `superseded_at` (when it stopped
  asserting it). Writes are **append-only on change** — a correction CLOSES
  the prior record's transaction interval (`superseded_at`) and inserts a new
  record, linked by `superseded_by` / `supersedes` ; nothing is deleted.

The retriever and KG traversal SHALL support **as-of** filtering on either
axis : `valid_at(t)` (records whose valid interval contains *t*) and
`known_as_of(s)` (records whose transaction interval contains *s*). Both
axes are **opt-in / nullable** — a record with null intervals is timeless,
so existing D-016 records remain valid unchanged.

**Relationship to prior decisions.** Extends D-016 (which gave the KG
provenance + confidence + layers but no time) and complements R-400-201's
provenance. This is the "bi-temporal" property of Graphiti-class memory,
realised **ArangoDB-natively** (extra fields + AQL interval filters, append
-only on supersession) — NOT by adopting the `graphiti` package, which would
require a second graph store and break D-002's unified ArangoDB store.

**Rationale.** An agentic memory whose facts evolve must distinguish a fact
becoming false in the world from the system learning it was wrong, must
retain corrected facts (tamper-evident lineage for ISO 21434 / ASPICE
traceability), and must avoid destructive overwrites that erase the "what we
knew when" audit trail. Append-only bi-temporality gives all three with no
new store.

**Consequences.**
- `400-SPEC-MEMORY-RAG.md` gains the bi-temporal requirement (R-400-210) and
  the as-of retrieval semantics ; the structural KG records (E-400-006 /
  R-400-200) carry the temporal fields first (code/requirements facts evolve),
  chunks later.
- `c7_memory` KG persistence becomes append-only on content change
  (supersession), with `as_of_valid` / `as_of_known` query paths.
- Scope-staged : the schema-guided L1 KG first ; the open-domain extractor and
  chunk-level valid time are follow-ons.

### D-020 — AyExtractor adopted as external extraction & chunking dependency (C13)

```yaml
id: D-020
version: 2
status: draft
category: architecture
impacts: [R-100-081, R-100-125, R-400-020, R-400-021, R-400-022, R-400-220, R-400-221, R-400-222, R-400-223, R-400-224, R-400-225, R-800-130, R-800-131, R-800-132, R-800-133, R-800-134]
```

**Decision.** AyExtractor (the prior internal work hosted under
`ay_extractor/` in this monorepo) SHALL be adopted as the platform's source
extraction & chunking engine — exposed as a new **dependency component
C13**, not as an internal AyWizz module. The component boundary is **code-
isolated**: `ay_platform_core/` SHALL NOT `import ayextractor`, and
AyExtractor SHALL NOT import anything from `ay_platform_core/`. The sole
contract between the two is the **MinIO artifact layout** AyExtractor writes
+ a thin **HTTP trigger surface** (`POST /analyze`, `GET /status/{run_id}`,
`GET /healthz`) that C12 (n8n) calls.

The D-013 ingestion pipeline is **re-partitioned**:

- **C12 (n8n)** — trigger reception, MinIO put of the raw upload, HTTP call
  to C13, polling of C13 `status.json`, read of `chunks.jsonl` + `run_manifest.json`,
  POST to C7 `/ingest-chunks`, NATS `ingestion.source.indexed` publication.
  C12 owns **orchestration only**, no parsing logic.
- **C13 (AyExtractor)** — Phase 1 (extract: PDF / EPUB / DOCX / MD / TXT /
  image, structure detection, table extraction, reference extraction, image
  vision) + Phase 2 (chunking, optional decontextualisation, optional Refine
  summarisation, optional Chain of Density). Writes all outputs as durable
  MinIO artifacts (markdown / jsonl / per-image json) accompanied by a
  `run_manifest.json` stamped with the AyExtractor version, the per-agent
  LLM model+prompt hashes, and per-phase timing — enabling **byte-exact
  reproducibility** and **diff between runs**. C13 SHALL NOT read from any
  AyWizz store (no `import` functions surviving the refactor: `rag/retriever`,
  `rag/{vector,graph}_store`, `consolidator/`, `batch/` are physically
  removed).
- **C7 (Memory)** — accepts pre-chunked input via a new endpoint
  `POST /memory/projects/{pid}/sources/{sid}/ingest-chunks` taking the rich
  chunk shape (`text`, `original_text?`, `context_summary?`, `global_summary?`,
  section path, char offsets). C7 retains its embed + index responsibility
  in C11 (R-100-081) and DROPS its in-process parse + chunk pipeline (the
  current `ingest_uploaded_source` is deprecated in v1.5 and removed in v2).

**v1 scope (locked).** Phase 1 + Phase 2 only. Explicitly **out of v1**:
Phase 3 (concept extraction / triplets / Leiden community detection / entity
profiles / synthesis / critic agent), Phase 4 consolidator (cross-document
corpus graph), batch CLI, JSON/SQLite/Redis/ArangoDB cache backends (only
JSON-cache-on-MinIO survives). KG enrichment of ingested sources stays
within C7's existing `c7_memory/kg/` (D-016 v1 subset). The deferred phases
are tracked as `Q-200-022` (Phase 3 KG adoption timing) and `Q-200-023`
(cache fingerprint backend).

**LLM frugality (mandatory).** AyExtractor SHALL prefer deterministic
libraries / heuristics over LLM calls for any step where both yield
acceptable quality. The internal refactor SHALL:

- Replace the LLM-driven `extraction/structure_detector.py` with heuristics
  (regex on TOC patterns, indentation, fonts) — LLM as opt-in fallback only.
- Replace the LLM-driven `pipeline/agents/reference_extractor.py` with
  `refextract` + regex for citations / cross-references — LLM as opt-in
  fallback only.
- Keep `image_analyzer.py` LLM-driven (Vision is mandatory, no code
  equivalent).
- Make `decontextualizer.py`, `summarizer.py`, `densifier.py` opt-in via a
  per-document `quality_tier ∈ {minimal, standard, high}` setting. The
  default `minimal` issues **zero LLM calls** when the document contains no
  images. `standard` adds the Refine summariser. `high` adds the
  decontextualiser and the Chain of Density densifier (5 iterations).

All AyExtractor LLM calls SHALL route through **C8** by configuration
(`OPENAI_BASE_URL=http://c8:8000/v1` + `OPENAI_API_KEY=$C8_GATEWAY_API_KEY`).
No code change inside AyExtractor is required for this routing — its
existing `llm/adapters/openai_adapter.py` is OpenAI-API-compatible and C8
LiteLLM exposes the same surface. The C8 `agent_routes` table gains entries
for `ayextract.image_analyzer`, `ayextract.decontextualizer`,
`ayextract.summarizer`, `ayextract.densifier` (R-800-130..133). All four
provider-specific adapters (Anthropic / OpenAI direct / Google / OpenRouter)
under `ay_extractor/src/llm/adapters/` other than the OpenAI-compatible one
SHALL be physically removed.

**Backend consolidation (mandatory).** The internal AyExtractor refactor
SHALL physically remove modules duplicating AyWizz's stack:

- `src/rag/vector_store/{chromadb,qdrant}_adapter.py` — AyExtractor does not
  read or write any vector store; C7 owns indexing in C11 ArangoDB.
- `src/rag/graph_store/{neo4j,arangodb}_adapter.py` — idem; cross-document
  graph belongs to C7/C11.
- `src/storage/{local_writer,s3_writer}.py` — replaced by a new
  `minio_writer.py` (S3-API on C10 via `boto3`); MinIO is the sole artifact
  store.
- `src/llm/adapters/{anthropic,google,openrouter}_adapter.py` — provider
  routing centralised in C8 (R-100-011).

**Vendoring + versioning.** `ay_extractor/` stays vendored in the monorepo;
its `pyproject.toml` builds a wheel locally; the C13 image
(`infra/c13_extractor/`) installs that wheel. The `run_manifest.json`
stamps the wheel version (= `ay_extractor/src/version.py::__version__` +
the monorepo git sha at build time) so an artifact remains attributable to
an immutable AyExtractor build.

**Operationalisation (sessions 2-7, see CHANGELOG).**

1. (this session) Spec: D-020 + R-100-081 v2 + R-100-125 + R-400-020/021/022
   v2 + R-400-220..225 + R-800-130..133.
2. Strip dead modules from `ay_extractor/src/` (rag/retriever, vector_store,
   graph_store, consolidator, batch, graph, Phase 3 agents). Tests trimmed.
3. Refactor LLM→library for `structure_detector` + `reference_extractor`.
   Add `minio_writer.py`. Reroute writes through it.
4. HTTP wrapper FastAPI in `ay_extractor/src/api/http.py` + Dockerfile
   under `infra/c13_extractor/` + compose + k8s manifests. C8 routing wired
   via env. AyExtractor stays callable as a CLI; the HTTP surface is the
   one C12 uses.
5. C7 new endpoint `POST /ingest-chunks`. The in-process
   `ingest_uploaded_source` is marked deprecated.
6. n8n workflow `extract_and_ingest.json` v1 in `infra/c12_workflow/workflows/`.
   The two existing workflows (`ingest_text_source.json`,
   `chunk_and_track.json`) are deprecated and removed.
7. Cleanup: removal of C7's deprecated parse + chunk in-process. Regression
   tests on RAG retrieval. Ops documentation.

**v2 optimisations (mandatory unless explicitly tagged opt-in).** Five
quality/cost levers landed at D-020 v2 — all rendered MANDATORY in spec
except (E) which is OPT-IN per `urgency` request flag:

(A) **2-tier LLM gating for the decontextualiser.** A new cheap
`ayextract.decontextualizer_screener` agent (Haiku-class, ~50 input +
~10 output tokens per call) decides per-chunk whether the full Sonnet-
class decontextualiser is needed. The screener is invoked whenever the
project's effective `quality_tier == high`; its output is one of
`NEEDS_DECONTEXT: YES | NO` plus a brief reason logged for audit. The
expensive decontextualiser SHALL be invoked **only** when the screener
returns YES. Catches semantic ambiguities a regex / heuristic would miss
("l'entreprise", "the system", "as described above") while skipping 30-
50% of expensive calls on typical technical docs. Break-even
mathematically at ~2% skip rate (Haiku $0.0001 / call vs Sonnet $0.005
/ call). Materialised in R-800-134 (new screener agent) + R-800-131 v2
(decontextualiser is conditional on screener output).

(B) **Mandatory prompt-caching structure for sliding-window prompts.**
The decontextualiser and summariser SHALL structure their prompts as
`[stable_prefix] cache_control{type:"ephemeral"} [variable_suffix]`,
where the stable prefix contains the running summary + sliding-window
chunks N-K..N-1. Without this explicit structure the provider does not
key the cache and the per-chunk gain (10x cheaper on cached input
tokens at Anthropic and OpenAI) is lost. R-800-131 v2 + R-800-132 v2
now make the cache-control marker placement normative, not just
"prompt_caching support is required".

(C) **Mandatory intra-document image deduplication.** C13 SHALL compute
sha256 of each image extracted in Phase 1b and SHALL invoke
`ayextract.image_analyzer` exactly once per unique image hash within a
single document. The analysis result is then referenced by all chunks
embedding that image. Pre-existing in AyExtractor's spec §7 but made
normative for C13 via R-100-125 v2.

(D) **Embeddings produced by C13, not C7.** This is the architectural
shift: C13 SHALL call C8 `/embeddings` (via the same `OPENAI_BASE_URL`
config as the chat completion calls) and SHALL write `embeddings.jsonl`
alongside `chunks.jsonl` under `02_chunks/`. C7's
`POST /ingest-chunks` SHALL accept the embeddings field on each
ChunkRich and SHALL skip its own embedding compute when the field is
present — becoming a pure Arango INSERT path. The embedding model id +
version SHALL be stamped in `run_manifest.json` (R-400-221 v2). This
aligns with R-400-207 reproducible-rebuild mandate (which already
required embeddings to be durable MinIO artifacts) and removes the
"extraction is reproducible but embeddings aren't" inconsistency that
v1 D-020 left. C7 retains its embedder for the requirements-corpus
write path (R-400-030..032) — only the external-source ingestion path
moves embedding to C13.

(E) **Opt-in batch API mode.** `POST /analyze` SHALL accept a new
parameter `urgency ∈ {"interactive", "background"}` (default
`"interactive"`). When `"background"`, C13 SHALL route all Phase 2 LLM
calls (decontextualiser screener + worker, summariser, densifier)
through the provider's batch API (Anthropic Batch API or equivalent),
trading latency (1-24 h) for cost (-50%). The `status.json` SHALL
remain `running` until all batch jobs complete; C12 SHALL surface a
"batched" hint to the UI so the operator knows the run is non-
interactive. Mandated as opt-in only — `interactive` mode preserves
the sub-minute UX for ad-hoc uploads.

**Deferred.** Adaptive Phase 1 (skip table extraction when structure
detector reports zero tables; skip image vision on decorative images
detected by a cheap CV classifier) is deferred to v2 under
**Q-200-028**. The risk of false negatives (table mis-detected → content
lost; informative diagram dismissed as decorative → info lost)
outweighs the gain on typical documents. Revisit with measured eval
data.

**Relationship to prior decisions.**

- Extends `D-013` (external-source ingestion) — same architectural goal,
  but the C12-owns-parsing assignment of R-100-081 v1 is replaced by
  C12-owns-trigger + C13-owns-parsing/chunking. n8n becomes a true macro-
  orchestrator (3 HTTP calls), not a thin proxy to C7.
- Extends `D-002` (stack reuse) — AyExtractor is prior internal work; we
  re-use it instead of writing a parsing/chunking pipeline from scratch.
- Honours `R-100-011` (no direct LLM provider call) via the C8-routing
  configuration switch — AyExtractor is a peer of any AyWizz component
  on this rule.
- Does NOT change `D-016` retrieval logic — C13 produces chunks; C7 still
  owns extraction of the L1 structural KG from them (`R-400-200..202`).

**Rationale.** Two converging arguments:

1. **Reuse over rebuild.** AyExtractor already implements a sophisticated
   per-format extraction + structural chunking + (opt-in) summary +
   decontextualisation pipeline (~16 600 LOC). Re-implementing this in
   `c7_memory/ingestion/` would duplicate ~6-8 weeks of work for the same
   quality bar.
2. **R-100-081's spec/reality gap.** R-100-081 v1 attributed parse + chunk
   to C12 (n8n), but the existing implementation does it all in C7
   (`ingest_uploaded_source`). The current n8n workflows are thin
   proxies. Rather than build n8n custom parser nodes (operationally
   painful) or accept a permanent spec/reality drift, C13 lands the
   "parsing happens outside C7" intent of D-013 with a clean component
   boundary.

The LLM-frugal stance is a direct operator constraint surfaced during
the May-2026 design review: the platform SHALL NOT incur LLM cost where
deterministic code yields equivalent quality. Decontextualisation +
Chain of Density are powerful but expensive — defaulting them off and
making them opt-in puts cost control in the project owner's hands.

**Consequences.**

- `100-SPEC-ARCHITECTURE.md` §4.2 component table gains C13 (dependency
  type). R-100-081 v2 re-partitions ownership.
- `400-SPEC-MEMORY-RAG.md` §4.3 ingestion pipeline updated (R-400-020..022
  v2) + new §4.3bis lands C13 contracts (R-400-220..225).
- `800-SPEC-LLM-ABSTRACTION.md` §4.6 agent catalog gains four AyExtractor
  agent entries; §8.1 sample config lists them in `agent_routes`.
- `infra/c13_extractor/` (NEW) hosts the C13 Dockerfile + compose + k8s
  manifests, mirroring the `infra/c{n}_*/` per-component pattern (CLAUDE.md
  §4.5).
- `c7_memory.service.ingest_uploaded_source` deprecated in v1.5, removed
  in v2 (session 7).

---

## 6. Document Mapping

| Cluster | Document | Scope | Status |
|---|---|---|---|
| Infrastructure | `100-SPEC-ARCHITECTURE.md` | K8s topology, sandboxing, Python/Rust component split, deployment targets, domain-agnostic backbone, env-file architecture (§10) | **v3 delivered** |
| Pipeline | `200-SPEC-PIPELINE-AGENT.md` | Five-phase pipeline, hard gates, sub-agents, escalation, LLM feature requirements per agent, domain plug-in contract | **v2 delivered** |
| Requirements mgmt | `300-SPEC-REQUIREMENTS-MGMT.md` | Markdown+YAML storage, StrictDoc tooling, CRUD API, versioning, tailoring enforcement, import/export | **v1 delivered** |
| Memory / RAG | `400-SPEC-MEMORY-RAG.md` | Graph-backed embeddings, short/long-term memory, external source ingestion, federated retrieval; layered knowledge representation + iterative retrieval (D-016) | **v3 (in progress)** |
| UI / UX | `500-SPEC-UI-UX.md` | Conversational UI, expert mode, structured elicitation, requirements surface, source upload UX | planned (scaffold) |
| Artifact quality | `600-SPEC-CODE-QUALITY.md` | Domain-specific quality enforcement (TDD for `code`, equivalent per-domain gates), dual review, evidence-based verification | planned (scaffold) |
| Vertical coherence | `700-SPEC-VERTICAL-COHERENCE.md` | MUST/SHOULD/COULD checks per domain, parser internals, reporting, domain plugin registration | **v3 delivered** |
| LLM abstraction | `800-SPEC-LLM-ABSTRACTION.md` | LiteLLM deployment, routing, feature mapping, eval harness | **v1 delivered** |
| Methodology | `meta/100-SPEC-METHODOLOGY.md` | Authoring conventions, versioning, git flow, review process, domain-neutral entity model, test tier topology (§11) | **v3 delivered** |

---

## 7. Open Questions Deferred to Detailed Specs

| # | Question | Document | Owning decision |
|---|---|---|---|
| Q1 | Python/Rust split per component (which components require Rust?) | `100` | D-002 |
| Q2 | Exact LiteLLM deployment shape (sidecar per namespace vs shared service)? | `100`, `800` | D-011 |
| Q3 | Sub-agent pod lifecycle details (init container, MinIO sync strategy)? | `100`, `200` | D-007 |
| Q4 | Concrete StrictDoc API usage and adapter layer specifics. | `300` | D-001 |
| Q5 | Embedding model selection (specific sentence-transformers model, local vs API)? | `400` | D-010 |
| Q6 | Expert mode event bus design (WebSocket, SSE, backend-driven)? | `500` | D-008 |
| Q7 | Exact static-analysis toolchain per supported language? | `600`, `700` | D-003, D-004 |
| Q8 | LLM feature matrix per agent (caching required? structured output required?) | `200`, `800` | D-011 |
| Q9 | Per-check enable/disable configuration format for vertical coherence engine? | `700` | D-006 |
| Q10 | Eval harness design for v2 (golden dataset, metrics, schedule)? | `800` | D-011 |
| Q11 | Domain registration mechanism (build-time vs runtime, schema for domain plugins) | `200`, `700` | D-012 |
| Q12 | Technical alignment with simplechat/AyExtractor ingestion pipeline (parsing libs, chunking, schema) | `400` | D-013 |
| Q13 | Storage quotas per project/tenant for external sources | `100`, `400` | D-013 |

---

## 8. Roadmap (indicative)

**v1 — Foundation (code domain)**
- D-001 through D-013 at their v1-scoped level
- `code` production domain fully implemented
- Vertical coherence: MUST checks (#1–#9) enforced for `code`
- LLM abstraction: portability (level 1)
- Staff-engineer pattern: five phases + three hard gates + sub-agents + four statuses + three-fix rule
- Embeddings: text-based + periodic refresh
- External source ingestion: option (i) formats (PDF, MD, TXT, images)
- Single active LLM provider, swappable
- Memory (v1-compatible subset of D-016): schema-guided L1 extraction, hybrid BM25+dense+RRF retrieval, prompt-cached cumulative chunk contextualisation

**v2 — Discipline, Intelligence & Documentation domain**
- `documentation` production domain registered
- Vertical coherence: SHOULD checks (#10–#13) for `code`, initial MUST set for `documentation`
- LLM abstraction: task-based routing (level 2)
- Staff-engineer pattern: fine model selection, forensic debugging, skill academy
- Embeddings: feedback-loop re-ranking (β)
- External source ingestion: option (ii) formats (DOCX, PPTX, XLSX)
- Eval harness across providers
- Per-provider prompt library (if regression observed)
- Memory: layered knowledge graph L2/L3 (communities + summaries) + full iterative retrieval loop (D-016)
- Evaluation: graded three-tier verdicts + per-entity quality regression detection (D-017)
- Artifact layer: two-tier intent/evidence MinIO store with WORM evidence bucket (D-018)

**v3 — Optimisation & Presentation domain**
- `presentation` production domain registered
- Vertical coherence: COULD checks (#14–#15) for `code`
- LLM abstraction: ensemble / parallel invocation (level 3) for critical decisions
- Embeddings: node2vec / GraphSAGE / hybrid GraphRAG (if measured need)
- External source ingestion: option (iii) formats (HTML, JSON/YAML/CSV, URL crawling)
- Advanced worktree management
- Platform-wide observability and SLOs

**v4+ — Domain expansion on demand**
- External source ingestion: option (iv) Git repo ingestion
- Additional production domains registered based on concrete user needs

---

**End of 999-SYNTHESIS.md v8.**
