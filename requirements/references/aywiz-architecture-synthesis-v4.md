---
document: aywiz-architecture-synthesis
version: 4
path: analyses/aywiz-architecture-synthesis-v4.md
language: en
status: draft
derives-from: [D-007, D-011]
references: [050-ARCHITECTURE-OVERVIEW, 100-SPEC-ARCHITECTURE, 200-SPEC-PIPELINE-AGENT, 300-SPEC-REQUIREMENTS-MGMT, 400-SPEC-MEMORY-RAG, 500-SPEC-UI-UX, 600-SPEC-CODE-QUALITY, 700-SPEC-VERTICAL-COHERENCE, 800-SPEC-LLM-ABSTRACTION, 999-SYNTHESIS, analyses/claude-code-patterns-gap-audit]
changelog:
  - "v4: consolidated the architecture decisions surfaced during the visual-architecture review pass. (1) Introduced explicit User-facing layer C1 frontend, C2 backend API, C3 short-term session memory. (2) Made the two v1 use cases first-class entities (code generation and documentation generation, parameterised by C6 domain plugin). (3) Decomposed MCP services into an extensible set: Working MCP in C15, Static MCP in C9, Validation MCP in C9 (wrapping C6), placeholder for Future MCPs. (4) Decomposed C7's persistent stores explicitly (Vector DB, Graph DB, Bi-temporal Graphiti) — workers never query stores directly, they call MCP services which query the stores. (5) Introduced new component C13 Observability access layer as a façade over the OSS Grafana stack (OpenTelemetry Collector + Tempo + Loki + Prometheus). (6) Defined a logging-contract attribute set that every Cn must emit on every span and log line. (7) Codified the single-responsibility hosting rule and explicit notation 'runs in Cn / rules from Cm'. (8) Confirmed that C4 owns the agent definition code and C15 is a pure execution sandbox with no aywiz application code. (9) Reserved C14 placeholder for a v2 Continuous Improvement Layer that produces a developer to-do list from log analysis — never auto-applied."
  - "v3: pivoted the generate-phase engine from the Claude Agent SDK (Anthropic-locked) to OpenHands SDK routed through C8/LiteLLM, with Databricks as the example primary provider. This restores the full applicability of D-011 (multi-LLM portability) to every phase including generate, eliminates the C8 bypass risk, and preserves Claude advanced features (prompt caching, extended thinking) that Databricks now exposes natively. Risks 1 and 4 of v2 are retired; new Q13 added for the POC criteria; D-014 reformulated."
  - "v2: added §6.3 working-data stratification (filesystem / structural index / differential embeddings / git in-pod / test results); revised §6.2 scope to static reference data; added Q11–Q12 open questions; added working-data R-* requirements in §9; added next steps for tree-sitter and git-in-pod."
  - "v1: initial draft documenting SDK adoption for the generate phase."
---

# Agentic harness adoption — architecture synthesis for aywiz

> **Purpose.** This document is the entry-point handed to Claude Code to amend the aywiz architecture. Three concentric layers of decisions are encoded:
>
> 1. **Engine choice (v3 inheritance):** OpenHands SDK becomes the runtime engine of the `generate` phase, embedded in C4 and routed through C8/LiteLLM to any configurable OpenAI-compatible LLM provider (Databricks Foundation Model APIs as the reference deployment).
> 2. **Component map (v4 additions):** the architecture is reframed end-to-end with explicit User-facing layer (C1/C2/C3), explicit two-use-case scope (code generation and documentation generation), extensible MCP services catalogue, decomposed persistent stores in C7, single-responsibility hosting rule, and new components C13 (observability access) and C14 placeholder (continuous improvement, v2).
> 3. **Operational contracts (v4 additions):** a logging-contract attribute set every Cn must emit, a clear ownership statement that C4 owns the agent code and C15 is a pure execution sandbox, and the convention 'runs in Cn / rules from Cm' to distinguish runtime hosting from configuration source.
>
> The document is self-contained: a reader with no prior conversation context should be able to act on it. v4 supersedes v3.

---

## 1. Context

### 1.1 aywiz in one paragraph

aywiz is an AI-assisted platform for knowledge work, currently scoped to the `code` production domain in v1. Its backbone is a five-phase pipeline (`brainstorm → spec → plan → generate → dual review`) coordinated by an orchestrator (C4), feeding a requirements corpus (C5), a memory & RAG service (C7) running on ArangoDB's combined vector+graph engine, an LLM gateway (C8) running LiteLLM as the single egress to providers, and an MCP server (C9) exposing platform capabilities to external agents. Infrastructure runs on Kubernetes (Docker Desktop locally, AKS in production), with MinIO as object storage and n8n for ingestion and post-release automation.

### 1.2 Why this document exists

The platform's `generate` phase — where an agent produces code, tests, and supporting artifacts under hard-gate discipline — was originally specified to be implemented from scratch on top of the LiteLLM gateway. Two re-evaluations led to the present version:

