---
document: 800-SPEC-LLM-ABSTRACTION
version: 4
path: requirements/800-SPEC-LLM-ABSTRACTION.md
language: en
status: draft
derives-from: [D-002, D-011, D-012, D-020]
---

# LLM Abstraction Specification

> **Purpose of this document.** Specify the LLM Gateway (C8): the LiteLLM proxy deployment, the OpenAI-compatible API contract, provider and model management, routing strategies across v1/v2/v3 stages, the per-agent feature catalog, cost tracking, budget enforcement, and the logging hooks required for the v2 eval harness. This spec defines the contract between C8 and every component that invokes an LLM.

> **Version 4 changes.** **R-800-131 v1→v2** and **R-800-132 v1→v2** make the `cache_control` prompt-marker structure normative (was: "prompt_caching is a required feature" — too implicit; without the explicit marker placement, the provider's cache does not key on the sliding-window prefix). New **R-800-134** declares `ayextract.decontextualizer_screener` (Haiku-class, ~50 input + ~10 output tokens per call) — the 2-tier gating from D-020 v2 §A. §8.1 `agent_routes:` extended with the screener entry. R-800-131 v2 now explicitly conditions the decontextualiser invocation on the screener's YES verdict (the gate, not a separate agent dependency). All four C13 agents inherit the `urgency=background` routing option declared at R-100-125 v2 §2 (batch API).

> **Version 3 changes.** §4.6 per-agent feature catalog extended with four AyExtractor (C13) agents — `ayextract.image_analyzer`, `ayextract.decontextualizer`, `ayextract.summarizer`, `ayextract.densifier` — per **D-020** + R-100-125 §4. New **R-800-130..133** (one per agent) declare the feature requirements + preferred model class for each. §8.1 sample `agent_routes:` extended accordingly. C13 reaches C8 via the OpenAI-compatible API surface (`OPENAI_BASE_URL=http://c8:8000/v1` + `OPENAI_API_KEY=$C8_GATEWAY_API_KEY`) — no code change in AyExtractor, configuration-only routing.

---

## 1. Purpose & Scope

This document specifies the LLM Gateway (C8) of the platform: the single point of egress for all LLM invocations. It establishes:

- The deployment shape of LiteLLM as a shared cluster service.
- The API contract exposed to internal components (OpenAI-compatible REST).
- Provider and model configuration management.
- The staged routing model (v1 single-provider multi-model, v2 task-based, v3 ensemble).
- The per-agent LLM feature requirements catalog.
- Cost tracking through propagated tags.
- Rate limiting and budget caps (soft + hard).
- Fallback behaviour on provider failure.
- Logging hooks required to enable the v2 eval harness without refactor.

**Out of scope.**
- Agent-level prompt engineering (→ `200-SPEC-PIPELINE-AGENT.md`).
- Embedding computation (→ `400-SPEC-MEMORY-RAG.md` for embedding models; this spec covers only completion/chat providers).
- Specific model evaluations and benchmark results (operational, not architectural).
- Multi-LLM ensemble algorithms (v3 roadmap, only principle mentioned here).

---

## 2. Relationship to Synthesis Decisions

| Decision | How this document operationalises it |
|---|---|
| D-002 (stack reuse) | LiteLLM is deployed inside the Kubernetes cluster. No external managed LLM gateway is introduced. |
| D-011 (multi-LLM abstraction via LiteLLM) | Defines the concrete proxy shape, the API contract, and the staged routing model (levels 1/2/3 mapped to versions v1/v2/v3). |
| D-012 (domain extensibility) | The API and feature catalog are not hard-coded to the `code` domain. New domain-specific agents register their feature requirements against the same catalog mechanism. |

---

## 3. Glossary

| Term | Definition |
|---|---|
| **Provider** | A distinct LLM vendor (Anthropic, OpenAI, Google, Mistral, local Ollama, etc.). |
| **Model** | A specific offering of a provider (e.g. `claude-opus-4-7`, `gpt-4o`, `gemini-2.5-pro`). |
| **Route** | A named mapping from a client-declared role (agent name, task type) to a specific model on a specific provider. |
| **Feature** | A provider/model capability such as prompt caching, structured outputs, tool calling, vision, long context, extended thinking. Not uniform across providers. |
| **Tag** | A key-value pair attached to an LLM request, propagated through logs and cost records for post-hoc aggregation (project, user, session, phase, agent, sub-agent). |
| **Hard cap** | A budget ceiling that blocks new calls when crossed. |
| **Soft cap** | A budget ceiling that triggers alerts but does not block. |
| **Eval hook** | A logging point in C8 that captures enough information to replay the request against a different model in post-hoc evaluation. |
| **Request fingerprint** | A deterministic hash of request inputs (model, messages, tools, parameters) used for deduplication and caching decisions. |

---

## 4. Functional Requirements

### 4.1 Proxy deployment

#### R-800-001

```yaml
id: R-800-001
version: 1
status: draft
category: architecture
```

C8 (LLM Gateway) SHALL be deployed as a **single shared Kubernetes service** running LiteLLM in proxy mode. All components that invoke LLMs SHALL reach C8 via its ClusterIP service (`http://litellm.<namespace>.svc/v1`). Per-component sidecars are not used in v1.

**Rationale.** Per Q-800-α decision. Shared service simplifies observability, cost tracking, and configuration. Latency overhead of an additional hop is negligible relative to LLM response times (tens of ms vs hundreds of ms to several seconds).

#### R-800-002

```yaml
id: R-800-002
version: 1
status: draft
category: architecture
```

C8 SHALL be horizontally scalable via HPA per R-100-050. In production, the HPA `minReplicas` for C8 SHALL be set to 2, overriding the default `minReplicas=1` from R-100-051. In local development, `minReplicas=1` remains acceptable.

**Rationale.** C8 is on the critical path of every LLM call; a single replica creates an unnecessary SPOF in production. Local development tolerates one replica as parity is not safety-critical there.

#### R-800-003

```yaml
id: R-800-003
version: 1
status: draft
category: architecture
```

C8 deployment manifests SHALL configure Kubernetes rolling updates with `maxUnavailable=0` and `maxSurge=1`, ensuring zero-downtime deployments. Readiness probes (per R-100-004) SHALL include validation of at least one configured upstream provider's availability.

**Rationale.** Because C8 sits on every LLM call, any deployment window with reduced capacity cascades into user-facing latency. Provider-availability-aware readiness prevents routing traffic to replicas whose configuration isn't effective yet.

#### R-800-004

```yaml
id: R-800-004
version: 1
status: draft
category: architecture
```

C8 SHALL be stateless (per R-100-003). All state (configuration, routes, budgets, audit logs) SHALL be externalised to ArangoDB (C11) for durable records and Redis (or equivalent, local to the cluster) for ephemeral rate-limit counters and idempotency caches.

**Rationale.** Standard statelessness requirement; allows horizontal scaling and rolling updates without state migration.

---

### 4.2 API contract

#### R-800-010

```yaml
id: R-800-010
version: 1
status: draft
category: functional
```

C8 SHALL expose an **OpenAI-compatible REST API** rooted at `/v1/`. The minimum v1 endpoint set SHALL include:

- `POST /v1/chat/completions` — chat/completion requests, streaming and non-streaming.
- `GET /v1/models` — enumeration of models accessible to the caller (after route resolution).
- `GET /v1/health` — liveness and readiness.

Other OpenAI-compatible endpoints (embeddings, image generation, audio) are **out of scope for this document**. Embeddings are addressed in `400-SPEC-MEMORY-RAG.md`. Image and audio capabilities are deferred.

**Rationale.** Per D-011: OpenAI-compatible is the LiteLLM baseline and is the de facto standard, enabling any OpenAI-SDK-compatible client to work against C8 without code change.

#### R-800-011

```yaml
id: R-800-011
version: 1
status: draft
category: functional
```

C8 SHALL accept only HTTP access via the proxy contract. It SHALL NOT be used as an importable Python SDK by any internal component. Attempts to import `litellm` as a library inside other components are prohibited by architectural policy.

**Rationale.** Per Q-800-β decision. Library-mode usage would bypass C8's observability, cost tracking, and budget enforcement, defeating the single-egress principle (R-100-011).

#### R-800-012

```yaml
id: R-800-012
version: 1
status: draft
category: security
```

Every incoming request to C8 SHALL carry a valid platform JWT in the `Authorization: Bearer <token>` header (per E-100-001). Anonymous requests SHALL be rejected with HTTP 401 in all authentication modes.

**Rationale.** C8 is the only egress to paid providers. Authenticated requests are a prerequisite for cost attribution, budget enforcement, and audit.

#### R-800-013

```yaml
id: R-800-013
version: 1
status: draft
category: functional
```

Requests SHALL include the following HTTP headers in addition to the standard OpenAI payload:

- `X-Agent-Name: <agent-identifier>` — declares the calling agent (e.g. `architect`, `planner`, `implementer`, `spec-reviewer`, `quality-reviewer`). Used for routing (§4.4) and tagging (§4.8).
- `X-Session-Id: <session-id>` — conversation session identifier for cost aggregation.
- `X-Phase: <phase-name>` — current pipeline phase (e.g. `brainstorm`, `spec`, `plan`, `generate`, `review`). Optional outside the orchestration flow.
- `X-Sub-Agent-Id: <sub-agent-id>` — ephemeral sub-agent identifier if applicable. Optional.
- `X-Cache-Hint: static | dynamic | none` — caching advisory (§4.5). Optional.

The first two headers are **mandatory**; missing mandatory headers cause HTTP 400 rejection.

**Rationale.** Tags enable cost tracking granularity (Q-800-ε). Cache hints enable prompt caching where the provider supports it (Q-800-γ implementation detail). Agent name drives routing.

#### R-800-014

```yaml
id: R-800-014
version: 1
status: draft
category: functional
```

C8 SHALL support streaming responses (Server-Sent Events) for `POST /v1/chat/completions` when the client sets `"stream": true` in the payload. Streaming SHALL preserve the OpenAI SSE wire format without modification.

**Rationale.** Streaming is required for responsive conversational UX (C3) and for long-context generation agents. OpenAI compatibility demands SSE.

#### R-800-015

```yaml
id: R-800-015
version: 1
status: draft
category: functional
```

C8 SHALL normalise provider-specific response fields to the OpenAI contract. Non-standard fields MAY be exposed in a `_provider_extensions` envelope for debugging and advanced use, but callers SHALL NOT depend on their presence. Documented provider extensions SHALL be surfaced through this envelope only.

**Rationale.** D-011 explicitly warns about provider feature parity. The envelope keeps the main contract clean; callers that want provider-specific data opt in.

---

### 4.3 Provider & model configuration

#### R-800-020

```yaml
id: R-800-020
version: 1
status: draft
category: functional
```

C8 configuration SHALL be declared in a YAML file (`litellm-config.yaml`) mounted as a Kubernetes ConfigMap. The file structure follows LiteLLM's native format. Changes to the configuration SHALL be applied via a config reload endpoint (admin-only) and SHALL NOT require pod restarts in the normal path.

**Rationale.** Per D-011: configuration-driven provider swapping without application code changes. Hot reload avoids disruption on routine configuration updates.

#### R-800-021

```yaml
id: R-800-021
version: 1
status: draft
category: security
```

Provider API keys SHALL NOT be stored in the ConfigMap. Secrets SHALL be mounted from Kubernetes Secrets and referenced by name in the configuration. In production, Kubernetes Secrets SHALL be populated via the External Secrets Operator reading from Azure Key Vault. In local development, Kubernetes Secrets are populated directly.

**Rationale.** Per Q-800-γ decision (option b). Same flow at the C8 application layer across environments; provenance differs via ESO.

#### R-800-022

```yaml
id: R-800-022
version: 1
status: draft
category: functional
```

The configuration SHALL support at minimum the following providers in v1: Anthropic, OpenAI, Google (Gemini), Mistral, Ollama (local), Azure OpenAI. Adding a new provider SHALL require only configuration changes, not code changes, provided LiteLLM supports the provider natively.

**Rationale.** Baseline provider coverage for typical deployment scenarios. Actual provider activation is per-deployment; all six need not be enabled.

#### R-800-023

```yaml
id: R-800-023
version: 1
status: draft
category: functional
```

At any point in time, the configuration SHALL declare exactly one **active primary provider** and zero or more active models from that provider. Models from other providers MAY be defined for fallback (see §4.9) but SHALL NOT be used for primary routing in v1 (per D-011 level 1).

**Rationale.** Per Q-800-η decision. v1 is single-provider multi-model; v2 introduces multi-provider routing (level 2). The configuration schema anticipates both, but v1 enforces the single-active-provider constraint.

#### R-800-024

```yaml
id: R-800-024
version: 1
status: draft
category: functional
```

Each model declared in the configuration SHALL carry metadata attributes:
- `display_name` (string) — human-readable name.
- `features` (list of strings) — supported capabilities from the feature catalog in §4.5.
- `context_window` (integer) — maximum context size in tokens.
- `cost_per_million_input` (float) — cost in USD per million input tokens.
- `cost_per_million_output` (float) — cost in USD per million output tokens.
- `rate_limit_rpm` (integer, optional) — provider-imposed requests per minute.
- `rate_limit_tpm` (integer, optional) — provider-imposed tokens per minute.

Metadata SHALL be consumed by the router (§4.4) and the cost tracker (§4.8).

**Rationale.** Routing decisions require feature capability and cost awareness. Rate limit metadata enables C8 to protect providers from overload before they return 429.

---

### 4.4 Routing

#### R-800-030

```yaml
id: R-800-030
version: 1
status: draft
category: functional
```

C8 SHALL implement a **route resolver** that maps each incoming request to a concrete model based on, in priority order:
1. Explicit `model:` field in the request payload, if it names a configured model.
2. The `X-Agent-Name:` header, matched against the agent-to-model mapping in the configuration (§4.6).
3. A default model declared in the configuration as the fallback for requests matching neither.

If none of these resolves to a configured model, C8 SHALL return HTTP 400 with an explanatory error.

**Rationale.** Three-level resolution accommodates explicit control (test harnesses, admin tools), agent-centric mapping (normal operation), and sane default behaviour.

**v1 implementation note (2026-05-20).** "C8" denotes the LLM-abstraction system as a whole — proxy + SDK client. The resolver step MAY live in the **client SDK** (`LLMGatewayClient`) in v1 : the client resolves `agent_name → model_name` from a locally-loaded copy of the `agent_routes:` table and forwards the resolved name to the LiteLLM proxy as the OpenAI `model:` field. This sidesteps the absence of native `X-Agent-Name`-aware routing in upstream LiteLLM, keeps the proxy off-the-shelf, and preserves the spec's three-level priority. The proxy still receives + logs the `X-Agent-Name` header for audit, cost attribution, and budget enforcement (R-800-013 / R-800-070). v2 moves the resolver into a proxy-side admission hook (Q-800-011).

#### R-800-031

```yaml
id: R-800-031
version: 1
status: draft
category: functional
```

In v1, routing SHALL be **deterministic**: identical resolution inputs SHALL always resolve to the same model. Load-balancing across multiple replicas of the same model is permitted; load-balancing across different models is not.

**Rationale.** Deterministic routing is essential for reproducibility in debugging and for the eval harness (§4.10). Probabilistic routing is a v2+ feature (ensemble mode).

#### R-800-032

```yaml
id: R-800-032
version: 1
status: draft
category: functional
```

v2 routing (task-based per D-011 level 2) SHALL extend §4.4 with conditional rules keyed on `X-Phase:`, `X-Agent-Name:`, request size, or other attributes. v2 rules are declared in the configuration; v1 deployments MAY predeclare v2 rules that remain inactive until v2 flag is enabled.

**Rationale.** Forward compatibility: v1 configs can be written with v2-ready structure.

#### R-800-033

```yaml
id: R-800-033
version: 1
status: draft
category: functional
```

v3 routing (ensemble per D-011 level 3) is **out of scope for v1**. The configuration schema SHALL leave a designated namespace for v3 ensemble rules to avoid future breaking changes.

**Rationale.** Roadmap preservation without v1 implementation burden.

---

### 4.5 Feature compatibility

#### R-800-040

```yaml
id: R-800-040
version: 1
status: draft
category: functional
```

C8 SHALL maintain a **feature catalog** listing all capabilities relevant to the platform's agents. The v1 minimum catalog SHALL include:

- `chat_completion` — baseline chat API support.
- `tool_calling` — function/tool calling per OpenAI contract.
- `structured_outputs` — JSON-schema-guided output.
- `vision` — image input.
- `long_context` — context window ≥ 128k tokens.
- `extended_thinking` — explicit reasoning mode (Claude extended thinking, OpenAI o-series reasoning).
- `prompt_caching` — server-side prompt caching (Anthropic, OpenAI cached prompts).
- `streaming` — SSE streaming support.

Each configured model declares which features it supports via its `features:` attribute (R-800-024). The catalog is extensible; new features are added through amendments to this document.

**Rationale.** Per Q-800-δ: features are declared normatively in the spec, projected operationally in the configuration. Normative declaration enables audit and ensures agents can reason about capability independently of any single provider.

#### R-800-041

```yaml
id: R-800-041
version: 1
status: draft
category: functional
```

When a request requires a feature the resolved model does not support, C8 SHALL reject the request with HTTP 422 and a body identifying the missing feature. Silent degradation is prohibited.

**Rationale.** Silent feature drop (e.g. ignoring a `tools:` array because the model doesn't support tool calling) yields incorrect results. Explicit failure forces callers to request an appropriate model.

#### R-800-042

```yaml
id: R-800-042
version: 1
status: draft
category: functional
```

When `X-Cache-Hint: static` is set on a request, C8 SHALL attempt to enable provider-side prompt caching for the static portion of the prompt if the resolved model supports `prompt_caching`. If the model does not support caching, the hint is silently ignored (best-effort semantic).

**Rationale.** Prompt caching is provider-specific and can reduce cost by an order of magnitude on agents with large stable system prompts (e.g. the Architect agent holding the methodology corpus). Best-effort semantics avoid hard failures while unlocking savings when possible.

---

### 4.6 Per-agent LLM requirements catalog

The following table is normative and defines the features each v1 agent requires from its routed model. Configurations SHALL map each agent to a model that supports at least the required features.

| Agent | Required features | Preferred model class | Fallback acceptable |
|---|---|---|---|
| `architect` | `chat_completion`, `streaming`, `long_context`, `prompt_caching`, `extended_thinking` | Flagship (Claude Opus, GPT-5) | Flagship of another provider |
| `planner` | `chat_completion`, `streaming`, `structured_outputs`, `long_context` | Flagship or mid-tier | Mid-tier (Sonnet, GPT-4o) |
| `implementer` | `chat_completion`, `streaming`, `tool_calling`, `long_context` | Mid-tier (Sonnet) | Fast tier (Haiku, GPT-4o-mini) |
| `spec-reviewer` | `chat_completion`, `structured_outputs`, `long_context` | Mid-tier | Fast tier |
| `quality-reviewer` | `chat_completion`, `structured_outputs`, `long_context` | Mid-tier | Fast tier |
| `sub-agent` (generic ephemeral) | `chat_completion`, `tool_calling` | Fast tier | Any that meets required features |
| `ayextract.image_analyzer` | `chat_completion`, `vision` | Mid-tier vision-capable (Sonnet) | Flagship vision-capable |
| `ayextract.decontextualizer_screener` | `chat_completion` | Fast tier (Haiku) | Any cost-leader |
| `ayextract.decontextualizer` | `chat_completion`, `prompt_caching` | Mid-tier (Sonnet) | Fast tier |
| `ayextract.summarizer` | `chat_completion`, `prompt_caching` | Mid-tier (Sonnet) | Fast tier |
| `ayextract.densifier` | `chat_completion`, `prompt_caching`, `long_context` | Mid-tier (Sonnet) | Flagship |

The catalog applies to the `code` production domain (v1) and the AyExtractor (C13) ingestion path (D-020 v1: Phase 1 + Phase 2 agents only). Future domains (v2+) register additional rows as they land. Phase 3 AyExtractor agents (`concept_extractor`, `community_summarizer`, `profile_generator`, `synthesizer`, `critic`) are out of v1 scope (Q-200-022) and SHALL be added to the catalog when adopted.

#### R-800-050

```yaml
id: R-800-050
version: 1
status: draft
category: functional
```

The agent-to-model mapping declared in the C8 configuration SHALL be consistent with the feature catalog above. C8 SHALL validate this on configuration load and refuse to apply a configuration that maps an agent to a model lacking a required feature.

**Rationale.** Catches misconfigurations at deploy time rather than at request time.

#### R-800-051

```yaml
id: R-800-051
version: 1
status: draft
category: functional
```

Adding a new agent (for example a new domain's agent in v2+) SHALL require updating this document with a new row in the catalog and amending the configuration accordingly. Agents without an entry SHALL fall back to the default model (per R-800-030 step 3) and SHALL emit a warning log.

**Rationale.** Keeps the normative catalog authoritative while tolerating the transient case of a newly introduced agent not yet documented.

#### R-800-130

```yaml
id: R-800-130
version: 1
status: draft
category: functional
derives-from: [D-020, R-100-125]
```

The `ayextract.image_analyzer` agent — used by C13 (AyExtractor) to extract text + structural descriptions from embedded images via LLM Vision — SHALL be routed to a model offering the `vision` capability. v1 baseline assignment: **mid-tier vision-capable** (Claude Sonnet 4.5 or equivalent). Fallback to flagship vision-capable is acceptable; fallback to a non-vision model is NOT (the call SHALL fail with a clear error rather than silently return text-only output).

This agent is **mandatory** when C13 processes a document containing embedded images, regardless of the project's `quality_tier` setting (R-400-224) — Vision is the only way to recover semantic content from a screenshot, diagram, or scanned page. For pure-text documents (no images detected during extraction) C13 SHALL NOT invoke this agent.

**Rationale.** Image content cannot be deterministically extracted by libraries (OCR alone misses diagram semantics, chart labels, structured tables in screenshots). LLM Vision is the necessary tool here; the platform's frugality stance (D-020) accepts this cost as unavoidable.

#### R-800-131

```yaml
id: R-800-131
version: 2
status: draft
category: functional
derives-from: [D-020, R-100-125, R-400-224, R-800-134]
```

The `ayextract.decontextualizer` agent — used by C13 to rewrite chunks with explicit references (resolve pronouns, anaphoras, anchor implicit subjects, disambiguate semantic referents such as "l'entreprise" / "the system" / "as described above") before embedding — SHALL be routed to a **mid-tier** model (Claude Sonnet 4.5 or equivalent).

**Invocation gating (D-020 v2 §A).** This agent SHALL be invoked under both conditions:

1. The project's effective `quality_tier` is `high` (R-400-224). For `minimal` and `standard` tiers, C13 SHALL NOT invoke this agent.
2. The `ayextract.decontextualizer_screener` (R-800-134) returned `NEEDS_DECONTEXT: YES` for the chunk. When the screener returns `NO`, C13 SHALL skip this agent and set `ChunkRich.text = ChunkRich.original_text`. The screener's verdict + reason SHALL be persisted to `02_chunks/screener_log.jsonl` per R-400-220 v2.

**Prompt structure (D-020 v2 §B — mandatory).** The agent's prompt SHALL be structured as:

```
[STABLE PREFIX]
  system instructions + ontology/domain primer
  running_summary (cumulative Refine output up to chunk N-1)
  sliding_window: chunks [N-K .. N-1] decontextualised text
cache_control: {type: "ephemeral"}
[VARIABLE SUFFIX]
  chunk N original_text (the one being rewritten)
```

The provider-specific cache marker (Anthropic `cache_control: {type: "ephemeral"}` block, OpenAI equivalent, etc.) SHALL be placed at the boundary between the stable prefix and the variable suffix. Without this explicit placement the provider does not key the cache and the per-chunk cost gain (10× discount on cached input tokens) is forfeit.

**Rationale.** v1 routed the decontextualiser to fast tier (Haiku) for cost. v2 splits the work in two: a Haiku screener (R-800-134) gates whether a chunk needs decontextualisation at all (catching ~30-50% of chunks that are already self-contained), and the remaining chunks — by construction the harder semantic-ambiguity cases — are routed to mid-tier (Sonnet) for better resolution quality. Combined with mandatory `prompt_caching` structure, the net cost of decontextualisation drops below v1's Haiku-only baseline while quality improves on the ambiguous cases. Math: with skip rate s and worker-call cost C_W, screener cost C_S, v1 cost = N·C_W(Haiku) ≈ N·$0.0005; v2 cost = N·C_S(Haiku) + (1-s)·N·C_W(Sonnet) ≈ N·$0.0001 + 0.6·N·$0.005·0.2 (with prompt-caching saving 80%) = N·$0.0007 → cost-comparable with materially better quality on the ambiguous subset.

**Supersedes** R-800-131 v1 (which routed all decontextualisation to fast tier without screener gating).

#### R-800-132

```yaml
id: R-800-132
version: 2
status: draft
category: functional
derives-from: [D-020, R-100-125, R-400-224]
```

The `ayextract.summarizer` agent — used by C13 to compute the **Refine**-style cumulative summary as the chunking pass advances — SHALL be routed to a **mid-tier** model (Claude Sonnet 4.5 or equivalent).

**Prompt structure (D-020 v2 §B — mandatory).** The agent's prompt SHALL be structured as:

```
[STABLE PREFIX]
  system instructions
  running_summary (cumulative summary up to chunk N-1)
cache_control: {type: "ephemeral"}
[VARIABLE SUFFIX]
  chunk N text (the one being summarised in)
```

The provider-specific cache marker (Anthropic `cache_control: {type: "ephemeral"}`, OpenAI equivalent) SHALL be placed at the boundary between the stable prefix and the variable suffix. Without this explicit placement the provider does not key the cache and the per-chunk cost gain (10× discount on cached input tokens) is forfeit.

This agent SHALL be invoked when the project's effective `quality_tier` is `standard` OR `high` (R-400-224). For `minimal`, C13 SHALL NOT invoke this agent.

**Rationale.** Refine summarisation is a per-chunk LLM call where the output materially feeds the next call (cumulative summary as context). Quality degradation between mid-tier and flagship is small on this task; cost degradation is large. Mid-tier is the sweet spot.

**v2 change.** The `prompt_caching` requirement from v1 is now explicit on the **prompt structure** — providers do not auto-key the cache without an explicit `cache_control` marker (or equivalent). Materialising the discount requires the structure above.

**Supersedes** R-800-132 v1 (which mandated `prompt_caching` feature but not the marker placement).

#### R-800-133

```yaml
id: R-800-133
version: 1
status: draft
category: functional
derives-from: [D-020, R-100-125, R-400-224]
```

The `ayextract.densifier` agent — used by C13 to apply the **Chain of Density** technique (5 iterations refining the document-level summary into a dense, entity-saturated form) — SHALL be routed to a **mid-tier** model (Claude Sonnet 4.5 or equivalent) and SHALL require both `prompt_caching` AND `long_context` (the final iteration's prompt carries the full Refine summary + 4 prior dense iterations).

This agent SHALL be invoked **only** when the project's effective `quality_tier` is `high` (R-400-224). The output (`dense_summary.md`) is duplicated as the `global_summary` field on every `ChunkRich` (R-400-222).

**Rationale.** Chain of Density is bounded (exactly 5 iterations per document, not per chunk) so the cost is well-controlled even at flagship pricing. Mid-tier is sufficient — the iteration loop self-corrects across passes, reducing the per-call quality bar. The agent runs once per document (not per chunk), justifying the slightly heavier model class than the per-chunk `decontextualizer`.

#### R-800-134

```yaml
id: R-800-134
version: 1
status: draft
category: functional
derives-from: [D-020, R-100-125, R-400-224, R-800-131]
```

The `ayextract.decontextualizer_screener` agent — used by C13 as a **cheap pre-decision** of whether the expensive decontextualiser (R-800-131 v2) needs to run on a given chunk — SHALL be routed to a **fast tier** cost-leader model (Claude Haiku 4.5 or equivalent, or a cheaper provider if cost/quality dictates).

**Prompt + response shape.** The screener invocation SHALL be a single short prompt:

```
SYSTEM: You decide whether a chunk needs decontextualisation.
A chunk needs decontextualisation when it contains references that
require external context to resolve (pronouns without antecedent, vague
nouns like "the company" / "the system" / "the standard", deictic
references like "above", "previously", incomplete acronyms, …).
A chunk does NOT need decontextualisation when its meaning is fully
self-contained.

USER: <chunk text, max ~500 tokens — TRUNCATED if longer>
Respond exactly:
VERDICT: YES|NO
REASON: <≤20 words explaining the choice>
```

The agent SHALL output ≤ 30 tokens. Total per-call cost at Haiku pricing is approximately **$0.0001 per chunk**.

**Invocation.** The screener SHALL be invoked when the project's effective `quality_tier == high` (R-400-224). Its verdict SHALL be parsed by C13 into a `{verdict: "YES"|"NO", reason: str}` structure, written to `02_chunks/screener_log.jsonl` (R-400-220 v2), and aggregated in `RunManifest.screener_stats` (R-400-221 v2). A failed parse (model output malformed) SHALL default to `verdict=YES` (safer, runs the decontextualiser) — this fallback SHALL be flagged in `reason`.

**No prompt caching.** The screener prompt is short and per-chunk-variable — caching is not applicable.

**Rationale.** The 2-tier gating (cheap screener → expensive worker) is a textbook cost-quality optimisation in LLM pipelines. The screener catches the chunks that don't need decontextualisation (estimated 30-50% of chunks on typical technical docs) while preserving the option of full decontextualisation on the harder cases. Break-even mathematically at ≥ 2% skip rate (screener cost $0.0001 vs worker cost $0.005 → 50× ratio). The screener is functionally simpler than the worker (binary classifier, no rewrite) so a smaller model is sufficient — using Sonnet here would be wasteful. See D-020 v2 §A and R-100-125 v2 §5 for the platform-level rationale.

---

### 4.7 Rate limiting & budget caps

#### R-800-060

```yaml
id: R-800-060
version: 1
status: draft
category: functional
```

C8 SHALL enforce **rate limiting** at three levels:
1. Per-provider aggregate (protects provider from overload, aligned with `rate_limit_rpm` / `rate_limit_tpm` metadata).
2. Per-tenant (prevents a tenant from consuming disproportionate capacity).
3. Per-user (prevents abuse within a tenant).

Rate limit exceeded SHALL return HTTP 429 with a `Retry-After` header.

**Rationale.** Multi-level rate limiting protects the system at three distinct failure modes: provider overload, tenant monopolisation, user abuse. All three are observed in practice.

#### R-800-061

```yaml
id: R-800-061
version: 1
status: draft
category: functional
```

C8 SHALL enforce **budget caps** in two modes, configurable per tenant and per project:

- **Soft cap**: when reached, C8 continues serving requests but emits structured alerts (log + metric + NATS event `billing.alert.soft_cap_reached`).
- **Hard cap**: when reached, C8 rejects new requests with HTTP 402 Payment Required and an explanatory error body. Existing in-flight requests continue.

Default values (per Q-800-ζ): hard cap 100 USD per month per project; soft cap at 80% of the hard cap. Defaults MAY be overridden per tenant by administrators.

**Rationale.** Defense in depth against runaway costs. Soft cap enables early warning without service disruption; hard cap prevents catastrophic billing events.

#### R-800-062

```yaml
id: R-800-062
version: 1
status: draft
category: functional
```

Budget cap state (current consumption, period boundaries) SHALL be persisted in ArangoDB and updated transactionally on every completed request. Cap evaluation on new requests SHALL consult the persisted state, not in-memory counters only.

**Rationale.** In-memory-only counters lose accuracy across pod restarts and horizontal scaling events. Persistence is required for accurate enforcement.

#### R-800-063

```yaml
id: R-800-063
version: 1
status: draft
category: functional
```

A tenant administrator SHALL be able to query current consumption vs budget via a dedicated admin endpoint (`GET /admin/v1/budgets?tenant_id=...`). This endpoint SHALL be exposed only to users with the `admin` or `tenant_admin` role (per E-100-002).

**Rationale.** Visibility into consumption is mandatory for operational control. Scoping to privileged roles prevents information leakage.

---

### 4.8 Cost tracking

#### R-800-070

```yaml
id: R-800-070
version: 1
status: draft
category: functional
```

Every LLM request processed by C8 SHALL be recorded in a dedicated ArangoDB collection (`llm_calls`) with the following fields:

- `call_id` (UUID) — primary key.
- `timestamp_start`, `timestamp_end` (ISO-8601 with millisecond precision).
- `provider`, `model` (resolved values).
- `input_tokens`, `output_tokens`, `cached_tokens` (integer).
- `cost_usd` (float) — computed from model metadata × token counts.
- `latency_ms` (integer).
- `status` (success | failure | timeout | rate_limited | budget_exceeded).
- `error_code`, `error_message` (optional, on failure).
- Tags from request headers: `tenant_id`, `project_id`, `user_id`, `session_id`, `agent_name`, `phase`, `sub_agent_id`.
- `request_fingerprint` (hash for deduplication).

**Rationale.** Per Q-800-ε: all levels of granularity supported via tags propagated from request headers. Post-hoc aggregation over this table serves cost dashboards, budget evaluation, audit, and the eval harness.

#### R-800-071

```yaml
id: R-800-071
version: 1
status: draft
category: functional
```

Cost computation SHALL use the `cost_per_million_input`, `cost_per_million_output`, and (if the provider supports caching) a discounted cached-token rate declared in model metadata. The computation formula SHALL be documented in the C8 operator documentation and be deterministic.

**Rationale.** Accurate cost tracking requires explicit, versioned formulae. Opaque computation breaks audit.

#### R-800-072

```yaml
id: R-800-072
version: 1
status: draft
category: nfr
```

The `llm_calls` collection SHALL be retained for at least 90 days for operational analysis and audit purposes. Longer retention MAY be configured per tenant for regulatory needs.

**Rationale.** Aligned with R-100-107 (cost tracking retention). 90 days is the baseline window for billing disputes and operational post-mortems.

#### R-800-073

```yaml
id: R-800-073
version: 1
status: draft
category: functional
```

C8 SHALL expose aggregation endpoints for cost queries:

- `GET /admin/v1/costs/summary?tenant_id=&project_id=&from=&to=`
- `GET /admin/v1/costs/by_agent?...`
- `GET /admin/v1/costs/by_session?...`

Authorisation follows R-800-063. Response schemas are defined in E-800-002.

**Rationale.** Cost visibility needs pre-built aggregations for dashboards and alerts.

---

### 4.9 Fallback behaviour

#### R-800-080

```yaml
id: R-800-080
version: 1
status: draft
category: functional
```

When the resolved primary provider returns a transient error (HTTP 5xx, network error, timeout), C8 SHALL retry up to 2 times with exponential backoff (base 500 ms, max 4 s) on the same model before failing.

**Rationale.** Transient provider errors are common; automatic retries avoid unnecessary user-facing failures for ephemeral problems.

#### R-800-081

```yaml
id: R-800-081
version: 1
status: draft
category: functional
```

When the resolved primary provider returns a non-transient error (HTTP 4xx except 429, authentication, invalid request), C8 SHALL NOT retry. The error SHALL be translated to an OpenAI-compatible error response and returned to the caller.

**Rationale.** Non-transient errors don't benefit from retries; retrying amplifies cost and delays error surfacing.

#### R-800-082

```yaml
id: R-800-082
version: 1
status: draft
category: functional
```

Cross-provider fallback (using a different provider when the primary fails) is **out of scope for v1** (per D-011: level 2 feature). When v1's primary provider is unreachable after retries, C8 SHALL return HTTP 503 to the caller. The platform's conversational path SHALL surface a clear error message per R-100-071.

**Rationale.** Cross-provider fallback requires prompt portability validation, cost arbitration, and feature-parity handling. Deferred to v2 with the eval harness.

#### R-800-083

```yaml
id: R-800-083
version: 1
status: draft
category: functional
```

C8 SHALL implement a per-model **circuit breaker** (per R-100-007). When 5 consecutive calls to a model fail within 30 s, the circuit opens and all subsequent calls to that model SHALL fail-fast with HTTP 503 until a half-open probe succeeds (default 60 s later).

**Rationale.** Prevents thundering-herd retries against a degraded provider. Standard resilience pattern.

---

### 4.10 Eval hooks (v1 preparation for v2 eval harness)

#### R-800-090

```yaml
id: R-800-090
version: 1
status: draft
category: functional
```

C8 SHALL support an optional **request/response archival mode**, disabled by default. When enabled via configuration, C8 SHALL persist the complete request payload (messages, tools, parameters) and response payload (choices, usage, finish_reason) to MinIO under `llm-archive/<call_id>.json`.

**Rationale.** Per Q-800-θ decision: hooks for the v2 eval harness must be present in v1 to avoid refactor. Archived payloads enable replay against alternative models post-hoc.

#### R-800-091

```yaml
id: R-800-091
version: 1
status: draft
category: security
```

Archival mode SHALL be controlled by configuration per tenant and per project. Default value is `disabled`. Enabling archival SHALL display a persistent warning in the UI indicating that prompt content is being archived for evaluation purposes.

**Rationale.** Archived prompts may contain sensitive user content, business data, or PII. Explicit opt-in and visible notification are required.

#### R-800-092

```yaml
id: R-800-092
version: 1
status: draft
category: security
```

When archival is enabled, archived payloads SHALL be encrypted at rest using server-side encryption with customer-managed keys where available (MinIO SSE-KMS in production; SSE-C or unencrypted in local development).

**Rationale.** Sensitive content demands at-rest encryption. Local development relaxes the constraint for practical reasons.

#### R-800-093

```yaml
id: R-800-093
version: 1
status: draft
category: functional
```

Archived payloads SHALL carry the same tags (tenant, project, agent, phase, etc.) as the `llm_calls` record. A join on `call_id` between `llm_calls` and the archive file path SHALL suffice to assemble the full evaluation dataset.

**Rationale.** Evaluation requires correlating metrics (cost, latency) with content (prompt, response). Shared `call_id` enables the join.

#### R-800-094

```yaml
id: R-800-094
version: 1
status: draft
category: functional
```

Archival mode SHALL be togglable at runtime via an admin endpoint without requiring pod restart. Toggling off SHALL not delete previously archived data; retention policies govern cleanup separately.

**Rationale.** Operators may need to enable archival temporarily (during incident investigation, A/B evaluation, model migration) without disrupting traffic.

---

## 5. Non-Functional Requirements

### 5.1 Performance

#### R-800-100

```yaml
id: R-800-100
version: 1
status: draft
category: nfr
```

C8 SHALL add no more than 30 ms of p95 latency overhead to a request beyond the provider's own response time, excluding network latency to the provider.

**Rationale.** C8 sits on every LLM call; overhead must be small relative to baseline LLM latency (typically 500 ms to several seconds).

#### R-800-101

```yaml
id: R-800-101
version: 1
status: draft
category: nfr
```

C8 SHALL support at least 100 concurrent streaming connections per replica on the baseline deployment footprint (R-100-106).

**Rationale.** Streaming connections are long-lived; concurrency ceiling dictates replica count for expected user counts.

#### R-800-102

```yaml
id: R-800-102
version: 1
status: draft
category: nfr
```

Configuration hot reload SHALL complete in under 5 seconds per replica and SHALL NOT drop in-flight requests.

**Rationale.** Configuration changes are routine (budget adjustment, model swap, key rotation). Slow or disruptive reloads discourage operational responsiveness.

### 5.2 Availability

#### R-800-110

```yaml
id: R-800-110
version: 1
status: draft
category: nfr
```

C8's target availability SHALL be 99.9% monthly, measured excluding upstream provider outages. Provider outages are counted against the degraded-mode SLO (per R-100-071 / R-800-082).

**Rationale.** C8 is on the critical path; high availability is expected. Provider outages are external and measured separately.

### 5.3 Observability

#### R-800-120

```yaml
id: R-800-120
version: 1
status: draft
category: nfr
```

C8 SHALL emit Prometheus metrics covering at minimum: request rate per model, latency percentiles per model, error rate per provider, token consumption per tag (tenant, project, agent), current budget consumption vs cap, circuit breaker state per model, configuration reload success/failure.

**Rationale.** LLM cost and latency are the top operational concerns; metrics must surface them along the dimensions operators query.

#### R-800-121

```yaml
id: R-800-121
version: 1
status: draft
category: nfr
```

Every LLM call SHALL be logged in structured JSON format (per R-100-104) with at minimum: call_id, agent_name, model, input/output token counts, latency, status, tenant_id, project_id, trace_id. The log record SHALL be distinct from the `llm_calls` ArangoDB record (logs are stdout/stderr for aggregation; the collection is queryable durable storage).

**Rationale.** Logs serve ops aggregation (ELK, Loki); the collection serves application queries. Both are needed, for different tools.

---

## 6. Interfaces & Contracts

### 6.1 External surface (to internal components)

See §4.1 through §4.5. The complete OpenAPI schema is defined in E-800-001.

### 6.2 Admin surface

Admin endpoints under `/admin/v1/` cover configuration reload, budget inspection, cost aggregations, and archival toggle. Authorization follows E-100-002.

### 6.3 NATS events

C8 SHALL publish the following events on NATS:

- `llm.call.completed` — after each successful call (carries call_id, tags, cost, latency).
- `llm.call.failed` — after each failed call (carries call_id, tags, error).
- `billing.alert.soft_cap_reached` — when a soft budget cap is hit.
- `billing.alert.hard_cap_reached` — when a hard budget cap is hit (first time in period).
- `llm.circuit.opened` / `llm.circuit.closed` — circuit breaker state changes.
- `llm.config.reloaded` — after successful configuration reload.

Payload schema follows the envelope defined in E-300-003 (reused).

### 6.4 Contract-critical entities

#### E-800-001: C8 REST API OpenAPI reference

```yaml
id: E-800-001
version: 1
status: draft
category: architecture
```

C8 exposes an OpenAI-compatible REST API. The authoritative schema lives in `api/openapi/llm-gateway-v1.yaml`. This entity references the OpenAPI document; details are not duplicated here.

**Constraints on the OpenAPI document.**
- `POST /v1/chat/completions`, `GET /v1/models`, `GET /v1/health` SHALL be declared and conformant with OpenAI's public specification.
- Admin endpoints under `/admin/v1/` SHALL be declared with explicit authorization requirements.
- Custom HTTP headers (R-800-013) SHALL be documented on `POST /v1/chat/completions`.
- Error responses SHALL include a non-OpenAI `_platform_code` field for platform-specific error classification (e.g. `BUDGET_HARD_CAP_EXCEEDED`).

#### E-800-002: `llm_calls` ArangoDB collection schema

```yaml
id: E-800-002
version: 1
status: draft
category: architecture
```

The `llm_calls` collection schema (owned by C8) is:

```json
{
  "_key": "<call_id>",
  "call_id": "<uuid>",
  "timestamp_start": "2025-11-05T14:23:01.123Z",
  "timestamp_end": "2025-11-05T14:23:03.456Z",
  "provider": "anthropic",
  "model": "claude-opus-4-7",
  "input_tokens": 12500,
  "output_tokens": 850,
  "cached_tokens": 10000,
  "cost_usd": 0.0345,
  "latency_ms": 2333,
  "status": "success",
  "error_code": null,
  "error_message": null,
  "tags": {
    "tenant_id": "<tenant-id>",
    "project_id": "<project-id>",
    "user_id": "<user-id>",
    "session_id": "<session-id>",
    "agent_name": "architect",
    "phase": "spec",
    "sub_agent_id": null
  },
  "request_fingerprint": "sha256:...",
  "archive_path": "llm-archive/<call_id>.json",
  "trace_id": "<W3C trace-id>"
}
```

Indexes:
- Persistent on `(tags.tenant_id, tags.project_id, timestamp_start)` for budget queries.
- Persistent on `(tags.session_id, timestamp_start)` for session cost aggregation.
- Hash on `request_fingerprint` for deduplication.
- TTL index on `timestamp_start` for retention (90 days default, tenant-configurable).

#### E-800-003: Agent-to-feature catalog reference

```yaml
id: E-800-003
version: 1
status: draft
category: architecture
```

The normative agent-to-feature catalog lives in §4.6 of this document. It SHALL be projected into the `litellm-config.yaml` as the `agent_routes:` section, one entry per agent row. The C8 configuration validator SHALL verify consistency between the catalog (this entity) and the configuration at deploy time.

A sample projection appears in Appendix 8.1.

---

## 7. Open Questions

| ID | Question | Owning decision | Target resolution |
|---|---|---|---|
| Q-800-001 | Should C8 support tool-calling result caching (beyond prompt caching)? Some providers offer result caching for identical fingerprints. | D-011 | v2 (feature-dependent) |
| Q-800-002 | Extended thinking mode exposure: how are reasoning tokens surfaced to callers when the provider supports them? Via `_provider_extensions`? | — | v1 (implementation detail, likely via extensions envelope) |
| Q-800-003 | Streaming heartbeat: should C8 inject keep-alive comments in long SSE streams to prevent intermediate timeouts? | — | v1 (baseline: yes, every 15 s) |
| Q-800-004 | Per-provider prompt adaptation for v2 (translating a prompt optimised for Claude to one optimised for GPT). Where does this logic live? C8? Per-agent? | D-011 | v2 |
| Q-800-005 | Eval harness workflow: automated nightly eval across configured providers for a golden dataset, or manual trigger? Storage of comparison results? | D-011 | v2 |
| Q-800-006 | Archival encryption key management: shared KMS key, per-tenant key, per-project key? | — | v1 (baseline: shared platform KMS key; per-tenant deferred) |
| Q-800-007 | Budget window semantics: rolling 30-day, calendar month, or user-configurable? | — | v1 (baseline: calendar month UTC) |
| Q-800-008 | Quota reset on tenant upgrade (paid tier): immediate reset or period-end transition? | — | v2 (billing concern) |
| Q-800-009 | Local Ollama integration: does C8 need special handling for local models (no cost tracking, no rate limit)? | — | v1 (baseline: Ollama treated as a provider with `cost_per_million_*` = 0 and no upstream rate limits) |
| Q-800-010 | v2 ensemble mode: voting algorithm (majority, weighted, structured-output cross-check)? | D-011 | v3 |
| Q-800-011 | Proxy-side admission middleware for `X-Agent-Name`-based resolution — LiteLLM pre-request hook vs thin FastAPI shim in front of the proxy. v1 sidesteps this by doing the resolution in the client SDK (see R-800-030 v1 note). | D-011 | v2 |

---

## 8. Appendices

### 8.1 Sample LiteLLM configuration (illustrative, not normative)

```yaml
# litellm-config.yaml
model_list:
  - model_name: claude-opus-flagship
    litellm_params:
      model: anthropic/claude-opus-4-7
      api_key: os.environ/ANTHROPIC_API_KEY
    model_info:
      display_name: "Claude Opus 4.7"
      features:
        - chat_completion
        - streaming
        - long_context
        - prompt_caching
        - extended_thinking
        - tool_calling
        - structured_outputs
        - vision
      context_window: 200000
      cost_per_million_input: 15.00
      cost_per_million_output: 75.00
      rate_limit_rpm: 4000
      rate_limit_tpm: 400000

  - model_name: claude-sonnet-midtier
    litellm_params:
      model: anthropic/claude-sonnet-4-6
      api_key: os.environ/ANTHROPIC_API_KEY
    model_info:
      display_name: "Claude Sonnet 4.6"
      features:
        - chat_completion
        - streaming
        - long_context
        - prompt_caching
        - tool_calling
        - structured_outputs
      context_window: 200000
      cost_per_million_input: 3.00
      cost_per_million_output: 15.00

  - model_name: claude-haiku-fast
    litellm_params:
      model: anthropic/claude-haiku-4-5-20251001
      api_key: os.environ/ANTHROPIC_API_KEY
    model_info:
      display_name: "Claude Haiku 4.5"
      features:
        - chat_completion
        - streaming
        - tool_calling
      context_window: 200000
      cost_per_million_input: 0.80
      cost_per_million_output: 4.00

agent_routes:
  architect: claude-opus-flagship
  planner: claude-sonnet-midtier
  implementer: claude-sonnet-midtier
  spec-reviewer: claude-sonnet-midtier
  quality-reviewer: claude-sonnet-midtier
  sub-agent: claude-haiku-fast
  # AyExtractor (C13) agents — D-020 v2 (Phase 1 + Phase 2 with optimisations).
  ayextract.image_analyzer: claude-sonnet-midtier            # vision-capable, deduplicated by sha256
  ayextract.decontextualizer_screener: claude-haiku-fast     # 2-tier gating, ~50 tokens
  ayextract.decontextualizer: claude-sonnet-midtier          # gated by screener, prompt_caching marker
  ayextract.summarizer: claude-sonnet-midtier                # Refine, prompt_caching marker mandatory
  ayextract.densifier: claude-sonnet-midtier                 # Chain of Density, long_context
  default: claude-sonnet-midtier

budgets:
  default_hard_cap_usd_per_month: 100.0
  default_soft_cap_ratio: 0.8
  window: calendar_month_utc

archival:
  enabled: false
  minio_bucket: llm-archive
  encryption: sse-kms

rate_limits:
  per_tenant_rpm: 1000
  per_user_rpm: 100
```

### 8.2 Cost computation formula (normative)

For a given call with input tokens `I`, output tokens `O`, cached tokens `C` (subset of `I`), and model metadata `(cost_in, cost_out, cost_cached)`:

```
cost_usd = (
  (I - C) * cost_in        / 1_000_000
  + C     * cost_cached    / 1_000_000
  + O     * cost_out       / 1_000_000
)
```

Where `cost_cached` defaults to `cost_in * 0.1` if not specified (Anthropic and OpenAI both charge cached tokens at approximately 10% of the standard rate as of the baseline). If a provider charges differently, the `cost_cached` field SHALL be set explicitly in model metadata.

---

**End of 800-SPEC-LLM-ABSTRACTION.md v4 (D-020 v2: prompt_caching markers normative, screener agent R-800-134).**