1. **v1/v2 considered the Claude Agent SDK (Anthropic's official agentic harness)** as a way to skip rebuilding the agent loop, file checkpointing, hooks, sub-agents, context compaction, and MCP integration. The conclusion was that the SDK packages exactly what aywiz' v1 backlog was preparing to write.

2. **v3 reverses course on the choice of SDK** after two operational constraints surfaced: (a) the deployment context mandates that all LLM access goes through a configurable multi-LLM provider — Databricks Foundation Model APIs in the primary deployment scenario, with the option to switch to another OpenAI-compatible provider; and (b) the Claude Agent SDK is tightly coupled to the Anthropic native API and does not officially support Databricks. A migration to a provider-agnostic open-source harness becomes mandatory.

The recommended replacement is **OpenHands**, a production-grade open-source agentic harness (MIT license, 65k+ GitHub stars, Series A funded) that is provider-agnostic by construction, exposes a Python SDK suitable for embedding in C4, supports MCP natively, and ships built-in equivalents to every primitive aywiz needs from a Claude-Code-class runtime. Routed through C8/LiteLLM to Databricks, OpenHands gives aywiz the advanced Claude features it cares about (prompt caching, extended thinking, native tool calling, 1M-token context window on Opus 4.7) while preserving D-011 multi-LLM portability across **every** phase including `generate`.

This document records the v3 decision and lays out the integration contour. It supersedes the v2 design.

### 1.3 Two data natures, two strategies

A second re-evaluation that drove v2 of this document concerns the data the agent reads and produces:

- **Static reference data** — ingested user documents, requirements corpus, prior approved artifacts, the platform's own specs. These are stable enough that extracting a knowledge graph (entities, relations, communities) yields high-value retrieval primitives, and the cost of re-extraction on update is manageable because updates are rare and batched. **Strategy**: knowledge graph + vector index + graph traversal MCP tools. Lives in C7. This is what v1 of this document covered.

- **Working data** — source code being written, documentation being drafted, test results being produced, configs being edited inside an active run. These mutate at every tool call. Attempting to maintain a live knowledge graph over working data is anti-pattern: the extraction cost is high, the graph is obsolete before consolidation, communities thrash. **Strategy**: a stratified set of cheap, structural, incrementally maintained indices, scoped to the run, never aspiring to graph form. Promotion to static-data form happens only at well-defined boundary events (release, merge, approval). This is what v2 of this document adds in §6.3.

### 1.4 Reading order

Read sections sequentially. §3 establishes the verdict; §4 gives the bird's-eye component split; §5 maps a reference architecture against OpenHands capabilities; §6 describes the three data integration channels (filesystem sync for project files, MCP for static reference data, stratified working-data layers); §7 covers risks accepted; §8 enumerates open questions; §9 lists the specs that need amending; §10 proposes a concrete next-step ordering.

---

## 2. Strategic decision summary

### 2.1 The question

Which agentic harness should run the `generate` phase, given that (a) the deployment context requires all LLM access to flow through a configurable multi-LLM provider (Databricks or equivalent), and (b) building a custom harness from scratch on top of LiteLLM is feasible but represents a significant engineering investment that would always lag the open-source state of the art?

### 2.2 The verdict

**Adopt OpenHands as the engine of the `generate` phase**, embedded as a Python SDK in C4, with all LLM calls routed through C8/LiteLLM to the active primary provider (Databricks Foundation Model APIs in the reference deployment). The rest of the platform (C1-C9, C12, C15) remains as specified, including the gated 5-phase pipeline, the requirements management, the memory & RAG over static reference data, and the working-data layers introduced in v2 of this document.

### 2.3 Rationale in three points

1. **Reuse over reinvention, without lock-in.** OpenHands packages the same primitives the v1 backlog was preparing to build from scratch: agent loop, built-in tools (read/write/edit/bash/grep/glob equivalents), sub-agents, MCP client integration, session management, context compaction, hooks. Unlike the Claude Agent SDK, OpenHands is provider-agnostic by construction. The harness is MIT-licensed, has 65k+ GitHub stars, is funded with an $18.8M Series A, and is benchmarked at "50%+ of real GitHub issues solved" on SWE-bench. We get the engineering benefit of a mature harness with no Anthropic-specific lock-in.

2. **D-011 is fully preserved on all phases including `generate`.** The Claude Agent SDK route (considered in v2) forced D-011 to carve out an exception for `generate` (the SDK called Anthropic directly, bypassing C8/LiteLLM). OpenHands routes all LLM calls through LiteLLM, so the single-egress invariant (R-100-011) holds across the whole pipeline. The cost-cap motivator of D-011 (intra-Claude tier routing on cheaper agents) and the sovereignty motivator (local Ollama on sensitive workloads) become applicable to `generate` agents as well. The cost reconstruction collector that v2 introduced as a workaround is no longer needed.

3. **Advanced Claude features are preserved through Databricks pass-through.** As of mid-2026, Databricks Foundation Model APIs natively support `cache_control` (prompt caching), `reasoning` content type (extended thinking), `tool_calls` with caching, and the 1M-token context window on Opus 4.7. LiteLLM's `DatabricksConfig` was patched in October 2025 to preserve `cache_control` checkpoints through the proxy (`BerriAI/litellm#15801`). The net feature loss vs the SDK Anthropic direct route is limited to Anthropic's server-side proprietary tools (Bash, Memory, Computer use, Web search, Web fetch), which aywiz reimplements anyway as part of the working-data layers (R-200-036/037, §6.3).

### 2.4 What this decision is not

- It is **not** an abandonment of the Claude model family. aywiz still runs on Claude Opus/Sonnet/Haiku as the default LLM choice for production agents, just through Databricks (or another configurable provider) instead of Anthropic-direct. The `effort`-equivalent control, prompt caching, extended thinking, 1M context — all preserved.
- It is **not** a commitment to OpenHands forever. The `generate` engine is encapsulated behind a `pipeline/generate_engine.py` abstraction in C4; if OpenHands stops matching aywiz' needs (license change, deprecation, performance regression), the harness can be swapped — Goose (Linux Foundation), a custom LangGraph-based harness, or a future option — without changing the rest of C4.
- It is **not** a refusal of the Claude Agent SDK on principle. If a future deployment context relaxes the provider constraint (a tenant runs against Anthropic-direct), an alternative deployment configuration using the SDK is not precluded. The decision recorded here is about the **reference architecture** in the configurable-provider context.

---

## 3. Clean component split

| Concern | Owner |
|---|---|
| Pipeline orchestration, 5-phase lifecycle, hard gates A/B/C, escalation, three-fix rule | **aywiz C4** (unchanged) |
| `brainstorm`, `spec`, `plan`, `review` phase execution | **aywiz C4 + C8** (unchanged: LiteLLM-routed agents) |
| `generate` phase execution (agent loop, tool calls, sub-agents, file edits) | **OpenHands SDK** (new — embedded in C4) |
| Agent definitions (YAML+MD per agent), system prompts, tool surface configuration | **aywiz C4** (sole owner — code reviewed and versioned in C4 repo; pushed to C15 at pod init) |
| User-facing chat interface (web + mobile) | **aywiz C1** (Frontend) |
| User-facing API gateway, auth, run dispatch | **aywiz C2** (Backend API) |
| Short-term conversational memory (per chat, turn-scoped) | **aywiz C3** (Session memory) |
| Requirements corpus CRUD + versioning + traceability | **aywiz C5** (unchanged) |
| Validation pipeline registry + **domain plugins** (code + docs in v1) + vertical coherence | **aywiz C6** (extended: now hosts code-plugin and docs-plugin) |
| Memory & RAG over **static reference data** (vector, graph, bi-temporal embeddings) | **aywiz C7** (unchanged) |
| **Working-data** indices (per-run filesystem, structural index, in-pod git, test results) | **aywiz C15 pod-local + C4 orchestration** (new) |
| LLM gateway: routing, cost tracking, budgets, cap enforcement, cache-hit reporting | **aywiz C8** (single egress for all phases including `generate`) |
| Active primary LLM provider (reference deployment) | **Databricks Foundation Model APIs** (configurable, swappable per D-011) |
| MCP server exposing aywiz capabilities to external agents | **aywiz C9** (unchanged) |
| MCP server exposing aywiz capabilities to internal `generate` agents — **extensible catalogue** | **aywiz C9 extended** + **in-pod MCP in C15** — see §6.2 for the extensible service list |
| Ephemeral sub-agent pod template (pure execution sandbox, no aywiz application code) | **aywiz C15** (runtime image only: Python + openhands-ai + tree-sitter + git + mc) |
| Object storage + retention | **MinIO** (unchanged) |
| Graph + vector unified store (backing C7) | **ArangoDB** (unchanged) |
| Ingestion + post-release workflows + working-data → static promotion | **n8n** (workflows owned by C12) |
| Cognition patterns above the agent loop (Step-back, Self-refine, Best-of-N, ToT) | **aywiz C4** (new — built on top of OpenHands invocations) |
| **Observability access** for tenant-scoped consultation of traces / logs / metrics | **aywiz C13** (new — façade over the OSS Grafana stack) |
| OSS observability backbone (traces / logs / metrics infrastructure) | **External infra** (OpenTelemetry Collector + Tempo + Loki + Prometheus + Grafana — not an aywiz Cn) |
| **Continuous improvement layer** — passive log analysis producing developer to-do list (never auto-applied) | **aywiz C14 — v2 placeholder** (not in v1 scope, reserved in nomenclature) |

**Key observation:** OpenHands occupies exactly one cell of the table. Everything else is either unchanged or extended in a self-contained way. There is no zone where two systems claim ownership of the same concern.

---

## 4. Reference architecture → OpenHands mapping

A reference architecture (originating from a community analysis of an agent design for research-paper reproduction, not an official Anthropic schema) catalogues 22 logical components organised in seven layers: Setup, Workers, Cognition, Watchers, Hardening, Outputs, Infrastructure. It serves as a useful checklist of what an advanced agent might need.

Status legend: ✅ native in OpenHands (or directly available through standard libraries it integrates), 🟡 primitive exists but requires wiring, ❌ not in OpenHands (must be built or sourced elsewhere).

| Component | Status | Where it lives in aywiz |
|---|---|---|
| Problem Classifier (Convergent / Divergent) | ❌ | C4 (brainstorm phase logic) |
| Definition of Done (5 contract criteria) | ❌ | C4 + C5 (plan phase output + Gate B contract) |
| Paper Analyzer (Extract math + priors) | 🟡 | C4 sub-agent invoked via OpenHands in `generate` |
| Code Implementer (Architect / Editor split) | ✅ | Two OpenHands agent definitions (architect-style + editor-style) |
| Experimenter (Run inference in sandbox) | ✅ | OpenHands bash tool inside the pod |
| Verifier (Run pytest on artifacts) | ✅ | OpenHands bash tool + Gate C in C4 |
| Report Writer (Compose REPORT.md) | ✅ | OpenHands agent definition with write permission |
| Thinking Channel (Reason before answer) | ✅ | Extended thinking via Databricks `reasoning` content type, passed through LiteLLM |
| Compute Allocator (Adaptive token budgets) | 🟡 | Per-agent model + reasoning effort declared in agent config; LiteLLM routes per agent header |
| Best-of-N Sampler (Verifier picks winner) | ❌ | C4 custom pattern: N parallel OpenHands invocations + scoring |
| Tree of Thoughts (Explore - score - prune) | ❌ | C4 custom pattern (v2 SHOULD) |
| Step Back Reasoner (Principle then specifics) | ❌ | C4 prompt template, no runtime primitive |
| Trace Tree (Span tree per call) | ✅ | OpenHands event stream + OTel exporter to platform observability |
| Budget Guard | ✅ | C8/LiteLLM enforces caps natively per agent / tenant (R-800-070) |
| Linter Gate (Auto-revert bad code) | ✅ | OpenHands post-action hook on file edits + rollback via in-pod git |
| Self Refine Loop (Critique then improve) | 🟡 | C4 pattern: critic sub-agent + re-invoke |
| Sandbox REPL (Persistent Docker) | ✅ | C15 pod with OpenHands runtime |
| MCP Tool Registry (12 typed tools) | ✅ | OpenHands MCP client + C9-extended (static) + in-pod MCP (working data) |
| Task DAG (SQLite, 8 subgoals) | 🟡 | OpenHands has task/subtask model; persistence mirrored to ArangoDB |
| Bi-Temporal Memory (valid_from / valid_to) | ❌ | C7 — candidate: Graphiti (already in memory-lib shortlist) — for static data only |
| Vector Store (bge-m3 or equivalent) | ❌ | C7 (ArangoDB vector collection, embeddings via sentence-transformers locally) — for static data only |
| Git Checkpointer (Audit trail per step) | ✅ | In-pod git repo with post-action hook (R-200-036) for durable audit and rollback |

**Counts:**

- ✅ OpenHands-native (or trivially available): 11
- 🟡 Primitive + wiring: 4
- ❌ Custom (must be built/sourced): 7

The seven ❌ components are precisely the ones that constitute aywiz' value-add over a vanilla OpenHands session: orchestrator-level discipline (gates, classifier, DoD), advanced sampling strategies (Best-of-N, ToT), bi-temporal memory, RAG. None of them is on OpenHands' roadmap; building them is the platform's actual differentiation.

---

## 5. Component descriptions

This section describes each component referenced in §4, with its role, its OpenHands status, its aywiz placement, and notes for the implementer. Components are grouped by layer.

### 5.1 Setup-phase components

#### Problem Classifier (Convergent / Divergent)

A classifier that, given a user query, decides whether the request is **convergent** (one correct answer to converge on — e.g. "implement an authentication module per OWASP") or **divergent** (an exploration space to map — e.g. "what architectural styles fit this problem?"). Convergent problems unlock the standard 5-phase pipeline; divergent problems may need additional brainstorming sub-phases or a Tree-of-Thoughts strategy. **OpenHands status: ❌ not provided.** This is product logic: it lives in C4's brainstorm-phase entry, implemented as a single LLM call against C8 with a structured-output classifier prompt. v1 may keep it minimal (binary tag); v2 may refine taxonomy. The classification outcome should be persisted in `c4_runs` so downstream cognition patterns can be triggered conditionally.

#### Definition of Done (5 contract criteria)

The explicit, machine-checkable success criteria the run will be evaluated against. For the `code` domain this is a list of acceptance criteria mapped to gates B and C: validation artifacts must exist, must initially fail, must subsequently pass, must be linked to requirements (via `@relation` markers), and must demonstrate no drift from the spec. **OpenHands status: ❌ not provided.** This is precisely what C4's hard-gate mechanism (R-200-010..013) already encodes, augmented by C5's tailoring and traceability data. The Definition of Done is *the* artefact of the `plan` phase, persisted in `c4_runs.plan` and consulted by both spec-reviewer and quality-reviewer. OpenHands doesn't replace it; it consumes it as part of the `generate` agent's input bundle.

### 5.2 Worker components (the `generate` chain)

#### Paper Analyzer (Extract math + priors)

In the reference architecture, this is the agent that reads the source paper, extracts equations, identifies assumptions, and produces a structured artifact (math + priors) the downstream implementer consumes. **OpenHands status: 🟡 wiring needed.** This is a specialised sub-agent that OpenHands can host as an agent definition, but the *content* of its prompt and its output schema are entirely domain logic. For aywiz, this maps onto the `architect` agent's pre-generate output during `brainstorm`/`spec`, with the structured artifact stored in the requirements corpus (C5) and pointed to from the plan. The implementer agent during `generate` then reads it as an input artifact. OpenHands reads it via its native file-read tool from the pod's mounted workspace.

#### Code Implementer (Architect / Editor split)

The dual-role pattern where an "architect" sub-agent designs the implementation, then an "editor" sub-agent makes the actual file edits. The split improves quality: the architect doesn't get distracted by mechanical edits, the editor doesn't drift from the design. **OpenHands status: ✅ native.** Implementable as two OpenHands agent definitions with distinct allowed tool sets: architect with read/grep/glob (read-only inspection); editor with read/write/edit/bash (mutation rights). C4 invokes them in sequence inside one `generate` run, passing the architect's output as context to the editor.

#### Experimenter (Run inference in sandbox)

The agent runs the produced code in a sandbox environment to observe its behaviour — for the reference use case, running model inference; for aywiz' `code` domain, running the test suite. **OpenHands status: ✅ native.** This is OpenHands' bash tool against the pod's emptyDir workspace. The pod itself is the sandbox (network policy enforced by R-200-031; no internet egress except to C8, C9-extended, and MinIO). For deterministic reproducibility, the bash invocation should be wrapped by a post-action hook that captures stdout/stderr/exit_code into `c4-runs/<run_id>/tool_calls/`.

#### Verifier (Run pytest on artifacts)

Distinguished from Experimenter in the reference architecture by being **graded externally** (by pytest, not by LLM). This is critical: the LLM doesn't get to certify its own work. **OpenHands status: ✅ native** for the execution (bash running `pytest --json-report`), **🟡 wiring needed** for the verdict logic: Gate C in C4 reads the JSON report and decides DONE / DONE_WITH_CONCERNS / BLOCKED. The verdict logic is not in OpenHands; it's in C4's domain-plugin call (see R-200-013 and the `code` domain descriptor E-200-003). The successive JSON reports of a run also constitute a **first-class working-data stream** consumed by the `tests.*` MCP tools described in §6.3.5.

#### Report Writer (Compose REPORT.md)

Produces the human-readable summary of the run. **OpenHands status: ✅ native.** Implementable as a final agent definition invoked at the end of `generate`, with write permission to a fixed output path (`/workspace/REPORT.md`). The orchestrator picks up the file, persists it to MinIO at `c4-runs/<run_id>/report/REPORT.md`, and surfaces it in the conversation UI per D-008.

### 5.3 Cognition components

#### Thinking Channel (Reason before answer)

Extended thinking mode: the model produces hidden reasoning tokens before its visible response. **OpenHands status: ✅ native.** Activate via the LLM call's `reasoning` parameter (mapped by LiteLLM to the Databricks `reasoning` content type, which Databricks Foundation Model APIs natively supports for Claude models). The per-agent default reasoning effort is declared in the agent definition file (R-200-026 v2). Recommended defaults: `architect` and `planner` get high reasoning effort; the editor sub-agent of `implementer` gets low effort (mechanical edits don't benefit from extended thinking).

#### Compute Allocator (Adaptive token budgets)

The orchestrator-level mechanism that decides how much "thinking compute" to allocate per task complexity. **OpenHands status: 🟡 wiring needed.** OpenHands doesn't expose a single `effort` dial; the per-agent reasoning effort is set as the `reasoning` parameter forwarded to the LLM via LiteLLM. On Databricks-hosted Claude this maps to the `reasoning` content type with budget control. The aywiz mapping: per-agent default effort declared in the agent definition file (R-200-026 v2); runtime overrides flow via the `X-Effort-Level` header (R-800-052 v2), translated by C8/LiteLLM into the provider-specific parameter shape.

#### Best-of-N Sampler (Verifier picks winner)

Generate N candidate solutions in parallel, score them with a deterministic verifier, return the winner. The "downward" arrow on the reference schema points to the Verifier, meaning the scoring function is the deterministic test runner — not another LLM. **OpenHands status: ❌ not native.** Implementation pattern: C4 spawns N parallel sub-agent invocations of OpenHands with identical inputs and different seeds (or different effort levels); collects the N candidate workspaces; runs Gate C verifier on each; selects the candidate with most-passing tests (ties broken by code quality score from C6). Cost: N× the token budget — gate by an explicit Best-of-N policy field in the run config (default `n=1`, opt-in to `n>1` for high-stakes runs).

#### Tree of Thoughts (Explore - score - prune)

A search strategy that explores a branching reasoning tree, scores partial solutions, and prunes unpromising branches. **OpenHands status: ❌ not native.** Significant implementation effort: maintaining the tree state, the scoring function, the pruning heuristic, and the back-tracking when a branch dead-ends. Recommended for **v2 SHOULD** (not v1). When implemented, it lives in C4 as a meta-controller that orchestrates multiple OpenHands sessions, possibly using OpenHands session fork to materialise branches.

#### Step Back Reasoner (Principle then specifics)

A prompting technique where the agent is first asked to articulate the high-level principle, then apply it. **OpenHands status: ❌ not native, but trivially achievable in prompts.** No runtime primitive needed: encode the technique in the system prompt of the relevant agent (architect or planner), in their `.md` body. v1-compatible.

### 5.4 Watcher components

#### Trace Tree (Span tree per call)

Per-run hierarchical view of every tool call, sub-agent dispatch, hook firing. **OpenHands status: ✅ native** through OpenHands' event stream (every action and observation is captured with parent/child links) plus an OTel exporter. For aywiz, hooking the trace into the platform-wide W3C trace context (R-100-105 v2, with Q-100-016 still open for K8s Jobs propagation) is the missing wiring. Implementation: an OTel collector sidecar in the C15 pod, exporting to Loki/ES per the observability tier.

#### Budget Guard ($0.0036 / $2.00)

Per-run cost ceiling, hard or soft. **OpenHands status: ✅** — since every LLM call routes through C8/LiteLLM, the existing C8 budget enforcement (R-800-070) covers `generate` natively, with the same semantics it has on all other phases. The orchestrator's per-run budget check before dispatch and the LiteLLM mid-run cap both apply uniformly.

### 5.5 Hardening components

#### Linter Gate (Auto-revert bad code)

Every code edit is immediately linted; if the lint fails, the edit is auto-reverted. **OpenHands status: ✅ native.** Implementation: a `PostToolUse` hook registered against `Edit|Write` tools. The hook runs the language-appropriate linter, and on failure, calls `rewind_files()` to restore the prior state. For Python, ruff is fast enough to run on every edit; for typed code, also pyright (per Olivier's preference for static analysis first). The hook is registered in the agent definition or globally for the `generate` phase via `ClaudeAgentOptions.hooks`.

#### Self Refine Loop (Critique then improve)

The agent critiques its own output and iterates. **OpenHands status: 🟡** — implementable but not a primitive. Pattern: after the `implementer`-editor's output, invoke a `critic` sub-agent that reads the produced artifacts and emits a structured critique; if critique is non-empty, re-invoke the editor with critique as input. Bound the iteration count (three iterations, mirroring the three-fix rule R-200-051). The pattern lives in C4 as the generator's inner loop; OpenHands provides the agent invocations.

### 5.6 Infrastructure components

#### Sandbox REPL (Persistent Docker)

Persistent Docker environment in which the agent works. **OpenHands status: ✅ native.** OpenHands documents the deployment pattern as a containerised runtime. For aywiz, this is C15 (sub-agent runner pod) extended to embed the OpenHands Python SDK plus **tree-sitter language parsers and a `git` binary** for the working-data layers (§6.3). Pod constraints (R-200-031): non-root, read-only root FS, scratch emptyDir, restricted egress. Egress is allowed to C8/LiteLLM (for all LLM calls), C9-extended (MCP server for static reference data tools), and MinIO. **No outbound Anthropic API call** — that's the structural improvement vs the v2 design. Default pod lifetime per R-200-032 (15 min hard timeout).

#### MCP Tool Registry (12 typed tools)

A central catalogue of tools the agent can invoke. **OpenHands status: ✅ native** — OpenHands has a built-in MCP client and supports both HTTP-based and in-process MCP servers. For aywiz, the catalogue is partitioned into three families: OpenHands built-in tools (filesystem operations on the local workspace), static-reference-data tools (served by C9-extended over HTTP, querying C7's RAG and graph indices, see §6.2), and **working-data tools (served by an in-pod MCP server, querying the run-local indices, see §6.3)**.

#### Task DAG (SQLite — Persistent state, survives restarts)

A persistent, restartable representation of the task decomposition. **OpenHands status: 🟡** — OpenHands has its own task/subtask model exposed via its SDK. For aywiz, the persistence layer is ArangoDB (the `c4_runs` collection extended with a `tasks[]` field or a sibling `c4_tasks` collection). The OpenHands runtime state is mirrored to Arango by C4's adapter layer; if/when OpenHands exposes a pluggable persistence protocol, the adapter migrates to it.

#### Bi-Temporal Memory (valid_from · valid_to)

Memory model that tracks not only when a fact was *recorded* but when it was *true* in the world. Critical for evolving knowledge (a person's role changes, a spec gets superseded). **OpenHands status: ❌ not provided.** **Scope clarification (v2): bi-temporal memory applies only to static reference data**, not to working data — see §6.3 for why a graph approach is the wrong tool for fast-mutating artefacts. Existing candidate in aywiz' memory shortlist: Graphiti, which is designed exactly around the bi-temporal model. Recommendation: adopt Graphiti for the long-term memory layer, accessed by agents via C9-extended MCP tools (§6.2.2). Short-term conversational memory remains in C3 / PostgreSQL or Arango as already planned.

#### Vector Store (bge-m3)

Dense vector embeddings for semantic recall. **OpenHands status: ❌ not provided.** **Scope clarification (v2): the platform-wide vector store covers static reference data only.** For *working data* a different strategy applies (§6.3.3: differential embeddings scoped to the run, optional v2 SHOULD). Lives in C7 (ArangoDB vector collection per the existing 400-SPEC). The embedding model `bge-m3` (or any equivalent) is a choice independent of the SDK; aywiz' current direction is to run sentence-transformers locally for embedding (privacy + cost). Agent access from `generate`: via the `rag.search` MCP tool of C9-extended.

#### Git Checkpointer (Audit trail per step)

Every step is committed to git, producing a granular audit trail. **OpenHands status: ✅ via in-pod git + post-action hook** (R-200-036). The pattern: a `PostAction` hook on file-edit actions runs `git add && git commit` with structured trailers (tool-call id, agent name, sub-agent id, parent action id). The agent itself can query the history via bash (`git log --oneline`, `git diff HEAD~3`, `git blame`). Rollback: `git reset --hard <commit>` to any prior state. At run end, `git bundle create` archives the full history to MinIO. This replaces the SDK-specific `rewind_files()` mechanism cleanly with a standard, language-agnostic, well-understood primitive.

---

## 6. Data integration

The OpenHands runtime runs inside a sandboxed pod (C15) and reaches the rest of the platform through three channels:

1. **A mounted local filesystem** for project files, requirements, and produced artifacts (sourced from MinIO at pod start). This is the workspace where OpenHands' built-in tools (file-read/write/edit, grep, glob, bash) operate.
2. **MCP servers for static reference data** — RAG search, graph traversal, requirement lookups against C7. On-demand, network calls to C9-extended.
3. **In-pod working-data layers** — a stratified set of indices over the workspace itself, kept fresh by hooks tied to OpenHands' actions. Includes structural index (tree-sitter), in-pod git, and test-result history.

All LLM calls — from `generate` and every other phase — route through C8/LiteLLM to the active primary provider (Databricks in the reference deployment). The single-egress invariant (R-100-011) is preserved across the whole pipeline; the v2 cost-reconstruction collector is no longer needed.

### 6.1 Channel 1 — Workspace via `mc` sync at pod start

The decision (recorded in the platform's design notes: "MinIO sync via mc CLI (pull on init, push on completion) — chosen over FUSE mounting for robustness") stands and is reaffirmed here. Rationale: OpenHands uses its native filesystem tools (read, write, edit, grep, glob) intensively; a local filesystem yields predictable performance and zero-configuration tool semantics. FUSE was rejected for stability and observability reasons.

**Lifecycle:**

1. **Init container** in the C15 pod runs:
   - `mc cp --recursive minio/<project>/c4-dispatch/<run_id>/<sub_agent_id>/ /workspace/` (pull the manifest, requirements scope, plan, prior artifacts).
   - `cd /workspace && git init && git add . && git commit -m "init: bundle from MinIO"` (initialise the in-pod git repo per §6.3.4).
   - Build the initial structural index via tree-sitter on every recognised source file (§6.3.2).

2. **Main container** starts the OpenHands runtime with `cwd=/workspace`. The runtime and the agent operate on the local filesystem exclusively for project files. Post-action hooks update the working-data layers on every file-edit action.

3. **PostStop sidecar or hook** runs on successful completion:
   - `git bundle create /tmp/repo.bundle --all` to capture the full history.
   - `mc cp --recursive /workspace/ minio/<project>/c4-runs/<run_id>/sub_agent_<id>/workspace/`.
   - `mc cp /tmp/repo.bundle minio/<project>/c4-runs/<run_id>/sub_agent_<id>/repo.bundle`.

   On failure (pod terminated by `activeDeadlineSeconds` or by the orchestrator), an emergency sync attempts to save any partial state (best-effort, no guarantee).

**What lives in `/workspace`:**

- `.git/` — in-pod repo (§6.3.4).
- `.openhands/` — OpenHands session state, picked up by C4's adapter.
- `.aywiz/` — aywiz-specific in-pod state: structural index DB, test-result archive, cost-collector buffer.
- `inputs/manifest.json` — task envelope (per R-200-033).
- `inputs/requirements/` — the requirements excerpt the agent may read.
- `inputs/plan.json` — the planner's output (Definition of Done lives here).
- `inputs/artifacts/` — prior artifacts the agent may read.
- `outputs/` — where the agent writes (code, tests, REPORT.md).

**No direct MinIO MCP tool.** The agent doesn't need it — the filesystem mediates everything. This is intentional: avoiding a MinIO MCP keeps the agent's tool surface clean and lets the orchestrator control exactly what's visible.

### 6.2 Channel 2 — MCP servers for static reference data (on-demand)

For static, slowly-evolving knowledge (ingested sources, requirements corpus, validation rules, prior approved artifacts), the strategy is **bundle the static minimum in the workspace, expose the rest as MCP tools the agent can call when it decides it needs them.**

#### 6.2.1 C9-extended responsibilities

C9 (MCP Server) currently exposes platform capabilities to *external* agents (per R-100-015). It is **extended** in scope to also expose capabilities to *internal* `generate` agents. The same code base, with two surfaces:

- External surface: as today (requirements CRUD, validation orchestration).
- Internal-agents surface: a curated subset of read-only tools tuned for in-task retrieval against **static reference data**.

The two surfaces share authorisation logic (JWT + scope checking) but differ in tool catalogue and rate limits. Working-data tools (§6.3) are served by a **different** MCP server, in-pod, not by C9-extended.

#### 6.2.2 Tool catalogue for internal `generate` agents — static reference data

| Tool | Backed by | Purpose | Example call |
|---|---|---|---|
| `rag.search` | C7 vector index | Semantic search over project's ingested sources + requirements corpus | `rag.search(query="OAuth refresh token rotation", scope="project", top_k=5)` |
| `rag.fetch_full` | C7 + MinIO | Retrieve the full text of a chunk's parent document | `rag.fetch_full(chunk_id="...")` |
| `graph.traverse` | C7 graph index (ArangoDB) | One-hop or N-hop traversal from a node | `graph.traverse(start="R-300-110", edge_type="impacts", depth=2)` |
| `graph.neighbors` | C7 graph index | Direct neighbours of a node, optionally filtered by edge type | `graph.neighbors(node="C5", filter_edge="depends_on")` |
| `requirements.get` | C5 | Fetch a requirement entity at current version or a specific version | `requirements.get(id="R-200-010", version=None)` |
| `requirements.list_relations` | C5 | List relations of a requirement (`derives-from`, `impacts`, `tailoring-of`) | `requirements.list_relations(id="R-200-010")` |
| `validation.dry_run` | C6 | Run a quality check against an in-progress artifact, return findings | `validation.dry_run(check_id="static_types", artifact="src/auth.py")` |
| `memory.recall` | C7 + Graphiti (if adopted) | Bi-temporal recall over static data: "what did we know about X at time T?" | `memory.recall(subject="OAuth flow", as_of="2026-04-01")` |

**v1 minimum:** `rag.search`, `graph.traverse`, `graph.neighbors`, `requirements.get`, `requirements.list_relations`, `validation.dry_run` (6 tools). `rag.fetch_full` and `memory.recall` may be v2 SHOULD pending Graphiti adoption.

#### 6.2.3 Wiring on the OpenHands side

In the agent's OpenHands configuration:

```python
from openhands.config import AgentConfig, LLMConfig, MCPServerConfig

llm_config = LLMConfig(
    # All LLM calls go through C8/LiteLLM, which routes to the active primary provider.
    # The model identifier is the C8-internal name (e.g. "databricks-claude-opus-4-7");
    # C8 maps it to the provider-specific endpoint and credentials.
    base_url="http://c8.aywiz-platform.svc/v1",
    api_key=os.environ["C8_TENANT_TOKEN"],
    model=os.environ["AGENT_MODEL"],  # set per-agent by the orchestrator
    # Extended thinking, prompt caching, and structured tool_calls are forwarded
    # by LiteLLM (BerriAI/litellm#15801) to Databricks Foundation Model APIs.
    extra_headers={
        "X-Agent-Name": agent_name,
        "X-Phase": "generate",
        "X-Run-Id": run_id,
        "X-Sub-Agent-Id": sub_agent_id,
        "X-Effort-Level": agent_effort,  # mapped by C8 to provider-specific reasoning param
    },
)

mcp_servers = [
    MCPServerConfig(  # static reference data via C9-extended
        name="aywiz_static",
        transport="http",
        url="http://c9.aywiz-platform.svc/internal/mcp",
        headers={
            "Authorization": f"Bearer {pod_jwt}",
            "X-Project-Id": project_id,
            "X-Run-Id": run_id,
            "X-Sub-Agent-Id": sub_agent_id,
        },
    ),
    MCPServerConfig(  # working data via in-pod MCP server (see §6.3)
        name="aywiz_working",
        transport="stdio",  # in-process or unix socket
        command=["python", "-m", "aywiz.working_data.mcp_server"],
    ),
]

agent_config = AgentConfig(
    llm=llm_config,
    mcp_servers=mcp_servers,
    allowed_tools=[
        # OpenHands built-in tools:
        "FileRead", "FileWrite", "FileEdit", "Bash", "Grep", "Glob",
        # Static reference tools:
        "aywiz_static.rag.search",
        "aywiz_static.graph.traverse",
        "aywiz_static.graph.neighbors",
        "aywiz_static.requirements.get",
        "aywiz_static.requirements.list_relations",
        "aywiz_static.validation.dry_run",
        # Working-data tools (see §6.3.6 for the full list):
        "aywiz_working.code.symbol_search",
        "aywiz_working.code.references",
        "aywiz_working.code.definition",
        "aywiz_working.tests.last_status",
        "aywiz_working.tests.failures_since",
    ],
)
```

The concrete OpenHands API surface may differ from the snippet above; the snippet is illustrative of the wiring pattern (LLM via C8/LiteLLM, two MCP servers, namespaced tool allowlist).

The C9-extended endpoint runs inside the cluster; the pod reaches it via the K8s ClusterIP service. The pod's JWT is mounted from a short-lived service account token, scoped to the project. C9 validates the JWT and the `X-Project-Id` claim against the project scope on every call. The working-data MCP server runs in-pod and inherits the pod's filesystem and process access, no auth needed.

#### 6.2.4 Pre-fetch vs on-demand: the policy (static data)

| Information type | Strategy | Rationale |
|---|---|---|
| Plan + requirements scope + prior artifacts for this sub-agent | **Pre-fetch into `/workspace/inputs/`** | Always needed; deterministic; reduces per-tool-call latency. |
| Project-wide requirements corpus | **On-demand** via `requirements.get`, `requirements.list_relations` | Agent decides what's relevant; pre-fetching all would blow the context budget. |
| Ingested sources (PDFs, docs uploaded by the user) | **On-demand** via `rag.search` | Sources can be GBs; only relevant chunks should enter context. |
| Knowledge graph relations | **On-demand** via `graph.traverse`, `graph.neighbors` | Graph is exploratory; agent traverses as questions arise. |
| Validation findings on the current artifact | **On-demand** via `validation.dry_run` | Cheap to call, expensive to over-include; the agent triggers it when it suspects an issue. |
| Conversational session memory (across turns of the run) | **OpenHands session state** | Native OpenHands mechanism, mirrored to ArangoDB by C4's adapter. |

#### 6.2.5 Caching, streaming, budget

- **Caching**: C9-extended caches `rag.search` results by `(query_hash, project_id, scope, top_k)` with a 5-minute TTL per run. Same for `graph.traverse` keyed on `(start_id, edge_type, depth, project_id)`. Cache invalidated on requirements mutation events (NATS subscription).
- **Streaming**: `rag.search` returns at most `top_k=5` chunks by default with each chunk capped at 500 tokens to bound context growth per call. The agent may explicitly request more with `top_k` up to 20 (justified in the call).
- **Budget per run**: each MCP call is logged in `c4_tool_calls` and contributes to the run-level call count. A soft cap (e.g. 50 MCP calls per `generate` run) emits a warning; a hard cap (e.g. 200) blocks further calls and triggers `BLOCKED` status.

### 6.3 Channel 3 — Working-data layers (in-pod, run-scoped)

This section addresses a class of data that the v1 of this document did not cover: the artefacts the agent **produces and mutates during the run itself** — source code being written, documentation being drafted, test reports being generated, configs being edited. This data is fundamentally different from static reference data, and applying the same tooling (knowledge graph extraction, persistent embedding) to it is anti-pattern:

- The data mutates at every tool call; a knowledge graph would thrash.
- The data is run-scoped; it has no value outside the run unless promoted to static form.
- Symbols are unstable (a function name may change five times in one session before settling).
- The cost of re-indexing on every edit is prohibitive if done with embedding-class operations.

The strategy is a **stratified set of cheap, structural, run-scoped indices**, applied incrementally on every relevant tool call via `PostToolUse` hooks. Each layer has a clear cost/benefit boundary; the implementation may stop at the layer that proves sufficient and defer the next.

#### 6.3.1 Layer 0 — Filesystem (OpenHands native)

**Capability:** file-read, grep, glob, bash on the local workspace.

**Coverage:** ~60-70% of real agent questions about working data. "Read this file", "find this regex", "list .py under src/".

**Cost:** zero. Already native to OpenHands. Performance excellent on a mounted emptyDir.

**Limit:** textual search only, no semantic intent ("find the function that handles authentication"). Mitigated by Layer 1.

**Status:** ✅ acquired by OpenHands adoption.

#### 6.3.2 Layer 1 — Structural index (tree-sitter)

**Capability:** symbol search, cross-reference, structural navigation. "Who calls `validate_token`?", "what imports `auth.py`?", "list classes inheriting from `BaseAgent`".

**Coverage:** approximately 25% additional questions on top of Layer 0 — the structural questions that grep cannot answer reliably.

**Technology choice:** **tree-sitter** in v1.

| Option considered | Pro | Con | Verdict |
|---|---|---|---|
| **tree-sitter** | Multi-language, fast parsers (<50ms/file), declarative queries, mature ecosystem | No cross-file resolution by itself (mitigated by symbol-table layer above) | **Chosen v1** |
| Universal Ctags | Very simple, multi-language | Flat index, less rich | Fallback if tree-sitter integration delays |
| LSP (pyright, ts-server, rust-analyzer…) | Richest possible info: types, definitions, references, hover | One server per language, lifecycle complexity, harder to sandbox | **v2** when one specific language has typed needs (Python via pyright already in stack) |

**Implementation:**

- Tree-sitter grammars are bundled in the C15 pod image for the v1 supported source languages (Python, TypeScript, YAML, Markdown for the `code` domain).
- An init-container step parses every recognised source file in `/workspace/` and populates an in-pod symbol DB. Storage: a single SQLite file at `/workspace/.aywiz/symbols.db` (run-scoped, fits in <50 MB for typical projects).
- A `PostToolUse` hook on `Edit | Write` re-parses only the modified file and updates the corresponding rows.
- Symbol schema: `(file, kind, name, start_line, end_line, parent_symbol, signature?)` where `kind` is `function | class | method | variable | import | section_header`.

**Exposed to the agent via MCP tools (in-pod MCP server `aywiz_working`):**

- `code.symbol_search(query: str, kind?: str, file_pattern?: str)` — substring or prefix match on symbol names, optional filter by kind or file glob.
- `code.references(symbol: str, file?: str)` — list call sites or import sites of a symbol (grep-based fallback when tree-sitter cannot resolve; LSP-precise in v2).
- `code.definition(symbol: str)` — return file + line range of the symbol definition.
- `code.structure(file: str)` — return the symbol tree of a file (useful for the agent to orient itself in a large file).

**Cost in v1:**

- Pod image grows by ~30 MB (tree-sitter parsers for supported languages).
- Init-time parsing: ~5s for a 10k-LOC repo.
- Per-edit overhead: 20-100ms depending on file size.

**Effort:** ~2 ETA-weeks for the integration + tool implementations.

**Status:** **MUST v1.**

#### 6.3.3 Layer 2 — Differential embeddings (run-scoped, v2 SHOULD)

**Capability:** truly semantic queries on working data — "find the function that *does* authentication, regardless of its name". The agent expresses intent; the index matches on meaning, not on lexical similarity.

**Pattern:** chunk the workspace by structural unit (function/class boundaries, derived from Layer 1). On every `Edit | Write`, re-embed only the affected chunks (hash-based dedup). Store in a run-scoped ArangoDB vector collection `c4_workspace_embeddings_<run_id>`. Purge at run end.

**Cost analysis:**

- Embedding model: sentence-transformers (bge-m3 or similar) running locally on CPU.
- Per chunk (~500 tokens): ~10ms embedding + ~5ms ArangoDB upsert.
- For a 50-LOC edit hitting ~3 chunks: ~50ms added latency, $0 marginal cost.

**Honest critical assessment:** Layer 1 (structural index) already covers most needs *for a project the agent is actively co-authoring* — the agent knows the symbol names it just created. Layer 2 adds real value only when the agent works in a large legacy codebase where it doesn't know the nomenclature, which is **not** aywiz' primary v1 use case (greenfield code generation from requirements).

**Recommendation:** **SHOULD v2.** Defer until v1 telemetry shows agents failing on semantic searches frequently. v1 skips this layer.

**Status:** v2 SHOULD.

#### 6.3.4 Layer 3 — In-pod git (audit, history, agent introspection)

**Capability:** complete change history, blame, temporal rollback, agent self-introspection on what changed and when.

**Pattern:**

- The workspace is initialised as a git repo in the init container: `git init && git add . && git commit -m "init"`.
- A `PostToolUse` hook on `Edit | Write | MultiEdit` runs `git add <changed_files> && git commit -m "agent edit: <tool_name> on <files>"` with structured trailers:
  ```
  tool-call-id: <uuid>
  agent-name: <implementer.editor>
  sub-agent-id: <id>
  parent-tool-call-id: <id-or-empty>
  ```
- On a successful Bash tool invocation that ran tests, a commit is also taken with trailer `verifier-status: passed|failed|error`, even if no files changed (the commit captures the moment of verification).
- At run end, `git bundle create` archives the full history to MinIO.

**Benefits (multiple, compounding):**

1. **Audit trail by construction.** Every change has an authored timestamp, a tool-call ID, and a structured trailer. Replaces the proposed `c4_tool_calls` collection from the gap audit — git is the source of truth, the collection becomes an index/projection.

2. **Granular rollback to any commit.** Git allows rollback to any commit, including across OpenHands sub-agent boundaries. Useful when an entire architectural direction proves wrong and the run should restart from an earlier consistent state. The linter-gate auto-revert (§5.5) is the fast path; manual rollback to deeper commits is the deep path.

3. **Agent introspection capability.** The agent itself can query the history via `Bash` invocations: `git log --oneline`, `git diff HEAD~3`, `git blame src/auth.py`, `git show <commit>`. This is a mode of self-reflection that LLMs are well-trained on (every public codebase is git-versioned). Cheap to enable, surprisingly valuable in iterative refinement.

4. **Post-run preservation.** The bundle can be pushed to a user-controlled git remote at release time (via n8n post-release workflow R-100-080), providing customers with a real git history of the agent's work — auditable, blameable, replayable.

**Cost:**

- Per-edit overhead: ~20ms for `git add && git commit` on a modern filesystem.
- Storage: git compresses aggressively; a 1000-edit run typically produces a `.git/` directory under 50 MB.
- Pod image: +5 MB for the git binary (likely already present in any base image).

**Why git for everything (no separate in-session undo mechanism):** in the SDK-Anthropic design considered in v2, an in-session `rewind_files()` mechanism was complementary to durable git. With OpenHands, we use git for both. The post-action hook commits after every settled edit; the linter-gate hook reverts via `git reset --hard HEAD` when a lint fails. Single source of truth, no two-system reconciliation.

**Effort:** ~1 ETA-week (init script + PostToolUse hook + PostStop bundle export).

**Status:** **MUST v1.**

#### 6.3.5 Test results as a first-class working-data stream

The verifier (Gate B and Gate C) produces a structured report on every invocation. These reports are **factual ground truth** for the agent — more reliable than any symbolic analysis. v2 of this synthesis recognises them as a working-data stream on par with the source code itself.

**Pattern:**

- Every verifier invocation (typically via `Bash` running `pytest --json-report --json-report-file=/workspace/.aywiz/verifier/<seq>.json`) appends to a numbered sequence under `/workspace/.aywiz/verifier/`.
- The most recent report is symlinked at `/workspace/.aywiz/verifier/latest.json`.
- A `PostToolUse` hook on Bash invocations matching verifier patterns updates an SQLite summary at `/workspace/.aywiz/verifier/index.db`: `(seq, timestamp, commit_hash, passed, failed, errors, skipped, duration_ms)`.

**Exposed to the agent via MCP tools (`aywiz_working` server):**

- `tests.last_status()` — returns the latest verifier report summary (counts + first 5 failures).
- `tests.last_full_report()` — returns the full latest JSON report.
- `tests.failures_since(commit_hash)` — diffs the failure set between two verifier runs, identified by the git commits they followed.
- `tests.history(limit=10)` — returns the chronological list of verifier runs with summary counts.

**Why this matters:** the agent gets a **factual feedback loop** that is more informative than a semantic index of its own code. Often the right next action is dictated by which test failed, not by what the code "means" semantically. Layer 2 (embeddings) becomes much less valuable when this stream is in place.

**Cost:** trivial. The reports already exist (they are written by pytest itself). The SQLite summary is a side-effect of one parsing step in a hook.

**Effort:** ~3 ETA-days.

**Status:** **MUST v1.**

#### 6.3.6 Tool catalogue summary — working data

All tools below are served by the in-pod MCP server `aywiz_working` (loaded by OpenHands via stdio transport). They operate on the run-local indices populated by the hooks described above; no network egress is involved.

| Tool | Backed by | Status v1 |
|---|---|---|
| `code.symbol_search(query, kind?, file_pattern?)` | Layer 1 (tree-sitter SQLite) | MUST |
| `code.references(symbol, file?)` | Layer 1 + grep fallback | MUST |
| `code.definition(symbol)` | Layer 1 | MUST |
| `code.structure(file)` | Layer 1 | MUST |
| `code.semantic_search(query, top_k?)` | Layer 2 (differential embeddings) | SHOULD v2 |
| `tests.last_status()` | §6.3.5 (verifier stream) | MUST |
| `tests.last_full_report()` | §6.3.5 | MUST |
| `tests.failures_since(commit_hash)` | §6.3.5 + git | MUST |
| `tests.history(limit?)` | §6.3.5 | MUST |
| `history.log(limit?, since_commit?)` | Layer 3 (git) | MUST |
| `history.diff(from, to)` | Layer 3 (git) | MUST |
| `history.blame(file, line?)` | Layer 3 (git) | MUST |

v1 ships 11 tools across the working-data layers. The agent additionally has unrestricted access via OpenHands' bash tool for any custom git, grep, or filesystem inspection — the MCP tools above are the *typed, audited* entry points.

#### 6.3.7 Promotion of working data → static reference data

Working data is **promoted** to static reference data at specific lifecycle events. After promotion, the artefact enters C7's index (vector + graph, optionally bi-temporal via Graphiti) and becomes accessible via the static MCP tools (§6.2).

**Promotion triggers (v1):**

| Event | Promoted | Mechanism |
|---|---|---|
| Run reaches `COMPLETED` and is released | Final code + tests + REPORT.md | n8n post-release workflow ingests into C7 |
| Documentation generated during the run is approved by a reviewer | The approved doc file(s) | C6 reviewer triggers an ingestion job |
| Validation Gate C passes on a milestone (e.g. inter-phase boundary in a long run) | Optional: the workspace snapshot at the boundary | C4 emits a `c4-runs.<run_id>.milestone.passed` event consumed by n8n |

**Schema of the promotion job:** identical to the existing external-source ingestion pipeline (R-100-080..087), with two additional metadata fields on the resulting C7 entities:
- `provenance.run_id` — links back to the originating `c4_runs` record.
- `provenance.commit_hash` — links to the exact git commit captured at promotion time.

This gives the static-side knowledge graph a backward link to the run that produced each piece of code or doc, enabling forensic queries ("show me the run that introduced this requirement violation") without breaking the static/working separation.

**What is NOT promoted:** intermediate edits, failed attempts, abandoned branches, working sketches. These remain in the run's preserved bundle (MinIO archive of the in-pod git repo) for audit/debugging, but they do not pollute the static knowledge graph.

#### 6.3.8 Effort recap and v1 commitment

| Layer | v1 status | Effort |
|---|---|---|
| L0 — Filesystem | acquired by OpenHands | 0 |
| L1 — Structural index (tree-sitter) | **MUST v1** | ~2 ETA-weeks |
| L2 — Differential embeddings | SHOULD v2 | (~3 ETA-weeks, deferred) |
| L3 — In-pod git | **MUST v1** | ~1 ETA-week |
| §6.3.5 — Test-result stream | **MUST v1** | ~3 ETA-days |
| §6.3.7 — Promotion to static | **MUST v1** (n8n workflow + C7 schema delta) | ~1 ETA-week |
| **Total v1 working-data investment** | — | **~5 ETA-weeks** |

This is the price of a working-data strategy that scales. It is small relative to the avoided cost of building a live knowledge graph over volatile artefacts (which would not work) or relying on filesystem grep alone (which would degrade agent quality).

### 6.4 Network policy update

R-200-031 currently restricts egress to "C8 + MinIO only". This becomes:

- **C8 LLM gateway** — allowed (this is the route for all LLM calls from OpenHands, including `generate`).
- **C9-extended MCP endpoint** — newly allowed, internal-cluster service only (for static reference data tools).
- **MinIO (`mc` sync)** — still allowed, init and PostStop.
- **No outbound to LLM providers (Anthropic / Databricks / OpenAI) directly** — all LLM traffic flows through C8. This preserves R-100-011 (single egress).
- **No outbound for working-data tools** — they are served by an in-pod MCP server, no network involvement.

The network policy should be a Kubernetes NetworkPolicy resource owned by the C15 pod template, declarative and reviewable. Compared to v2, the policy is now stricter and simpler: zero outbound LLM-provider connections from the pod.

### 6.5 Cost tracking (unified through C8)

Because all LLM calls — including those from OpenHands during `generate` — route through C8/LiteLLM, the existing C8 cost tracking infrastructure (R-800-120 metrics, R-800-121 structured logs, `llm_calls` collection) covers the `generate` phase natively. No parallel collection, no reconstruction collector, no dashboard glue. The v2 `c4_llm_calls_generate` collection is **dropped from the design**.

For cache-hit reporting specifically: Databricks Foundation Model APIs return `usage.prompt_cache_read_tokens` and `usage.prompt_cache_creation_tokens` in the OpenAI-compatible response. LiteLLM normalises these into the standard usage fields. C8 records them per call. Cache-hit ratios per agent / per tenant are queryable directly from the unified `llm_calls` collection.

This is the single most-tangible architectural simplification of v3 over v2.

---

## 7. Risks accepted

By adopting this architecture, the team explicitly accepts the following risks. They are recorded so they can be revisited:

1. **OpenHands harness lock-in (lighter than the v2 SDK lock-in but real).** Migrating away from OpenHands requires rewriting the `pipeline/generate_engine.py` adapter, ~500-1000 LOC. Mitigation: the engine is encapsulated behind a single module; the rest of C4 sees it through a stable internal interface; alternatives (Goose / custom LangGraph) remain comparable in shape.
2. **OpenHands maturity caveat.** Open-source, MIT, Series A funded, but still evolving. Mitigation: pin precise versions; run a regression suite on every bump; review changelog monthly; budget ~0.5 ETA-day/month for upkeep. Compared to the v2 SDK Anthropic option, OpenHands has the advantage of a community of contributors and is not tied to a single vendor's roadmap.
3. **Harness performance vs Claude Agent SDK on Claude models.** A published benchmark (Terminal-Bench 2.0) shows that the *same model* can swing 30 to 50 percentage points depending on the harness wrapping it. The Claude Agent SDK is tuned for Claude; OpenHands is generic. The actual delta on aywiz' use cases must be measured in the POC (Q13). Mitigation: the POC measures task-completion rate on a representative sample of `generate` workloads against both routes (OpenHands→LiteLLM→Databricks-Claude vs hypothetical SDK→Anthropic-direct); the choice can be revisited if the gap is large and the contractual context allows it.
4. **Databricks feature-lag for new Anthropic releases.** Beta features Anthropic ships first on the direct API typically take 2-6 weeks to land on Databricks Foundation Model APIs. Mitigation: feature-flag any code path depending on a beta feature; rely on stable features (prompt caching, extended thinking, tool calling, 1M context) which Databricks already supports.
5. **Two layers of agentic discipline.** The C4 orchestrator's hard gates A/B/C wrap OpenHands' own agent loop with its own hooks and sub-agents. The contract between them must be carefully drawn (see §8 Q2). Risk of latent inconsistencies. Mitigation: clear handoff points — OpenHands invocation is one tightly-scoped sub-task of `generate`, orchestrator owns the rest.
6. **MCP-mediated static-data access adds latency.** Every `rag.search` or `graph.traverse` is a network round-trip. Mitigation: caching (§6.2.5) and a 50-call soft cap per run keep this within ~1-2s of cumulative latency overhead in typical runs. Working-data tools are in-pod, no network latency.
7. **Pod startup cost.** Each `generate` sub-agent pulls MinIO state, starts Python + OpenHands, builds the structural index, initialises git — typically 10-20 seconds (slightly faster than v2 since no Node.js bootstrap). Mitigation: image pre-warming on the cluster; possibly a pod-warming pool for tenants with strict latency SLOs (deferred to v2).
8. **Working-data index staleness on hook failure.** If a post-action hook crashes silently, the structural index or git history may drift from the workspace. Mitigation: hooks must fail loudly (raise to the OpenHands runtime, which surfaces to the orchestrator); a final `git status --porcelain` check at run end SHALL be clean (no uncommitted changes); a tree-sitter rebuild can always be triggered on demand if drift is suspected.
9. **LiteLLM as critical dependency for `generate`.** Previously LiteLLM was on the cold path of the non-`generate` phases; with v3, every `generate` LLM call also depends on it. A LiteLLM outage now blocks the most token-intensive phase. Mitigation: LiteLLM is run in HA in C8; existing R-800-080..084 (retry, fallback, circuit breaker) cover the operational case; the dependency is the same shape as the rest of the pipeline.

---

## 8. Open questions (with separate recommendations)

Each open question is stated first as a neutral question; the recommendation follows after a clear separator.

### Q1 — Active primary provider for the reference deployment

**Question.** The architecture supports any OpenAI-compatible LLM provider routed through C8/LiteLLM. Which provider is the *reference* active primary for v1 of the platform?

**RECOMMENDATION.** **Databricks Foundation Model APIs** with Claude Opus 4.7 / Sonnet 4.6 / Haiku 4.5 as the primary tier set. Rationale: (a) Databricks meets the sovereignty / governance / unified billing constraints typical of regulated enterprise tenants; (b) the Claude family on Databricks supports prompt caching, extended thinking, tool calling, and 1M-token context natively; (c) LiteLLM's `DatabricksConfig` has been patched to preserve advanced features. Alternatives that work in the same architectural slot, with no code changes beyond C8 configuration: Anthropic direct (for tenants without sovereignty constraints), AWS Bedrock, Vertex AI, Azure AI Foundry, or a self-hosted vLLM endpoint for full air-gap deployments.

### Q2 — Orchestrator ↔ OpenHands coupling: (a) one shot or (b) multi-step?

**Question.** Does the C4 orchestrator invoke OpenHands once for the entire `generate` phase (OpenHands runs to its own completion, then orchestrator picks up for `review`), or does the orchestrator invoke OpenHands in multiple short sub-steps, enforcing Gates between invocations?

**RECOMMENDATION.** Option **(a) one shot per logical artifact, with Gate B enforced as a precondition** (test must exist + fail before `generate` enters). After the single OpenHands invocation:
- If the editor wrote both production code and tests, Gate B is verified retroactively by C6 (the test must have been authored before the production code — checkable via in-pod git commit order from §6.3.4).
- Gate C (validation green after edits) is run by C6 after OpenHands exits.

This preserves OpenHands' strength (long-running context, sub-agents, MCP-mediated retrieval) while keeping C4's discipline. The alternative (multi-step) fragments the OpenHands session and defeats its context management.

### Q3 — Cognition patterns (ToT, Best-of-N, Step-Back, Self-Refine): MUST v1 or v2?

**Question.** Which of the four cognition patterns (Tree of Thoughts, Best-of-N, Step-Back Reasoner, Self-Refine Loop) should be in v1 vs v2?

**RECOMMENDATION.**
- **Step-Back Reasoner**: MUST v1. It's purely a prompt-template change in architect/planner; cost is one paragraph in their `.md` definitions.
- **Self-Refine Loop**: MUST v1. The critic sub-agent + re-invocation pattern is what the three-fix rule (R-200-051) already implies for code; making it explicit costs little.
- **Best-of-N**: SHOULD v1, opt-in per run. The infrastructure cost is low (parallel pod dispatch already exists); the token cost is high (N×). Make it a per-run flag, default `n=1`. v1 ships the capability; tenants opt in for high-stakes runs.
- **Tree of Thoughts**: SHOULD v2. The tree state, pruning, scoring, and backtracking implementation is substantial. v1 backlog as a candidate for the first divergent-problem domain.

### Q4 — Bi-temporal memory: Graphiti vs custom on ArangoDB?

**Question.** For the bi-temporal memory layer (the "Bi Temporal Memory" component of the reference architecture, scoped to **static reference data only** per §6.3 of this document), adopt Graphiti or build a custom layer on top of ArangoDB?

**RECOMMENDATION.** Adopt **Graphiti**, conditional on it supporting ArangoDB as a backend (or being adaptable; Graphiti is currently Neo4j-first but the bi-temporal semantics are decoupled from the store). If Graphiti requires Neo4j, the cost of running a second graph DB is non-trivial — fall back to a thin custom bi-temporal layer over the existing ArangoDB collections (add `valid_from`, `valid_to` timestamps to edges, query patterns documented). Investigation needed before commitment.

### Q5 — Per-agent default `effort` level?

**Question.** What should the default `effort` level be for each agent role?

**RECOMMENDATION.**
- `architect`: `high` (complex synthesis, worth the thinking budget).
- `planner`: `high` (high-cost-per-mistake, worth the budget).
- `implementer` (architect-style sub-agent): `medium`.
- `implementer` (editor-style sub-agent): `low` (mechanical edits don't benefit from extended thinking).
- `spec-reviewer`, `quality-reviewer`: `medium` (structural reasoning, but mostly checklist).
- Generic `sub-agent`: `low`.

Override per agent via the `X-Effort-Level` header (R-800-052 v2) derived from the agent definition file (R-200-026 v2). C8/LiteLLM translates the header into the provider-specific reasoning parameter (Databricks `reasoning` content type budget). Operators can globally adjust via env var; per-tenant adjustment is a v2 SHOULD.

### Q6 — OpenHands session persistence

**Question.** OpenHands has its own internal state representation for sessions (events, observations, agent state). How is this persisted in aywiz, given the ArangoDB-centric stack?

**RECOMMENDATION.** Persist to ArangoDB via a C4 adapter that subscribes to OpenHands' event stream and writes events to a `c4_openhands_events` collection (one document per event, indexed by `run_id` + `sequence_number`). The current OpenHands runtime emits events that can be intercepted via an `EventCallback`; aywiz' adapter consumes the stream and persists. On resume, the adapter replays events to reconstitute state. Estimated effort: 1-2 ETA-days. If OpenHands' API surface changes substantially across versions, the adapter is one of the modules to keep an eye on at each version bump (per Risk 2).

### Q7 — Cost record granularity (unified)

**Question.** Since v3 routes all LLM calls through C8/LiteLLM (including `generate`), the existing `llm_calls` collection captures everything. Is the existing granularity sufficient or does `generate`'s longer-running invocations need extra fields?

**RECOMMENDATION.** Keep the existing `llm_calls` schema (R-800-121); no extra collection. Ensure the `X-Run-Id`, `X-Sub-Agent-Id`, and `X-Agent-Name` headers (already specified) are passed through OpenHands to C8 so calls can be aggregated per-run. The `usage.prompt_cache_read_tokens` / `usage.prompt_cache_creation_tokens` fields returned by Databricks via LiteLLM are normalised by C8 into the standard `cache_read_tokens` / `cache_creation_tokens` columns. The cache-hit ratio per agent / tenant is then a one-query aggregation. No new collection, no parallel record stream.

### Q8 — Static-data tool surface vs working-data tool surface: how does the agent choose?

**Question.** OpenHands' built-in tools operate on `/workspace/` (working data via Layer 0). The `aywiz_working` MCP tools operate on the run-local indices over the same workspace (Layers 1, 3, §6.3.5). The `aywiz_static` MCP tools operate on the static reference data. How does the agent decide which surface to use?

**RECOMMENDATION.** Through agent prompt design and tool naming. The system prompt of `implementer` makes the boundary explicit, e.g.:
- "Use `Read`, `Write`, `Edit` for direct file operations on `/workspace/`."
- "Use `Grep`, `Glob` for textual searches in `/workspace/`."
- "Use `code.symbol_search` / `code.references` / `code.definition` for structural questions about the workspace (faster and more precise than grep for code navigation)."
- "Use `tests.last_status` / `tests.failures_since` to check verifier outcomes — never re-run pytest just to find the latest result."
- "Use `history.log` / `history.diff` / `history.blame` to query what changed during this run and why."
- "Use `rag.search` when you need information from project sources or requirements *not* in your workspace."
- "Use `graph.traverse` to explore relationships between requirements or prior artifacts in the project corpus."

Tool names are namespaced (`mcp__aywiz_static__rag.search` vs `mcp__aywiz_working__code.symbol_search` vs bare `Read`), making the LLM's choice mechanical and the audit log unambiguous.

### Q9 — Image build and supply chain for the C15 pod?

**Question.** Where does the C15 pod image come from, given it must bundle Python + OpenHands + tree-sitter parsers + git + the platform's Python code?

**RECOMMENDATION.** Two-stage build:
- Base layer: `python:3.12-slim` + `pip install openhands-ai` (or equivalent OpenHands SDK package name) + tree-sitter wheels + git from the base distro + `mc` CLI. No Node.js needed (one less language runtime than v2).
- Platform layer: aywiz' `c15-runner` image on top, adding the orchestration glue, the in-pod MCP server module for working data, MCP client config for `aywiz_static`, OTel collector sidecar config.
- The image is rebuilt on every OpenHands version bump (CI); the platform layer is rebuilt on every aywiz release.
- Image is signed (cosign) and scanned (trivy) per platform policy.

### Q10 — How are aywiz instructions loaded into OpenHands?

**Question.** OpenHands has its own configuration for system prompts and instruction loading. aywiz has its own instruction system (the platform differentiator: instructions as collective intelligence asset across project/template/organization/platform levels). How do they connect?

**RECOMMENDATION.** The aywiz instruction system stays the authoritative source of truth. At `generate` phase entry, the orchestrator resolves the full instruction set (per project + tenant + platform inheritance) and materialises it as the agent's system prompt in the OpenHands `AgentConfig`. The platform's contribution model (anonymous upward contribution from project to template etc.) operates at the aywiz level; OpenHands sees only the resolved final prompt. No leak of aywiz' instruction-system semantics into OpenHands runtime.

### Q11 — Tree-sitter at v1 or LSP at v1 for the structural index?

**Question.** §6.3.2 commits to tree-sitter for v1 and LSP for v2. Is this the right phasing for the `code` domain, which is Python-heavy and where pyright is already in the stack for static analysis?

**RECOMMENDATION.** Stay with **tree-sitter in v1** despite the Python-heavy emphasis. Reasoning:
- pyright is already used for *quality validation* (C6); reusing it for *agent navigation* would couple the two responsibilities and force pyright to run continuously in the pod (high resource usage).
- tree-sitter is multi-language by default; v2 of the platform will add documentation and presentation domains that may include TypeScript, YAML, Markdown — tree-sitter handles all of them with one tool.
- LSP integration is significantly more complex to sandbox correctly (the LSP server is a long-running process with its own filesystem expectations); deferring it avoids accumulating that complexity in v1.
- The precision difference (LSP knows types, tree-sitter doesn't) matters less for *navigation* than for *validation*. For "find the function that does X", tree-sitter is precise enough.

v2 may add LSP as an additional `aywiz_working` tool family (`lsp.hover`, `lsp.diagnostics`) without removing tree-sitter; the two are complementary.

### Q12 — When exactly does working data promote to static data?

**Question.** §6.3.7 lists three promotion triggers (release, doc approval, milestone validation). Which trigger applies in which case, and who decides?

**RECOMMENDATION.** Default policy for the `code` domain v1:
- **Release** is the primary trigger. When a run reaches `COMPLETED` and the user/admin approves the release (via the same UI control that triggers the n8n post-release workflow per R-100-080), promotion runs automatically: the final code, tests, and REPORT.md are ingested into C7 with `provenance.run_id` and `provenance.commit_hash` set.
- **Doc approval** applies only when the run produced documentation files explicitly marked as `target: static` in the plan's Definition of Done. Routine in-line code comments are not promoted; only structured documentation deliverables (architecture docs, API reference, runbooks) are. The reviewer's approval action triggers the ingestion.
- **Milestone validation** is a v2 SHOULD: in long multi-phase runs, the platform may want to promote intermediate stable states. Not in v1.

Operators may override the defaults via tenant configuration (e.g. "promote only after manual review, never automatic on release") — same shape as existing R-100-080 tenant overrides.

### Q13 — POC criteria for OpenHands adoption

**Question.** Before fully committing to the v3 design in the specs, what are the success criteria for a POC of OpenHands + LiteLLM + Databricks?

**RECOMMENDATION.** A 2-week POC, in two phases.

**Phase 1 (1 week) — wiring**:
- Stand up a single-pod OpenHands deployment in K8s with the SDK configured to call C8/LiteLLM.
- C8 routes to Databricks Foundation Model APIs (Claude Opus 4.7).
- A trivial `generate` task: read a small spec, write a Python module, write a pytest, run the test.
- Pass criteria: end-to-end success on 3 sample tasks; cache-hit ratio >0% by the 2nd run on the same task; OTel traces visible end-to-end.

**Phase 2 (1 week) — discipline**:
- Wire the post-action hook for in-pod git commits (R-200-036 prototype).
- Wire the working-data MCP server with a minimal subset of tools (`code.symbol_search`, `tests.last_status`).
- Run a representative `generate` workload (a single requirement from one of the platform's existing specs, e.g. R-200-029 itself).
- Pass criteria:
  - Task completion rate ≥ 70% on a 10-task sample (compared to Claude Code SDK direct on the same tasks, if measurable in a parallel run).
  - Latency overhead from C8/LiteLLM proxying < 200ms p50, < 500ms p95.
  - Cost reporting in C8's `llm_calls` collection includes cache fields and per-agent attribution.
  - No silent hook failures (`git status --porcelain` clean at run end on all runs).

**Decision gate:** if Phase 2 pass criteria are met, the v3 design proceeds to specs amendment. If not, the analysis is documented and the team revisits Goose, custom LangGraph, or contractual relaxation of the Databricks-only constraint.

---

## 9. Specs to amend

This section enumerates the architectural impacts. Each item is a delta against the current spec corpus.

### 9.1 New ADR — D-014 (OpenHands adoption for `generate`, routed through C8/LiteLLM)

A new decision entity in `999-SYNTHESIS.md`:

```yaml
id: D-014
version: 1
status: proposed
category: architecture
impacts: [R-100-*, R-200-*, R-600-*, R-800-*, R-100-040, R-100-041, R-200-031]
```

Content: codifies the verdict of §2, the component split of §3, and references this synthesis document. The decision establishes OpenHands as the `generate`-phase agentic harness, embedded as a Python dependency in C4, with all LLM calls routed through C8/LiteLLM to the active primary provider (Databricks Foundation Model APIs in the reference deployment). Acceptance: requires (a) successful completion of the POC defined in Q13 of §8, and (b) explicit approval. Pinned versions of `openhands-ai` and platform-tested LiteLLM are declared in the deployment image manifest and reviewed monthly.

### 9.2 Amended D-007 (staff-engineer pattern)

Promote from "v2 deferred" to "v1 scope":
- Fine-grained model selection per task complexity (now realised through the `X-Effort-Level` header from agent definitions, routed by C8/LiteLLM).
- Per-agent tool surface (now realised through OpenHands' `allowed_tools` + C9-extended MCP + in-pod `aywiz_working` MCP).
- File checkpointing (now realised through in-pod git history with post-action hook, R-200-036).
- In-agent task decomposition (now realised through OpenHands' task model + ArangoDB persistence).
- Hooks framework (now realised through OpenHands' post-action hooks + C4-side orchestration hooks).

Confirm in "v2 deferred": skill academy, forensic debugging, Git worktree management (note: in-pod git in C15 is a separate, simpler pattern from the proper Git worktree-per-task management deferred here).
Add to "v2 SHOULD": Tree of Thoughts, differential embeddings layer for working data (§6.3.3), LSP-based structural index (Q11).
Confirm "excluded": visual companion.
Add to "excluded": memory tool as agent scratchpad (C7 + working-data layers cover the use cases).

### 9.3 Amended D-011 (LiteLLM multi-LLM abstraction)

Restate the levels, with v3 restoring full applicability to the `generate` phase:

- **Level 1 — provider portability (MUST v1)**: one active primary provider, swappable, multi-model from that provider. Applies to **every** LLM invocation across **every** phase, including `generate`. (v2 carved an exception for `generate` because of the Claude Agent SDK lock-in; v3 retires that exception because OpenHands routes through LiteLLM.)
- **Level 2 — intra-provider task-based routing (MUST v1, was SHOULD v2)**: different models from the active primary provider per agent / phase / task complexity. Applies across all phases.
- **Level 2b — cross-provider task-based routing (SHOULD v2)**: applies across all phases.
- **Level 3 — ensemble (COULD roadmap)**: unchanged.

Explicit scope clause: "D-011 governs every LLM invocation in aywiz, including those made by OpenHands during the `generate` phase. The integration is detailed in D-014."

### 9.4 New requirements in `200-SPEC-PIPELINE-AGENT.md`

- **R-200-029** (new): The orchestrator SHALL invoke the OpenHands SDK as the runtime of the `implementer` agent during the `generate` phase. The OpenHands LLM client SHALL be configured to call C8/LiteLLM as the OpenAI-compatible endpoint, never any LLM provider directly. The `openhands-ai` Python package may only be imported in the `pipeline/generate_engine.py` module; all other agent code remains subject to R-200-021.
- **R-200-030 v2**: Sub-agent pods running OpenHands SHALL include the `openhands-ai` Python package, the tree-sitter language parsers for v1 supported source languages (Python, TypeScript, YAML, Markdown), the `git` binary, and the `mc` CLI. Specific version pins are declared in the deployment image manifest and reviewed monthly. **No Node.js runtime is required** (simplification vs v2).
- **R-200-031 v2**: Egress policy allows: C8 (for all LLM calls, including from OpenHands during `generate`), C9-extended MCP endpoint, MinIO (`mc` sync), and the OTel Collector endpoint (for span/log export). All other egress remains blocked. **No direct egress to any LLM provider endpoint** (Anthropic, Databricks, OpenAI, etc.). Working-data MCP traffic is in-pod stdio/loopback and not subject to network policy.
- **R-200-035** (new): The OpenHands-driven `generate` phase SHALL emit one row in C8's existing `llm_calls` collection per LLM turn, with the standard schema fields plus the headers `X-Run-Id`, `X-Sub-Agent-Id`, `X-Agent-Name`, `X-Phase=generate`. **No parallel `c4_llm_calls_generate` collection is created** (the v2 design's reconstruction collector is dropped).
- **R-200-036** (new): The C15 pod SHALL initialise a git repository at `/workspace/.aywiz/repo` at pod start and configure an OpenHands post-action hook that issues `git add -A && git commit -m '<tool_call_id>'` after every file-mutation action. At pod stop, `git bundle create` exports the full history to MinIO.
- **R-200-037** (new): The C15 pod SHALL run an in-process MCP server `aywiz_working` (loaded by OpenHands via stdio transport) exposing the working-data tool catalogue: code symbol search, code references, test status, test history, file history, blame, diff. The full tool surface is enumerated in `400-SPEC-MEMORY-RAG.md`.
- **R-200-038** (new, v4): The agent definitions (system prompts, allowed tools, model selection, effort defaults — one YAML+MD file per agent per R-200-026) SHALL be versioned in the C4 source repository and reviewed through C4's code review process. The C15 pod SHALL NOT contain any aywiz application code; at pod init, the C4-resolved agent definitions are pushed to the pod's `/workspace/.aywiz/agents/` directory via an init container. C15 hosts only the runtime image (Python + openhands-ai + tree-sitter + git + mc) declared in R-200-030 v2.
- **R-200-036** (new): The C15 pod SHALL initialise an in-pod git repository at workspace root during init-container execution, with the initial commit capturing the `mc`-synced bundle state. Every successful `Edit`, `Write`, or `MultiEdit` tool call SHALL be committed atomically via a `PostToolUse` hook with structured commit-message trailers identifying the tool-call ID, agent name, sub-agent ID, and parent tool-call ID. The final git history SHALL be bundled and exported to MinIO at run completion.
- **R-200-037** (new): The C15 pod SHALL run an in-process MCP server `aywiz_working` (loaded by OpenHands via stdio transport) exposing the working-data tool catalogue defined in §6.3.6 of this synthesis. The server reads from the in-pod indices (tree-sitter SQLite at `.aywiz/symbols.db`, verifier history at `.aywiz/verifier/`, in-pod git) and SHALL NOT issue network calls.

### 9.5 New requirements in `400-SPEC-MEMORY-RAG.md`

- **R-400-NNN** (new): C7 SHALL expose, via C9-extended, the following internal-agent MCP tools for **static reference data**: `rag.search`, `graph.traverse`, `graph.neighbors`, `requirements.get`, `requirements.list_relations`, `validation.dry_run`. Each tool's input/output schema is defined in `mcp-server/tools/internal-agents-v1.json`. Working-data tools are not served by C9 — they are in-pod (R-200-037).
- **R-400-NNN+1** (new): The MCP tools above SHALL enforce project scoping via the `X-Project-Id` header validated against the JWT's `project_scopes` claim. Cross-project access SHALL return HTTP 403.
- **R-400-NNN+2** (new): When working data is promoted to static data at run release (per §6.3.7), the resulting C7 entities SHALL carry `provenance.run_id` and `provenance.commit_hash` metadata fields, enabling backward queries from static entities to the originating run.

### 9.6 New requirements in `600-SPEC-CODE-QUALITY.md`

- **R-600-NNN** (new): The `code` domain plug-in SHALL include tree-sitter grammar bindings for Python and TypeScript (v1 supported source languages), used both for working-data structural indexing in the C15 pod (§6.3.2) and for any quality check that requires structural understanding of source files (C6).
- **R-600-NNN+1** (new): Verifier invocations (`pytest --json-report`) SHALL emit a structured JSON report per run, written to `/workspace/.aywiz/verifier/<seq>.json` in the pod, with retention to MinIO at run completion. The `tests.*` MCP tools (§6.3.5) consume these reports.

### 9.8 New requirements in `900-SPEC-OBSERVABILITY.md` (new spec document)

- **R-900-001** (new, v4): Every aywiz Cn SHALL emit OpenTelemetry spans and structured JSON logs that include the attribute set defined in §12.3 of this synthesis (`tenant_id`, `project_id`, `run_id`, `phase`, `gate_id`, `agent_name`, `sub_agent_id`, `tool_call_id`, `mcp_server`, `model`, `cache_read_tokens`, `cache_creation_tokens`), where applicable. A shared library `aywiz_otel/contract.py` provides the canonical span enrichment helpers.
- **R-900-002** (new, v4): The platform SHALL provide a component C13 (Observability access layer) that exposes a REST API for tenant-scoped consultation of traces, logs, and metrics. C13 enforces RBAC against the tenant + project context propagated from C2, translates aywiz semantic queries into TraceQL/LogQL/PromQL, and emits its own audit log of access.
- **R-900-003** (new, v4): The deployment SHALL include the OSS observability backbone (OpenTelemetry Collector, Tempo, Loki, Prometheus, Grafana). These are infrastructure dependencies, not aywiz components, and are deployed via standard Helm charts referenced in the platform deployment manifest. No aywiz code duplicates their functionality.
- **R-900-004** (new, v4): Ops and SRE access to Grafana is permitted for deep platform debug; end-user (tenant-scoped) access to observability data SHALL go exclusively through C13.

### 9.9 New nomenclature entry — C14 placeholder (v2 only)

The component identifier C14 is **reserved** for a v2 Continuous Improvement Layer per §13 of this synthesis. C14 is not in v1 scope, has no R-* requirements in v1 specs, and is registered in `050-ARCHITECTURE-OVERVIEW.md` solely to prevent the nomenclature slot from being reused.

### 9.10 Requirements rendered obsolete by OpenHands adoption

The following requirements drafted in the gap audit (`analyses/claude-code-patterns-gap-audit.md`) are **rendered unnecessary** by OpenHands and SHALL NOT be implemented:

- R-200-090 (PreTool/PostTool hook framework) — OpenHands provides post-action hooks natively.
- R-200-091 (tool-call audit log as a separate ArangoDB collection) — **replaced by the in-pod git history (R-200-036) as the source of truth**, with a projection job populating an optional `c4_tool_calls` index from git trailers for fast query.
- R-200-025 (allowed tools per agent) — OpenHands `allowed_tools` per agent definition natively.
- R-200-053 (file checkpointing fin-grain) — **replaced by in-pod git history (R-200-036) for durable per-edit history**, with linter-gate auto-revert via `git reset --hard HEAD`.
- R-200-028 (agent todo list) — OpenHands task/subtask model natively (with ArangoDB persistence via C4 adapter).
- R-200-027 (MCP client capability for internal agents) — OpenHands MCP client natively; replaced by §6.2 (static data) and R-200-037 (working data).
- R-200-034 (parent dispatch traceability) — OpenHands event stream captures parent/child links; also captured in git commit trailers (R-200-036).
- R-600-050 (implementer tool surface) — OpenHands built-in tools + working-data MCP (R-200-037) natively.
- R-800-052 (effort level header) — **kept relevant**: C8/LiteLLM translates the per-agent header into the provider-specific reasoning parameter (Databricks `reasoning` content type budget).

The following requirements from the gap audit **remain relevant** and SHALL be implemented:

- R-200-024 (heterogeneous tier default) — applies to **every** phase, including `generate`, since all LLM calls go through C8/LiteLLM.
- R-200-026 (agent definition format YAML+MD) — used by the orchestrator to populate OpenHands' `AgentConfig` and the C8 routing headers.
- R-800-023 v2 (multi-model within single primary provider) — applies to **every** phase including `generate`.
- R-800-032 v2 (intra-provider task-based routing MUST v1) — applies to **every** phase including `generate`.
- R-800-052 (effort level header) — **promoted from "obsolete" list to "remain relevant"**, applies to every phase.
- R-800-084 (intra-provider fallback on 429) — applies to **every** phase including `generate`.

The change vs v2: every "applies to non-`generate` agents" is upgraded to "applies to every phase including `generate`", since the single egress invariant is restored.

---

## 10. Next steps for the Claude Code amendment session

The recommended order for the upcoming work session:

1. **Read this document end-to-end** (you are here).
2. **Read the gap audit** (`analyses/claude-code-patterns-gap-audit.md`) for the original analysis that motivated the harness-adoption opportunity. Note that several R-* drafts from the gap audit are now obsolete per §9.10 above.
3. **Draft D-014 in `999-SYNTHESIS.md`** as the formal decision record. Use the spec template per `meta/100-SPEC-METHODOLOGY.md`.
4. **Amend D-007 and D-011 in `999-SYNTHESIS.md`** per §9.2 and §9.3 above. Increment versions to v2; status `proposed` pending approval.
5. **Draft the new R-200-* requirements** in `200-SPEC-PIPELINE-AGENT.md` per §9.4. Include the formal R-200-029 establishing the OpenHands-as-engine contract and R-200-036 / R-200-037 establishing the in-pod working-data layers.
6. **Draft the new R-400-* requirements** in `400-SPEC-MEMORY-RAG.md` per §9.5 for the internal-agents MCP tool catalogue (static side) and the working-data → static promotion provenance fields.
7. **Draft the new R-600-* requirements** in `600-SPEC-CODE-QUALITY.md` per §9.6 for tree-sitter integration and the verifier JSON report contract.
8. **Open `c4_orchestrator/generate_engine.py`** as a stub module containing the OpenHands `AgentConfig` + `LLMConfig` assembly, the OpenHands invocation, the event-stream subscription, the ArangoDB event persistence adapter, and the result handoff to `review`. This is the only module where `import openhands` (or the actual package name) is allowed.
9. **Open `c9_mcp_server/tools/internal_agents/static/`** as a new subpackage containing the six static-data tool implementations (`rag.search`, `graph.traverse`, etc.). Each tool wraps the corresponding C7 / C5 API call with the project-scope check.
10. **Open `c15_runner/working_data/`** as a new subpackage in the C15 pod image, containing:
    - `mcp_server.py` — the in-process MCP server `aywiz_working` loaded by OpenHands via stdio.
    - `structural_index.py` — tree-sitter integration writing to `.aywiz/symbols.db`.
    - `git_hooks.py` — post-action hooks performing the commit-per-edit pattern.
    - `verifier_stream.py` — the parser for `pytest --json-report` outputs and the `tests.*` tool implementations.
11. **Draft the C15 pod image manifest** (`infra/docker/Dockerfile.c15-runner`) per the recommendation in Q9, with `openhands-ai`, tree-sitter parsers, and the git binary. **No Node.js**.
12. **Define the OpenHands event persistence adapter for ArangoDB** (`c4_orchestrator/openhands_event_store.py`). Wires OpenHands' event callback to a `c4_openhands_events` collection. Document the adapter's contract so version bumps of OpenHands surface API drift.
13. **Define the working-data → static promotion job** in `c12_n8n/workflows/promotion-working-to-static.json`, triggered by the existing release flow, ingesting the run's final state into C7 with provenance metadata.
14. **Resolve open questions Q1-Q13** before implementation locks in. Q1, Q3, Q4, Q11, Q12 are architectural; Q2, Q5-Q10 are operational details that can be refined during implementation. **Q13 is the gate before implementation starts** — the POC's pass/fail decides whether v3 is the final design or whether the team revisits.
15. **Update the test matrix in `065-TEST-MATRIX.md`** to add the new MCP endpoints exposed by C9-extended for internal agents and the in-pod MCP server endpoints exposed by `aywiz_working`.
16. **Update `050-ARCHITECTURE-OVERVIEW.md`** to register the new components: C1 (Frontend), C2 (Backend API), C3 (Session memory) made explicit, C13 (Observability access layer), and C14 (Continuous Improvement Layer placeholder, v2). Add a note on the single-responsibility hosting rule (§14 of this synthesis).
17. **Draft R-200-038** (C4 owns agent code, C15 is pure execution sandbox) in `200-SPEC-PIPELINE-AGENT.md`.
18. **Create new spec `900-SPEC-OBSERVABILITY.md`** with R-900-001 (logging contract), R-900-002 (C13 façade), R-900-003 (OSS stack dependencies), R-900-004 (access boundary between C13 and Grafana). Reference §12 of this synthesis.
19. **Add the docs domain plugin** to `600-SPEC-CODE-QUALITY.md` (rename to `600-SPEC-DOMAIN-PLUGINS.md` if scope is broader than code, or split into `600-SPEC-CODE-QUALITY.md` + `610-SPEC-DOCS-QUALITY.md`). Document the C6 verifier and linter contracts for the docs domain (link-check, markdown-lint, RST validation).
20. **Register C14 placeholder** in `050-ARCHITECTURE-OVERVIEW.md` with explicit note that it is reserved for v2 and has no v1 R-* requirements. Reference §13 of this synthesis for scope.

The session can be split across multiple working passes: pass 0 = item 14 (Q13 POC, **before any spec amendment**); pass 1 = items 3-7, 16-20 (specs, contingent on POC success); pass 2 = items 8-13 (code stubs); pass 3 = Q1-Q12 resolution. Implementation depth follows in subsequent passes.

---

## 11. User-facing layer (v4 explicit)

Before this revision, the synthesis focused on the `generate` engine and its data integration, leaving the user-facing entry points implicit. v4 makes them first-class.

### 11.1 Components

- **C1 — Frontend.** Chat UI, available on web (React/Next.js) and mobile (React Native or equivalent — implementation-defined). Hosts the conversation rendering, the run-status feedback, the artifact preview, the access to historical runs.
- **C2 — Backend API.** Python/FastAPI gateway. Handles authentication, tenant context, routing of run requests to C4, and the consumption of C13 for observability views.
- **C3 — Session memory.** Short-term conversational state per chat. Persistence in PostgreSQL (or an ArangoDB collection if the platform consolidates storage). Holds the last N turns, the resolved system prompt, the running summary that feeds C4 context. Scope is strictly intra-conversation; long-term memory lives in C7 (RAG/graph for static data) and in MinIO bundles (per-run working data).

### 11.2 Use cases supported in v1

The platform supports two domain-parameterised use cases in v1:

| Use case | `domain` selector | Verifier (C6 plugin) | Linter (C6 plugin) | Output formats |
|---|---|---|---|---|
| Code generation | `code` | `pytest --json-report` | ruff + pyright | code + tests + configs |
| Documentation generation | `docs` | link-check + structure validator | markdown-lint + RST lint | md, RST, HTML, PDF |

The `code` plugin is the v1 reference; the `docs` plugin is v1+ (added at platform stabilisation). The chain `architect → editor → verifier → report writer` (workers) is invariant across use cases; only the C6 domain plugin changes the verifier behaviour and the output artifact types.

### 11.3 Flow at run start

1. User submits a query through C1.
2. C2 receives the request, resolves tenant + project context, optionally fetches C3 to enrich with conversation history, then dispatches a run to C4.
3. C4 selects the domain plugin from C6 based on the use case, initialises the run record in `c4_runs` (ArangoDB), and proceeds through phases 1-5.
4. During the run, C2 streams progress events to C1 (via the orchestration event bus / WebSocket).
5. At run completion, artifacts are stored in MinIO; the user can browse them from C1, and the historical view of the run (logs, traces, gate verdicts) is fetched via C2 → C13.

---

## 12. Observability architecture (v4 new)

The platform requires distinguishable traces and logs per component, accessible through a single audited entry point. This is realised through a layered architecture combining OSS standard infrastructure and one new aywiz component (C13).

### 12.1 OSS observability backbone (external infrastructure)

- **OpenTelemetry Collector** (DaemonSet on K8s) — receives OTLP from every aywiz Cn.
- **Tempo** — distributed tracing backend, stores spans.
- **Loki** — logs aggregation backend, stores structured JSON logs.
- **Prometheus** — metrics backend, stores counters/gauges/histograms.
- **Grafana** — dashboards and ad-hoc query interface for ops/SRE.

This stack is not an aywiz Cn. The deployment manifests configure it via standard Helm charts.

### 12.2 C13 — Observability access layer (new aywiz component)

C13 is a **façade**, not a store. It owns:

- A REST API consumed by C2 to render tenant-scoped observability views in C1.
- Translation of aywiz semantic queries (e.g. "show me the timeline of run `XYZ`") into the underlying TraceQL/LogQL/PromQL queries.
- RBAC enforcement (tenant + project scoping) before any backend query is issued.
- Its own audit log (who consulted what, when).

C13 does not duplicate Grafana — it exposes a smaller, scoped, audited surface for end-users. Ops and SRE retain direct Grafana access for deep platform debug.

### 12.3 Logging contract (mandatory)

Every Cn SHALL emit OTel spans and JSON logs that include, where applicable, the following attribute set. C13 indexes on these attributes; without them, cross-component correlation breaks.

| Attribute | Type | When required |
|---|---|---|
| `tenant_id` | string | always (propagated from C2 onward) |
| `project_id` | string | always (propagated from C2 onward) |
| `run_id` | string | as soon as a run is initialised (C4 owns) |
| `phase` | enum | one of `brainstorm, spec, plan, generate, review` — during a run |
| `gate_id` | enum | one of `A, B, C` — at gate evaluation only |
| `agent_name` | string | inside agent execution |
| `sub_agent_id` | string | inside sub-agent dispatch |
| `tool_call_id` | string | per tool call (OpenHands or MCP) |
| `mcp_server` | string | when an MCP tool is invoked |
| `model` | string | per LLM call (C8) |
| `cache_read_tokens`, `cache_creation_tokens` | int | per LLM call where cache was active |

Span hierarchy follows OTel parent-child semantics: a `phase` span has child spans for `gate_id`, `agent_name`, etc.

### 12.4 Where the logging contract is enforced

Implementation responsibilities:

- **C2** initialises `tenant_id` and `project_id` at request entry, propagates via OTel context (W3C tracecontext + baggage headers).
- **C4** owns `run_id`, `phase`, `gate_id`, `agent_name`, `sub_agent_id`.
- **C8** owns `model`, `cache_read_tokens`, `cache_creation_tokens` (these are read from the provider response and added to its own span).
- **C9** owns `mcp_server` and `tool_call_id` for MCP calls.
- **C15 (OpenHands runtime)** emits spans for every action, tagged with the above attributes received from C4 via env vars / agent config.

A library helper (`aywiz_otel/contract.py`) provides the standard span enrichment functions used by all components.

---

## 13. Continuous Improvement Layer — C14 placeholder (v2 only)

### 13.1 Scope

C14 is reserved in the nomenclature for a v2 feature: a passive analysis layer that ingests data already produced by C13/Tempo/Loki and identifies opportunities for improvement. **It never auto-applies changes**. It produces a **developer to-do list** consumed by the platform team.

### 13.2 What it does (v2 scope, level N1 only)

- Runs as a scheduled batch job (weekly or monthly) — likely orchestrated by C12 (n8n).
- Internally, it is itself an OpenHands run (dogfooding): the platform's own agent pipeline analysing the platform's own logs.
- It queries C13 for patterns:
  - Friction points (where agents repeatedly fail or retry)
  - Latency outliers (phases consuming P99 > some threshold)
  - Cost anomalies (runs costing X× the median for similar size)
  - Recurring error signatures in `Gate C BLOCKED` outcomes
- It produces a `IMPROVEMENT_BACKLOG.md` artifact: a structured list of suggested platform improvements, each entry citing the supporting evidence (linked spans, runs, metrics) accessible via C13.
- The developer team reviews the backlog. Selected items go through aywiz's own pipeline as normal changes (spec → plan → generate → review).

### 13.3 What it explicitly does NOT do (and why)

- **Never auto-applies changes** — preserves traceability of versions, conformity with ISO 21434-style requirements for controlled releases, and avoids feedback-loop drift (Goodhart's law).
- **Never modifies prompts or agent definitions in flight** — these are owned by C4 and only changed through controlled release.
- **Never modifies the C7 knowledge graph from its own analysis** — only humans can promote a finding into structured static data.

Levels N2-N4 (semi-automated recommendations, auto-update of bounded actions, agent-context RAG augmentation) are explicitly out of v2 scope. They are research-stage and require RLHF-grade ground truth that the platform does not yet produce.

### 13.4 What needs to be true before C14 can exist

C14 is dependent on:

1. C13 fully operational (§12) — required for log/trace querying.
2. The logging contract (§12.3) consistently applied across all Cn.
3. A stable v1 baseline (POC OpenHands passed per Q13, platform in production for at least one quarter to accumulate signal).

Therefore C14 is **not in v1 specs**. It is **registered in the nomenclature** so no other component takes that slot.

---

## 14. Single-responsibility hosting rule (v4 codified)

The architecture has been audited for ambiguous dual hosting. The rule below applies to every spec entry and diagram going forward.

### 14.1 The rule

Every feature has **exactly one runtime host** — the Cn where its code executes at run-time. When a feature also has configuration or input data sourced from another component, that source is named separately and explicitly, not as a co-host.

### 14.2 Notation

Three patterns, in order of frequency:

- **`(Cn)`** — feature has a single host. Example: "Budget guard (C8)".
- **`runs in Cn · rules from Cm`** — the runtime is Cn; the configuration that parameterises it comes from Cm. Cn and Cm have distinct responsibilities, no shared ownership. Example: "Linter gate · runs in C15 · rules from C6".
- **`in Cn · invokes Cm`** — Cn exposes the surface; the underlying logic is invoked from Cm. Pattern: Backend-for-Frontend / façade. Example: "Validation MCP · in C9 · invokes C6".

### 14.3 What is NOT shared hosting

- **References** (`refs Cm items`) — the feature reads data from Cm but Cm has no execution responsibility. Example: "Definition of Done · refs C5 items".
- **External infrastructure** — when no Cn fits, the host is named explicitly as external. Example: "Trace tree · external OTel collector".

### 14.4 Cross-references in the diagram

The diagram accompanying this synthesis (`analyses/aywiz_v3_single_responsibility_diagram.svg`) renders the architecture in twelve functional zones, with hosting annotated per the above conventions. Cross-zone relationships (e.g. workers consume MCP services, which query persistent stores) are stated in zone headers rather than drawn as long arrows that would cross unrelated zones — keeping the diagram readable at 680px width without losing the cross-component flow story.

---

## 15. Companion documents

- `analyses/claude-code-patterns-gap-audit.md` (v1) — the original gap analysis that surfaced the harness-adoption opportunity. Still valid as background; some R-* drafts are now obsolete per §9.10 of this document.
- `analyses/aywiz_v3_single_responsibility_diagram.svg` — the visual reference companion; renders the twelve functional zones with single-responsibility hosting annotations.
- `999-SYNTHESIS.md` — the platform decision log; D-014 lands here.
- `050-ARCHITECTURE-OVERVIEW.md` — the component overview; needs additions for C1/C2/C3 explicit reframing, C13 (observability access), C14 placeholder (continuous improvement v2).
- `100-SPEC-ARCHITECTURE.md` — the component catalogue; needs notes on C9-extended (Validation MCP, Future MCPs slot), C15 (pure execution sandbox, no aywiz code), and on the single-responsibility hosting rule.
- `200-SPEC-PIPELINE-AGENT.md` — the orchestrator + agent spec; major delta including the working-data layers, the C4-owns-agent-code clarification (new R-200-038), the use cases parameterisation by C6 domain plugin.
- `400-SPEC-MEMORY-RAG.md` — the memory spec; delta for the internal-agents MCP catalogue (static side, Validation MCP, Future MCPs) and working-data → static promotion provenance.
- `600-SPEC-CODE-QUALITY.md` — the code domain spec; delta for tree-sitter and verifier report contract; plus the docs domain plugin is hosted alongside.
- `800-SPEC-LLM-ABSTRACTION.md` — the LLM gateway spec; clarification that D-011 fully applies, with the budget guard kept inside C8 (not in a separate Watchers zone).
- **New: `900-SPEC-OBSERVABILITY.md`** — to be created. Defines C13, the logging contract (R-900-001), the C13 API surface (R-900-002), the deployment dependencies on Tempo/Loki/Prometheus/Grafana.

---

**End of `aywiz-architecture-synthesis-v4.md`.**
