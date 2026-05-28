---
document: 200-SPEC-PIPELINE-AGENT
version: 3
path: requirements/200-SPEC-PIPELINE-AGENT.md
language: en
status: draft
derives-from: [D-007, D-008, D-011, D-012, D-015]
---

# Pipeline & Agent Specification

> **STATUS: draft v3 — Tranche B expansion (2026-05-20).** Adds §4.10 (live tracing + operator steering of running pipelines), §5.17 (live-docs operator-driven CRUD beyond the LLM tool-loop), §5.18 (source-files tree projection + operator-driven dir/file rename/move), §5.19 (prompt-attached references — files and text excerpts inlined into LLM context). New contract entities E-200-006..008 in §6.3, new REST endpoints in §6.1, new questions Q-200-013..016 in §7. Earlier baseline (v2) unchanged.
>
> Derives from D-007 (staff-engineer pattern), D-008 (hybrid agent exposure), D-011 (multi-LLM via LiteLLM), D-012 (domain extensibility), D-015 (DocGen v1 chat-direct, extended in §5.17). Open questions in §7 MUST be resolved before C4 is considered production-ready.

---

## 1. Purpose & Scope

This document specifies the **Orchestrator (C4)** and the contract between the orchestrator and the agents it coordinates:

- The five-phase pipeline (`brainstorm → spec → plan → generate → dual review`) per D-007.
- The three hard gates enforcing discipline between phases.
- Sub-agent dispatch as ephemeral Kubernetes pods with isolated context.
- Agent-to-LLM contracts: which agents run in which phase, what LLM features they require (cross-referenced to 800-SPEC §4.6).
- The hybrid agent exposure model: invisible by default, expert mode togglable, events published on NATS for C3/UI consumption.
- The domain plug-in contract so that v2+ `documentation` and `presentation` domains register without touching the orchestrator core.
- Escalation model: four statuses, three-fix rule, architectural-review request on halt.

**Out of scope.**
- Per-agent prompt engineering (operational).
- LLM gateway routing details (→ `800-SPEC-LLM-ABSTRACTION.md`).
- Memory/RAG read path (→ `400-SPEC-MEMORY-RAG.md`).
- Per-domain validation check specifics (→ `600-SPEC-CODE-QUALITY.md` for `code` domain).
- UI rendering of expert mode (→ `500-SPEC-UI-UX.md`, which consumes the NATS events specified here).

---

## 2. Glossary

| Term | Definition |
|---|---|
| **Pipeline run** | A single end-to-end execution of the five phases, scoped to one project + one conversation session. Addressable by `run_id`. |
| **Phase** | One of: `brainstorm`, `spec`, `plan`, `generate`, `review`. Phase `review` is the dual review (spec compliance + artifact quality). |
| **Agent** | A named role executing one or more phases (`architect`, `planner`, `implementer`, `spec-reviewer`, `quality-reviewer`, `sub-agent`). |
| **Sub-agent** | An ephemeral agent spawned by the orchestrator for a bounded task, running in an isolated pod with a restricted context. |
| **Hard gate** | A pre-condition enforced before advancing to the next phase. Failure blocks progression and surfaces an actionable error. |
| **Domain** | A production domain (`code`, `documentation`, `presentation`, …) providing its own generation and validation plugins. |
| **Escalation status** | One of `DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, `BLOCKED`. Returned by every agent completion. |
| **Three-fix rule** | After three consecutive failed fix attempts on the same artifact, halt the pipeline and request architectural review. |

---

## 3. Relationship to Synthesis Decisions

| Decision | How this document operationalises it |
|---|---|
| D-007 (staff-engineer pattern) | Defines the five-phase lifecycle, the three hard gates, the four escalation statuses, the three-fix rule, and the sub-agent dispatch model. |
| D-008 (hybrid exposure) | Defines the NATS event subjects the orchestrator publishes for C3/UI consumption. |
| D-011 (LiteLLM abstraction) | Declares the per-agent feature requirements referenced by 800-SPEC §4.6 and constrains the orchestrator to invoke C8 exclusively (no direct provider calls). |
| D-012 (domain extensibility) | Formulates every gate and agent contract in domain-agnostic terms; defines the plug-in registration mechanism. |

---

## 4. Functional Requirements

### 4.1 Pipeline lifecycle

#### R-200-001

```yaml
id: R-200-001
version: 1
status: draft
category: pipeline-design
```

The orchestrator SHALL execute pipeline runs across exactly five ordered phases: `brainstorm`, `spec`, `plan`, `generate`, `review`. Phases are traversed sequentially; skipping a phase is prohibited. A run MAY terminate before reaching `review` if a hard gate (§4.2) blocks progression or if an agent returns `BLOCKED`.

**Rationale.** The phase sequence encodes the discipline of D-007: no generation without design, no completion without verification. Skipping would dilute the gates.

#### R-200-002

```yaml
id: R-200-002
version: 1
status: draft
category: pipeline-design
```

Each pipeline run SHALL be identified by a unique `run_id` (UUID v4). The run is scoped to a single `(project_id, session_id)` pair. The orchestrator SHALL persist run state in ArangoDB (collection `c4_runs`, owned by C4 per R-100-012) to survive pod restarts. Concurrent runs within the same session SHALL be rejected with HTTP 409.

**Rationale.** One active run per session keeps the conversational UX coherent (user shouldn't see two parallel pipelines in a single chat). Persistence supports HPA and rolling updates per R-100-003.

#### R-200-003

```yaml
id: R-200-003
version: 1
status: draft
category: pipeline-design
```

The orchestrator SHALL advance phases only upon agent completion with a terminal status (`DONE` or `DONE_WITH_CONCERNS`). `NEEDS_CONTEXT` SHALL trigger a context-enrichment round (bounded by R-200-040). `BLOCKED` SHALL halt the run and surface an architectural-review request.

**Rationale.** The four escalation statuses form the only permitted transition signals out of a phase. Other outcomes (exceptions, timeouts) are mapped onto `BLOCKED` by the dispatcher.

### 4.2 Hard gates

#### R-200-010

```yaml
id: R-200-010
version: 1
status: draft
category: pipeline-design
```

**Gate A — Design before artifact.** The orchestrator SHALL NOT allow the `generate` phase to run unless the `plan` phase completed with status `DONE` or `DONE_WITH_CONCERNS` AND the produced plan was explicitly approved by the human user (invisible-mode: implicit approval via continuation; expert-mode: explicit toggle).

**Rationale.** Enforces D-007's first hard gate in domain-agnostic terms: "no artifact before design approval."

#### R-200-011

```yaml
id: R-200-011
version: 1
status: draft
category: pipeline-design
```

**Gate B — Validation artifact before production artifact.** The orchestrator SHALL NOT allow the `generate` phase to emit a production artifact before the associated validation artifact (the domain-specific form: failing test for `code`, unmet acceptance checklist for `documentation`, etc.) has been written AND demonstrated to fail as expected. Domain plug-ins (§4.7) define the concrete form of "validation artifact" and "fails as expected".

**Rationale.** D-007's second hard gate, generalised per D-012. For `code`, this is the TDD red phase.

#### R-200-012

```yaml
id: R-200-012
version: 1
status: draft
category: pipeline-design
```

**Gate C — Fresh verification before completion.** The orchestrator SHALL NOT mark a run as completed until the `review` phase has produced evidence of verification dated AFTER the last artifact modification. Cached or stale verification results are rejected.

**Rationale.** D-007's third hard gate: "no completion claim without fresh verification evidence." Prevents the "green-when-last-measured" anti-pattern.

#### R-200-013

```yaml
id: R-200-013
version: 1
status: draft
category: pipeline-design
```

Hard-gate formulations SHALL be domain-agnostic. Code-specific vocabulary (`test`, `function`, `class`, etc.) SHALL NOT appear in gate definitions. Domain plug-ins translate the abstract gates into concrete checks for their artifact class.

**Rationale.** D-012: backbone contracts are domain-agnostic.

### 4.3 Agents and responsibilities

#### R-200-020

```yaml
id: R-200-020
version: 1
status: draft
category: pipeline-design
```

The orchestrator SHALL recognise the following agent roles in v1:

| Agent | Active in phase(s) | Responsibility |
|---|---|---|
| `architect` | brainstorm, spec | Elicit intent, propose architecture, author requirements |
| `planner` | plan | Decompose into ordered, testable steps; declare gate-B validation artifacts |
| `implementer` | generate | Produce artifacts (code, docs, …) against the plan; dispatches sub-agents for bounded tasks |
| `spec-reviewer` | review | Verify artifact conforms to the approved spec |
| `quality-reviewer` | review | Verify artifact passes the domain's quality checks per 600-SPEC |
| `sub-agent` | any | Ephemeral ad-hoc helper with isolated context |

Each agent role maps to an entry in the `AGENT_CATALOG` (C8 `catalog.py`) that declares its required LLM features.

**Rationale.** Minimal-viable agent set per D-007. The dual-review split (spec compliance vs artifact quality) is preserved.

#### R-200-021

```yaml
id: R-200-021
version: 1
status: draft
category: pipeline-design
```

Agents SHALL invoke LLMs exclusively via the C8 LiteLLM gateway (R-800-011). Direct provider SDK imports (e.g. `anthropic`, `openai`) in agent code are prohibited by architectural policy. Every LLM call SHALL carry the mandatory headers declared in R-800-013 (`X-Agent-Name`, `X-Session-Id`), plus the orchestration-specific `X-Phase` header.

**Rationale.** Single egress point per D-011 for cost tracking, routing, and audit.

#### R-200-022

```yaml
id: R-200-022
version: 1
status: draft
category: pipeline-design
```

Every agent completion SHALL return one of the four escalation statuses. The status SHALL be accompanied by a structured payload matching E-200-002:

- `DONE` — terminal success, output is usable as-is.
- `DONE_WITH_CONCERNS` — terminal success with non-blocking caveats; the orchestrator records them and surfaces them to the user in the run summary.
- `NEEDS_CONTEXT` — agent lacked necessary information; specifies what is missing. Triggers a context enrichment round (R-200-040) without advancing the phase.
- `BLOCKED` — agent cannot proceed; specifies the blocker. Halts the run, requests architectural review.

**Rationale.** Four statuses per D-007 capture every outcome without unbounded error taxonomy.

### 4.4 Sub-agent dispatch

#### R-200-030

```yaml
id: R-200-030
version: 1
status: draft
category: pipeline-design
```

The orchestrator SHALL dispatch sub-agents as **ephemeral Kubernetes pods** (per R-100-040). Each sub-agent pod SHALL be scheduled fresh (no pod reuse), receive the minimum context necessary for its task via a MinIO-sourced context bundle, and terminate after returning its result.

**Rationale.** D-007 (staff-engineer pattern) isolates sub-agent work to prevent context pollution across tasks. Ephemeral pods enforce this boundary at the infrastructure level.

**v1 implementation note (2026-05-20).** Dispatcher selection is config-driven via `C4_DISPATCHER_BACKEND` :
- `in-process` (default, dev fallback) — agents run in the orchestrator process via `InProcessDispatcher`. Preserves existing dev velocity (docker-compose bind-mount loop) at the cost of the spec's isolation guarantee. Suitable for the backbone dev stack ONLY.
- `k8s` — `K8sDispatcher` schedules a Pod per dispatch in the `c4-workers` namespace (`infra/k8s/base/c4_workers/`) with the security context + NetworkPolicy + ServiceAccount the spec requires. Targets Docker Desktop K8s in dev, production K8s in prod.

The two paths land on the SAME `AgentCompletion` envelope (same sub-agent runtime ; same in-process parser shared via `dispatcher.in_process` helpers — P2.1.a). A future fully-K8s dev path (R-200-030 with no in-process fallback) is a deliberate decision deferred until tooling (Skaffold/Tilt) reduces the K8s iteration penalty.

#### R-200-031

```yaml
id: R-200-031
version: 1
status: draft
category: pipeline-design
```

Sub-agent pods SHALL run as non-root, with read-only root filesystem, a scratch `emptyDir` for work files, and egress network policy permitting only C8 (LLM gateway) and C10 (MinIO). No direct provider access, no internet egress.

**Rationale.** Sandboxing per R-100-041. The C8-only egress preserves the single-egress invariant (D-011).

#### R-200-032

```yaml
id: R-200-032
version: 1
status: draft
category: pipeline-design
```

Sub-agent pod lifecycle SHALL be bounded by a hard timeout (default 15 minutes, configurable per agent role). Pods exceeding the timeout SHALL be terminated via Kubernetes `activeDeadlineSeconds`; the orchestrator SHALL treat the timeout as a `BLOCKED` completion for three-fix-rule accounting (R-200-051).

**Rationale.** Prevents runaway sub-agent costs; consistent with rate-limiting/cost-cap defenses at C8.

#### R-200-033

```yaml
id: R-200-033
version: 1
status: draft
category: pipeline-design
```

Sub-agent context bundles SHALL be assembled by the orchestrator in MinIO at path `c4-dispatch/<run_id>/<sub_agent_id>/` and mounted read-only in the pod. Bundles SHALL contain: the relevant requirements excerpt, the plan step scoped to the sub-agent, any prior artifacts the sub-agent may read, and a manifest file (`manifest.json`) describing the task envelope.

**Rationale.** Explicit context manifest is auditable (required by D-007) and enables deterministic replay for debugging.

### 4.5 Context enrichment & retries

#### R-200-040

```yaml
id: R-200-040
version: 1
status: draft
category: pipeline-design
```

On `NEEDS_CONTEXT` completion, the orchestrator SHALL perform a **context enrichment round**: consult the Memory Service (C7, retrieval API defined in 400-SPEC), append the retrieved context to the agent's bundle, and retry the same phase. At most **three** enrichment rounds SHALL be performed per phase; exceeding this count SHALL promote the completion to `BLOCKED`.

**Rationale.** Bounded retries prevent thrash while still handling legitimate context gaps.

#### R-200-041

```yaml
id: R-200-041
version: 1
status: draft
category: pipeline-design
```

On `DONE_WITH_CONCERNS` completion, the orchestrator SHALL record the concerns on the run record (`c4_runs.concerns[]`) and advance. Concerns SHALL be surfaced in the run summary returned to the conversational UI, distinguished from `DONE` by a structured badge.

**Rationale.** Non-blocking concerns must remain visible without blocking the pipeline.

### 4.6 Escalation & three-fix rule

#### R-200-050

```yaml
id: R-200-050
version: 1
status: draft
category: pipeline-design
```

The orchestrator SHALL track the number of consecutive fix attempts on a given artifact (or sub-task) within a single phase. A "fix attempt" is any retry triggered by a gate failure or a reviewer escalation.

**Rationale.** Prerequisite for the three-fix rule (R-200-051).

#### R-200-051

```yaml
id: R-200-051
version: 1
status: draft
category: pipeline-design
```

**Three-fix rule.** After **three** consecutive failed fix attempts on the same artifact within the same phase, the orchestrator SHALL halt the run with status `BLOCKED`, record the fix history on `c4_runs`, and emit a NATS event `orchestrator.{run_id}.review.requested` for the human operator to intervene.

**Rationale.** Per D-007. Beyond three attempts, continued retries rarely resolve the underlying issue and cost more than the architectural review itself.

#### R-200-052

```yaml
id: R-200-052
version: 1
status: draft
category: pipeline-design
```

On `BLOCKED` halt, the orchestrator SHALL preserve all intermediate artifacts (in MinIO under `c4-runs/<run_id>/`) and the full fix history. The human operator MAY resume the run via a dedicated admin endpoint after editing the failing step or rejecting the run.

**Rationale.** Debuggability and auditability. Resumption avoids losing work already done on earlier phases.

### 4.7 Domain plug-in contract

#### R-200-060

```yaml
id: R-200-060
version: 1
status: draft
category: architecture
```

Production domains SHALL register with the orchestrator via a **domain descriptor** (E-200-003) declared in a YAML file mounted at C4 startup. The descriptor enumerates: the domain's artifact MIME types, its validation artifact type, the concrete check bundle to invoke for each hard gate, and the agent roles specific to the domain (if any beyond the v1 roster of R-200-020).

**Rationale.** Per D-012, backbone is domain-agnostic; domain behavior lives in pluggable descriptors.

#### R-200-061

```yaml
id: R-200-061
version: 2
status: draft
category: architecture
```

The v1 implementation SHALL ship with **two** registered domains, selected per deployment via the `C4_DOMAIN` env var (default `code`) :

1. `code` — original target. Descriptor maps :
   - `artifact_mime_types`: `text/x-python`, `text/x-typescript`, and the other languages declared in R-100-XXX.
   - `validation_artifact_type`: `pytest_test` for Python, `vitest_test` for TypeScript, etc.
   - `gate_b_check`: "validation artifact exists and runs red against the current tree".
   - `gate_c_check`: "validation artifact runs green against the current tree with a timestamp newer than the last production-artifact write".

2. `documentation` — markdown/spec docs target (P4.a, 2026-05-20). Descriptor maps :
   - `artifact_mime_types`: `text/markdown`, `text/x-rst`, `text/asciidoc`.
   - `validation_artifact_type`: `documentation_outline` (the table-of-contents shape the generated doc must fulfil).
   - `gate_b_check`: "outline artifact exists and lists the sections the operator's prompt asks for" (analogue of "test exists and fails first").
   - `gate_c_check`: "every outline section has a non-trivial body and the outline timestamp is older than the doc body's last write" (analogue of "test passes with fresh evidence").

Per-run domain selection (a single project hosting BOTH a `code` and a `documentation` run side-by-side) is deferred to v2 per Q-200-012 — v1 binds one plugin per C4 deployment instance.

**Rationale.** Two domains demonstrate the plug-in surface concretely (D-012 validation). Selection-by-deployment keeps OrchestratorService construction simple (one plugin instance, not a registry) ; runtime per-run dispatch is the v2 evolution Q-200-012 anticipates.

#### R-200-062

```yaml
id: R-200-062
version: 1
status: draft
category: architecture
```

Domain plug-in registration SHALL be **build-time only** in v1. Runtime registration (hot-plug of a new domain on a running C4) is deferred to v2 when a second domain lands.

**Rationale.** Simpler invariants; forces a conscious deployment cycle on domain changes.

### 4.8 NATS events (hybrid exposure)

#### R-200-070

```yaml
id: R-200-070
version: 1
status: draft
category: pipeline-design
```

The orchestrator SHALL publish events on NATS at the following subjects (hierarchical):

- `orchestrator.{run_id}.phase.started`
- `orchestrator.{run_id}.phase.completed`
- `orchestrator.{run_id}.agent.invoked`
- `orchestrator.{run_id}.agent.completed`
- `orchestrator.{run_id}.sub_agent.dispatched`
- `orchestrator.{run_id}.sub_agent.completed`
- `orchestrator.{run_id}.gate.passed`
- `orchestrator.{run_id}.gate.blocked`
- `orchestrator.{run_id}.review.requested`
- `orchestrator.{run_id}.run.completed`
- `orchestrator.{run_id}.run.blocked`

Payloads SHALL share the envelope defined in E-300-003 (reused). Event-specific payloads are defined in E-200-004.

**Rationale.** Per D-008: invisible-by-default UI can ignore these; expert-mode UI subscribes and renders them. C3 (conversation service) relays relevant events into the conversation thread. Delivery guarantee: at-least-once via NATS JetStream; consumers SHALL be idempotent on `event_id`.

#### R-200-071

```yaml
id: R-200-071
version: 1
status: draft
category: pipeline-design
```

Invisible mode SHALL NOT alter event publication — the events are published unconditionally, and UI-side filtering selects which to render. Changing modes at runtime SHALL NOT require orchestrator restart or re-subscription.

**Rationale.** Decouples agent logic from presentation (D-008 consequence).

### 4.9 Persistence & observability

#### R-200-080

```yaml
id: R-200-080
version: 1
status: draft
category: pipeline-design
```

Run state SHALL be persisted in the `c4_runs` ArangoDB collection (schema in E-200-001) on every phase transition, agent completion, and gate evaluation. On pod restart, in-flight runs SHALL be resumable from the last persisted checkpoint.

**Rationale.** Statelessness (R-100-003) and rolling updates.

#### R-200-081

```yaml
id: R-200-081
version: 1
status: draft
category: nfr
```

The orchestrator SHALL emit Prometheus metrics covering at minimum: runs started/completed/blocked per phase, phase duration percentiles, agent completion status distribution, sub-agent dispatch count and pod lifetime, three-fix-rule triggers, gate failure rate per gate.

**Rationale.** Visibility on operational pipeline health.

### 4.10 Live tracing & operator steering

> Adds a polling-friendly trace channel and a side-band steering input on running pipelines (Tranche B, 2026-05-20). Complements §4.8 NATS events (push, expert-mode) with a poll-friendly ledger consumable by the standard polling UI, and gives the operator a way to influence a running pipeline at phase boundaries without aborting it.

#### R-200-200

```yaml
id: R-200-200
version: 1
status: draft
category: functional
```

The orchestrator SHALL accumulate, for every run, an append-only `trace: list[TraceEvent]` ledger (entity E-200-006) persisted on the `c4_runs` document. The ledger SHALL record at minimum, in order: sub-agent dispatch start/end, gate evaluations (A/B/C), fix-attempt invocations and outcomes, phase boundary crossings, and steering applications (R-200-203). Each event SHALL carry an ISO-8601 timestamp, the current phase, a `kind` discriminator, a short human label, and optionally `duration_ms` / `ok` / `payload`.

**Rationale.** §4.8 NATS+SSE is push-only and expert-mode-scoped per D-008. A persisted ledger lets the standard polling UI render run progress incrementally without committing to a long-lived stream connection, AND survives reload regardless of NATS retention.

#### R-200-201

```yaml
id: R-200-201
version: 1
status: draft
category: functional
```

`GET /api/v1/orchestrator/runs/{run_id}` SHALL include in `RunPublic` the **200 most recent** TraceEvents on the run (sliding window). Older events SHALL be retrieved via `GET /api/v1/orchestrator/runs/{run_id}/trace?before=<iso_ts>&limit=<=200`, returning events strictly older than `before`, ordered newest-first, capped at 200. The full ledger SHALL remain queryable indefinitely in v1 — no server-side eviction. A future eviction policy (LRU/age-based) is tracked in Q-200-013.

**Rationale.** A 200-event default keeps `GET /runs/{id}` snappy for the polling loop while letting the UX scroll back lazily. The "no eviction in v1" stance is deliberate : audit takes precedence over storage cost ; eviction will be revisited when a real cost signal emerges.

#### R-200-202

```yaml
id: R-200-202
version: 1
status: draft
category: functional
```

The orchestrator SHALL expose `POST /api/v1/orchestrator/runs/{run_id}/steer` with body `RunSteer` (entity E-200-007) `{ message: str }`. The endpoint SHALL append the message to a per-run `pending_steer: list[str]` queue persisted on `c4_runs`. The endpoint SHALL return 200 with the updated `RunPublic`. It SHALL return 409 when `status != "running"`. RBAC SHALL match `/feedback` (project_owner / project_editor / admin per E-100-002 v2 — tenant_manager rejected on content endpoints).

**Rationale.** A side-band channel for live operator guidance ("focus the spec on REST not gRPC", "skip the README"). Separates steering from `/feedback` which is gate-bound (Gate A approval, interactive-phase user input) — `steer` is a hint accepted between phases, NOT a precondition to advancement.

#### R-200-203

```yaml
id: R-200-203
version: 1
status: draft
category: functional
```

The orchestrator SHALL consume the `pending_steer` queue **only at phase or sub-agent-tour boundaries**, never mid-LLM-call. At each consumption point, the orchestrator SHALL drain the queue, prepend the messages (chronological order) to the next agent's user prompt under a clearly delimited `<operator-steering>` block, clear the queue, and append a `TraceEvent { kind: "steer-applied", payload: { count: <N>, sample: <truncated first message> } }`. The previous mid-call LLM invocation SHALL NOT be interrupted, cancelled, or retried as a consequence of a steer arriving.

**Rationale.** "No interruption brutale en plein appel LLM" — preserves run determinism and avoids partial-output costs. Boundary consumption is the natural extension point that already exists in the dispatcher.

#### R-200-204

```yaml
id: R-200-204
version: 1
status: draft
category: functional
```

The contract registry (per §8.4 of CLAUDE.md) SHALL declare `TraceEvent`, `RunSteer`, and the extended `RunPublic` (with `trace`) so consumers (UI, third-party clients) import these models directly rather than redefining them. The auth matrix catalog (`tests/e2e/auth_matrix/_catalog.py` per §13 of CLAUDE.md) SHALL include one entry for `POST /runs/{id}/steer` and one for `GET /runs/{id}/trace`.

**Rationale.** Same coherence discipline applied to every other public C4 contract.

#### R-200-205

```yaml
id: R-200-205
version: 1
status: draft
category: nfr
```

`POST /steer` SHALL persist the queued message in ≤ 100 ms p95 (Arango single-document append). `GET /trace?before=...` SHALL return in ≤ 200 ms p95 for the 200-event window. The trace append on `R-200-200` events SHALL add ≤ 5 ms p95 to phase-transition overhead — counted inside the R-200-100 budget (200 ms p95 total).

**Rationale.** Polling at 2 s intervals (current UX cadence) makes trace fetch a hot path ; the latency budget must be small to keep the perceived cost of polling negligible.

---

## 5. Non-Functional Requirements

### 5.1 Performance

#### R-200-100

```yaml
id: R-200-100
version: 1
status: draft
category: nfr
```

Phase-transition overhead (orchestrator bookkeeping, persistence, event emission) SHALL add no more than 200 ms p95 per transition. LLM and sub-agent latency are counted separately against their respective budgets.

**Rationale.** The orchestrator is on the critical path of every phase; its own overhead must be small relative to the LLM calls it coordinates.

### 5.2 Concurrency

#### R-200-110

```yaml
id: R-200-110
version: 1
status: draft
category: nfr
```

A single orchestrator replica SHALL handle at least 50 concurrent pipeline runs on the baseline deployment footprint (R-100-106). Exceeding this count SHALL trigger HPA scale-up per R-100-050.

**Rationale.** Capacity baseline. A conversation turn may trigger a run, so per-replica throughput must match expected conversational load.

### 5.3 Auditability

#### R-200-120

```yaml
id: R-200-120
version: 1
status: draft
category: nfr
```

Every phase transition, agent completion, gate evaluation, and sub-agent dispatch SHALL be recorded in a structured audit log (stdout JSON) with: `run_id`, `project_id`, `tenant_id`, `user_id`, `phase`, `agent`, `status`, `event_type`, `timestamp`. Retention policy follows R-800-072 (≥ 90 days, tenant-configurable).

**Rationale.** Regulated contexts require the ability to reconstruct the exact sequence of agent decisions leading to an artifact.

---

### 5.13 Project artifacts surface

#### R-200-130

```yaml
id: R-200-130
version: 1
status: draft
category: functional
```

The orchestrator SHALL persist every artifact produced by a pipeline run under a deterministic MinIO path following the convention `orchestrator/c4-artifacts/{tenant_id}/{project_id}/{run_id}/{relative_path}`. `relative_path` SHALL be a POSIX-style forward-slash path relative to the run root, with no leading slash, no `..` segments, and no Windows-style backslashes.

**Rationale.** A stable, tenant- and project-scoped path lets the UX browse run outputs without knowing run internals, and lets the future Gitea mirror (R-200-140) replay each run as a commit using the same relative tree.

#### R-200-131

```yaml
id: R-200-131
version: 1
status: draft
category: functional
```

The orchestrator SHALL expose a read-only artifacts REST surface mounted under `/api/v1/projects/{project_id}/artifacts/*` :

```
GET /api/v1/projects/{pid}/artifacts/runs                  → list runs (id, started_at, completed_at, status, file_count, total_bytes)
GET /api/v1/projects/{pid}/artifacts/runs/{rid}/tree       → flat list of `ArtifactNode` (path, kind=file|dir, size_bytes, mime_type)
GET /api/v1/projects/{pid}/artifacts/runs/{rid}/blob?path  → file content (Content-Type detected, Content-Disposition: inline)
```

Authentication SHALL follow the platform forward-auth pattern (X-User-Id / X-Tenant-Id / X-User-Roles). RBAC SHALL accept any tenant member for reads ; `tenant_manager` SHALL be REJECTED (per E-100-002 v2, content-blind). The blob endpoint SHALL accept an optional `download=1` query parameter that flips the `Content-Disposition` to `attachment`.

**Rationale.** Three minimal endpoints cover both the in-app preview path (tree + inline blob) and the download path, without exposing MinIO directly to the operator. Profile-agnostic : the same surface serves `codegen` (source code) and `docgen` (rendered documents) projects.

#### R-200-132

```yaml
id: R-200-132
version: 1
status: draft
category: functional
```

The orchestrator SHALL maintain a per-run metadata document in ArangoDB collection `c4_artifact_runs`, keyed by `run_id`, containing at minimum : `project_id`, `tenant_id`, `started_at`, `completed_at`, `status` (`pending` | `running` | `completed` | `failed`), `file_count`, `total_bytes`. The `tree` and `blob` endpoints SHALL refuse a request when the run's `project_id` does not match the X-Tenant-Id-resolved project (404, no detail leak).

**Rationale.** Listing all blobs under a MinIO prefix is too slow for the UX (≥ hundreds of ms even on tens of files) ; the Arango index gives sub-10-ms list queries and isolates run lifecycle bookkeeping from blob storage.

#### R-200-133

```yaml
id: R-200-133
version: 1
status: draft
category: non-functional
```

The artifacts surface SHALL be transparent : the UX SHALL NOT expose any direct link to the MinIO console, the bucket name, or the storage backend identity. Every artifact access SHALL transit through the C4 REST surface so storage migration (e.g. to S3, GCS, the future Gitea pass) can happen without UX changes.

**Rationale.** Operators interact with artifacts through "Code source" / "Documents générés" sections — they SHALL never see "MinIO", "bucket", or any backend implementation detail. This decouples storage migration from UX delivery and protects against accidental credential leak.

---

### 5.14 Project versioning (Gitea integration)

#### R-200-140

```yaml
id: R-200-140
version: 1
status: draft
category: functional
```

The platform SHALL bundle a Gitea instance as the canonical Git backend for every project. The dev / system / production overlays SHALL ship a `gitea` container alongside the rest of the stack. Gitea SHALL NOT be exposed directly to operators — every interaction (commit list, file diff, mirror config) SHALL transit through platform-owned endpoints so the storage backend stays interchangeable (cf. R-200-133 transparency invariant).

**Rationale.** Bundling Gitea makes the platform self-sufficient — no external Git account required to onboard a new tenant. Hiding it behind our endpoints lets us swap Gitea for another Git backend (or migrate hosting) without UX rebuilds.

#### R-200-141

```yaml
id: R-200-141
version: 1
status: draft
category: functional
```

For each project, the platform SHALL provision in Gitea :
  - **One dedicated service-account user** named `svc-{tenant_id}-{project_id}`, with a non-revealing password and an OAuth2 access token scoped to that user.
  - **One private repository** owned by that service account, named `{tenant_id}/{project_id}` (path : `{owner}/{repo_name}`).

Provisioning SHALL happen synchronously during `POST /api/v1/projects` (C2). Failure SHALL roll back the project creation (no orphan project without a repo). The service-account credentials SHALL be stored in a NEW Arango collection `c2_project_secrets` keyed by `project_id`.

**Rationale.** One service account per project gives least-privilege isolation between tenants AND between projects within a tenant. Synchronous provisioning keeps the platform invariant simple : every project has a repo.

#### R-200-142

```yaml
id: R-200-142
version: 1
status: draft
category: functional
```

`ProjectPublic` SHALL expose a read-only field `git_repo_url` containing the HTTPS clone URL of the Gitea repository (e.g. `http://gitea:3000/{tenant}/{project}.git` in dev, the K8s Service URL in prod). The field SHALL be present once provisioning succeeded and absent (`null`) for projects created before this feature shipped (backwards compat).

**Rationale.** Operators may want to clone the repo from their own machine for offline review. Exposing the URL through `ProjectPublic` (not via a separate endpoint) keeps the UX simple — the project settings page just displays it as a copyable string.

#### R-200-143

```yaml
id: R-200-143
version: 1
status: draft
category: functional
```

On each artifact run completion (C4 pipeline OR the dev seeder), the platform SHALL push every file under the run's MinIO prefix to the project's Gitea repo as a single commit. Commit message SHALL follow the pattern `"run {run_id} — {label or 'untitled'}"`. The push SHALL use the per-project service-account credentials persisted in `c2_project_secrets`. The push is best-effort : a failure SHALL log a warning but SHALL NOT block the artifact persistence to MinIO (MinIO remains the immediate source of truth ; Gitea is the versioned mirror).

**Rationale.** MinIO stays the hot-path (microsecond reads for the UX tree). Gitea adds versioning + cloneability without slowing the pipeline. Decoupling avoids a Gitea outage breaking artifact production.

#### R-200-144

```yaml
id: R-200-144
version: 1
status: draft
category: functional
```

A project MAY declare an optional external Git mirror via two NEW fields on the project record : `git_mirror_url` (HTTPS clone URL of the remote, e.g. a GitHub repo) and `git_mirror_token` (deploy token with push scope). When set, the platform SHALL push every Gitea commit to the mirror as a best-effort follow-up (failure logged, no retry on the request hot-path ; out-of-band retry is allowed). The mirror SHALL be considered a downstream copy — Gitea remains the source of truth.

**Rationale.** Tenants who already host code in GitHub/GitLab can keep their workflow while benefiting from the platform's automated push. The bundled Gitea remains in place (R-200-140) so no setup is required for new tenants.

#### R-200-145

```yaml
id: R-200-145
version: 1
status: draft
category: non-functional
```

The Gitea integration SHALL remain transparent to operators : the UX SHALL NOT link to Gitea's own web UI. Commit history, file diff and any other Git-level view SHALL be served through platform endpoints proxying the Gitea API. The `git_repo_url` from R-200-142 is an exception — it is a Git clone URL the operator may use from their own machine via `git clone`, which is necessary by definition (the protocol).

**Rationale.** Same reasoning as R-200-133 : decouples backend migration from UX delivery, and keeps the operator inside one consistent UI. Q-100-020 tracks the credential-storage threat model for the per-project service-account tokens.

#### R-200-146

```yaml
id: R-200-146
version: 1
status: draft
category: functional
```

On `ArtifactRunStatus` flipping to `completed` (C4 pipeline real run OR dev seeder admin endpoint), the platform SHALL push every file under the run's MinIO prefix to the project's Gitea repo via the Gitea Contents API. Each file SHALL produce its own commit message of the shape `"run {run_id} — {relative_path}"`. The push uses the **Gitea root admin credentials** (NOT the per-project service account) so the operation does not depend on cross-component read of `c2_project_secrets` — the per-project service account remains usable for operator-side `git clone` from outside the cluster.

The push SHALL be best-effort : a Gitea failure SHALL log a WARNING (with run_id + path) but SHALL NOT roll back the MinIO write (MinIO remains the source of truth ; Gitea is the versioned mirror).

**Rationale.** Using the root admin keeps C4 free of cross-component database coupling. Per-file commits avoid the complexity of git-tree plumbing while still giving the operator a granular history. Best-effort ensures a Gitea outage never breaks artifact production.

#### R-200-147

```yaml
id: R-200-147
version: 1
status: draft
category: functional
```

The platform SHALL expose `GET /api/v1/projects/{project_id}/git/commits` as a read-only proxy over the project's Gitea repo, returning a list of `ArtifactCommit` (`sha`, `message`, `author_name`, `author_email`, `committed_at`). Pagination is page-based (`?page=N`, default 1, server-side cap at 50 per page). The endpoint SHALL be tenant-scoped (X-Tenant-Id guard ; mismatched tenant → 404). RBAC follows the artifacts surface : any tenant member ; `tenant_manager` rejected per E-100-002 v2.

**Rationale.** A simple paginated list covers the v1 UX need (browse commits in chronological order). Diff inspection and per-commit file content are deferred to future passes — not required for the initial "Versions" section UX.

### 5.15 Generate-phase artifact materialisation

#### R-200-150

```yaml
id: R-200-150
version: 1
status: draft
category: functional
```

The `generate` phase's agent completion envelope SHALL declare a `output.files` list when `status ∈ {DONE, DONE_WITH_CONCERNS}`. Each entry SHALL be a JSON object `{path: string, content: string}` where `path` follows the R-200-130 path convention (POSIX forward slashes, no `..`, no leading `/`, no backslashes) and `content` is the file's UTF-8 text body. Binary files are NOT supported by v1 of this surface — agents producing binary artifacts SHALL embed them via a deterministic conversion (e.g. base64) under a future requirement, not this one.

**Rationale.** The C4 orchestrator does not invent file shape — it relies on the agent's structured output. A flat `files[]` list is the simplest contract that materialises onto the existing artifacts surface (`ArtifactStorage.put_blob` + `ArtifactsService.create_run / put_file`) without intermediate transformation.

#### R-200-151

```yaml
id: R-200-151
version: 1
status: draft
category: functional
```

On the orchestrator's first successful `generate` completion within a run (Gate B passed per R-200-011), the orchestrator SHALL materialise every entry of `output.files` into the artifacts surface, reusing the orchestrator's `run_id` as the artifact-run id (same id ↔ same run, no second identifier to track) and `tenant_id` / `project_id` from the `c4_runs` row. Materialisation order is `ArtifactsService.create_run(status=RUNNING)` → one `put_file(...)` per entry → `mark_completed(status=COMPLETED)`. The `mark_completed` call SHALL fire the existing best-effort Gitea push (R-200-146) so every generated run lands in the project's Gitea repo without additional wiring.

**Rationale.** Reusing the orchestrator's `run_id` removes an entire class of cross-reference bugs (UX has one id, backend has one id, MinIO + Gitea both use that one id). Triggering materialisation only on the first successful generate avoids duplicating files when the pipeline retries gate failures (which would otherwise materialise N times under the three-fix rule).

#### R-200-152

```yaml
id: R-200-152
version: 1
status: draft
category: functional
```

When the orchestrator is wired without an `ArtifactsService` (legacy/test setups where the artifacts surface is not mounted) materialisation SHALL be silently skipped — the pipeline state machine MUST NOT depend on materialisation success. Failures inside `ArtifactsService.put_file` SHALL be logged as WARNINGs scoped to the run_id + path, and the orchestrator SHALL continue with the remaining files (best-effort, mirroring the Gitea push semantics of R-200-146). A subsequent `mark_completed` failure SHALL log a WARNING and SHALL NOT mark the run BLOCKED — MinIO partial state remains preferable to a phantom-blocked run.

**Rationale.** Decouples the pipeline's correctness from the storage backend's availability. Backbone components (state machine, gate evaluators, three-fix rule) keep their semantics regardless of artifact-surface health.

### 5.16 Chat-direct document API (DocGen v1 — D-015)

> These requirements implement the v1 DocGen path recorded in **D-015** (`999-SYNTHESIS.md`). The conversation's tool calls (Phase 2.C.2) mutate the project document corpus directly, bypassing the 5-phase pipeline. Migration to the synthesis-v4 pipeline path (OpenHands in C15) is a v2 concern with conditions stated in D-015.

#### R-200-153

```yaml
id: R-200-153
version: 1
status: draft
category: functional
```

The platform SHALL expose a document CRUD surface under `/api/v1/projects/{project_id}/documents` : `POST` (create/overwrite, 201), `PUT /{path}` (overwrite, 200), `GET` (list, 200), `GET /{path}` (read, 200), `DELETE /{path}` (delete, 204). The surface is tenant-scoped (X-Tenant-Id guard) and project-scoped ; RBAC follows the artifacts surface — any tenant member, `tenant_manager` rejected (E-100-002 v2). Paths follow the R-200-130 convention (POSIX relative, no `..`, no leading `/`, no backslashes) ; violations map to 400.

**Rationale.** A minimal REST surface the conversation tool layer drives. Reuses the existing MinIO + Gitea artifact backend (R-200-130..147) so v1 chat-direct and v2 pipeline paths share one mutation surface.

#### R-200-154

```yaml
id: R-200-154
version: 1
status: draft
category: functional
```

All documents for a project SHALL live under one perpetual artifact run with the deterministic id `live-docs`, created lazily on the first write with `status=running` and **never** transitioned to `completed`. The run's `file_count` / `total_bytes` SHALL be recomputed from the MinIO listing on every write and delete so the UX run summary stays accurate. The `live-docs` id SHALL be bound to exactly one `(tenant_id, project_id)` ; an attempt to bind it to a second project SHALL return 409.

**Rationale.** A single growing run models a living document corpus better than one-run-per-edit (which would fragment the tree across hundreds of runs). The perpetual-RUNNING status distinguishes it from pipeline runs that complete.

#### R-200-155

```yaml
id: R-200-155
version: 1
status: draft
category: functional
```

Each successful document write (`POST` / `PUT`) SHALL trigger an immediate best-effort single-file push to the project's Gitea repo (one commit per write, message `"docgen — {path}"`, root-admin owner per R-200-146). A Gitea failure SHALL log a WARNING and SHALL NOT fail the write — MinIO remains the source of truth. `DELETE` SHALL remove the object from MinIO but SHALL NOT rewrite Gitea history — the deleted document survives in the git log (audit trail ; the deletion is recorded by the absence of subsequent commits, not by a history rewrite).

**Rationale.** Incremental push gives the operator a per-edit Gitea history (vs the batch-on-complete push of R-200-146 for pipeline runs). Retaining deleted docs in git history is a deliberate audit choice consistent with R-200-145 transparency.

#### R-200-156

```yaml
id: R-200-156
version: 1
status: draft
category: functional
```

The document CRUD surface SHALL be profile-agnostic in code. The DocGen profile uses it via the C3 conversation tool layer (Phase 2.C.2) ; the v2 migration (D-015) reuses the same surface from the OpenHands runtime in C15. No profile-specific branching SHALL exist in the router or the service document methods.

**Rationale.** Keeps the v1→v2 migration a caller swap, not a surface rewrite (D-015 encapsulation property).

### 5.17 Live-docs operator-driven CRUD (mkdir / rename / move)

> Extends §5.16 with operator-direct REST endpoints for directory management and rename/move (Tranche B, 2026-05-20). These are operator-driven UX operations (right-click context menu on the live-docs tree), NOT LLM-tool-loop operations. Exposing them as tools to the chat is a separate decision tracked in Q-200-014.

#### R-200-160

```yaml
id: R-200-160
version: 1
status: draft
category: functional
```

The platform SHALL expose three additional endpoints under the live-docs surface : `POST /api/v1/projects/{project_id}/documents/mkdir` body `{ path: str }`, `POST .../documents/rename` body `{ from_path: str, to_path: str }`, and `POST .../documents/move` body `{ from_path: str, to_dir: str }`. RBAC SHALL match `R-200-153` (any project member, tenant_manager rejected). Paths SHALL follow the R-200-130 validation (POSIX relative, no `..`, no leading `/`, no backslashes) ; violations map to 400.

**Rationale.** Operator-driven file management without round-tripping through the LLM. The semantic split (`rename` = pure path change, `move` = relocate under a different directory) maps cleanly onto the UX right-click menu — even if both reduce to the same primitive operation server-side.

#### R-200-161

```yaml
id: R-200-161
version: 1
status: draft
category: functional
```

`mkdir` SHALL materialise an empty directory by writing a zero-byte `.keep` marker at `<path>/.keep` in MinIO. Listing endpoints SHALL filter `.keep` markers from the file count BUT SHALL infer the directory's existence from the marker. Attempting to `mkdir` an existing path SHALL return 409. `mkdir` SHALL trigger a Gitea push (one commit, message `"docgen — mkdir {path}"`) per R-200-155 best-effort semantics.

**Rationale.** MinIO is a flat object store ; empty directories require a placeholder. `.keep` is the standard POSIX convention. The Gitea commit keeps the tree consistent across both backends.

#### R-200-162

```yaml
id: R-200-162
version: 1
status: draft
category: functional
```

`rename` and `move` SHALL be atomic at the service-method level : the new path is written, then the old path deleted, in one method scope. A failure to write the new path SHALL leave the old path untouched (no partial state). A failure to delete the old path after a successful write SHALL leave both paths present, log a WARNING, and return 200 (caller sees the rename succeeded ; the orphan is cleaned by a subsequent retry or by the operator). For directories, the operation SHALL apply recursively to every blob under the directory prefix, in deterministic order. A single Gitea push (one commit, message `"docgen — rename {from} → {to}"` or `"docgen — move {from} → {to_dir}"`) SHALL be issued after MinIO success, per R-200-155 best-effort semantics.

**Rationale.** Atomicity-of-intent without distributed-transaction infrastructure. The "stale-orphan tolerated over partial-rename" rule mirrors R-200-156 of the artifact materialisation (best-effort downstream, MinIO is source of truth — see Q-200-015 for the alternative).

#### R-200-163

```yaml
id: R-200-163
version: 1
status: draft
category: functional
```

`rename` and `move` SHALL return 409 when `to_path` (or any descendant of a directory move) already exists. They SHALL return 404 when `from_path` does not exist. They SHALL return 400 when `from_path == to_path` or when the operation would create a path traversal cycle (`from = a/b`, `to = a/b/c` and similar). Self-overwrite is NEVER silent.

**Rationale.** Failure mode discipline : the UX must distinguish "missing source" from "destination conflict" to render the right confirmation dialogue.

#### R-200-164

```yaml
id: R-200-164
version: 1
status: draft
category: functional
```

The three operator endpoints SHALL emit one event each into the run-independent audit channel (per R-200-120 stdout JSON) AND, for any conversation currently bound to the project, an inline event `{ kind: "live-docs-op", op: "mkdir"|"rename"|"move", from_path?, to_path?, ok: bool, duration_ms }` MAY be appended to the active conversation's `MessagePublic.events` ledger only when the operation was triggered FROM the chat surface ; UX-only triggers (right-click in Working area) SHALL NOT pollute the chat ledger.

**Rationale.** Auditability without conversational noise : the stdout JSON is the persistent audit, the inline ledger is the UX echo and applies only to chat-initiated operations.

### 5.18 Project source-files tree projection & operator-driven CRUD

> A tree-shaped UX over the source-files artifact surface (R-200-130..147), independent of how MinIO actually stores them. Operator-driven mkdir / rename / move + a metadata-read endpoint (Tranche B, 2026-05-20). Like §5.17, these are NOT tools for the LLM — they're direct UX operations.

#### R-200-170

```yaml
id: R-200-170
version: 1
status: draft
category: functional
```

The platform SHALL expose `GET /api/v1/projects/{project_id}/source/tree` that returns a projection of the source-files MinIO content for the project, in a recursive tree of nodes `{ name: str, kind: "file"|"dir", path: str, size?: int, modified_at?: datetime, children?: [Node] }`. The projection is computed at request time from the MinIO listing — no separate persisted tree is maintained. RBAC SHALL match `R-200-130` (any project member). Pagination is NOT required in v1 (project source trees are bounded by the artifact run cap) ; if a single response exceeds 5 MB it SHALL be truncated with a top-level `truncated: true` marker and the operator notified in the UX.

**Rationale.** A tree is the UX-natural representation regardless of how MinIO stores the data (flat keys with `/`-separated prefixes). Computing it at request time keeps the model dead-simple ; a maintained-tree would duplicate state.

#### R-200-171

```yaml
id: R-200-171
version: 1
status: draft
category: functional
```

The platform SHALL expose `POST .../source/mkdir`, `POST .../source/rename`, `POST .../source/move` with the same body shapes, validation, and error codes as §5.17 R-200-160..163. Operations SHALL act on the project source-files surface (`orchestrator/c4-artifacts/{tenant}/{project}/`) and SHALL follow the same atomicity rules as R-200-162. RBAC : any project member with at least `project_editor`. `project_viewer` SHALL receive 403.

**Rationale.** Same UX needs as live-docs ; consistent semantics minimise cognitive load. The viewer-vs-editor gate is stricter than live-docs because source files materialise into Gitea history and feed downstream review.

#### R-200-172

```yaml
id: R-200-172
version: 1
status: draft
category: functional
```

Mutations under R-200-171 SHALL be reflected to Gitea as best-effort single-commit pushes (one commit per operation, messages `"source — mkdir {path}"`, `"source — rename {from} → {to}"`, `"source — move {from} → {to_dir}"`) under the project's root-admin owner per R-200-146. A Gitea failure SHALL log a WARNING and SHALL NOT fail the MinIO operation. MinIO remains the source of truth per Q-200-015 (option (i)).

**Rationale.** Mirrors R-200-155 incremental-push semantics so the operator gets a per-edit Gitea audit trail of structural changes, consistent with the existing per-file generate-phase push (R-200-146).

#### R-200-173

```yaml
id: R-200-173
version: 1
status: draft
category: functional
```

The platform SHALL expose `GET .../source/file/{path}/meta` returning `{ path, size, mime_type, modified_at, last_commit_sha?, last_commit_message?, last_commit_author?, kg_indexed?: bool }`. `last_commit_*` fields are best-effort and populated by querying Gitea for the path's most recent commit ; an unreachable Gitea SHALL omit them rather than fail the request. `kg_indexed` SHALL reflect whether the file appears in the C7 KG index for the project. `mime_type` SHALL be detected from the path extension (Python `mimetypes` fallback) — content sniffing is deferred (Q-200-016).

**Rationale.** Surfaces the metadata the operator typically wants when right-clicking a source file (size, when it changed, who changed it, is it in the search index) without exposing backend identifiers.

#### R-200-174

```yaml
id: R-200-174
version: 1
status: draft
category: functional
```

The auth matrix catalog (`tests/e2e/auth_matrix/_catalog.py` per §13 of CLAUDE.md) SHALL include one entry per new endpoint introduced by §5.17 and §5.18, declaring the accepted-role set, the excluded-global-roles set (always including `tenant_manager` on content endpoints), and the backend persistence flag (MinIO, optionally Gitea).

**Rationale.** Catalog is the SOT for the test matrix (§13) ; missing an entry is caught as a build break by the coherence test.

#### R-200-175

```yaml
id: R-200-175
version: 1
status: draft
category: functional
```

The platform SHALL expose `DELETE /api/v1/projects/{project_id}/source/file/{path}?run_id=<id>` for operator-driven removal of a single source file. RBAC follows R-200-171 (project_editor+). The endpoint SHALL return 204 on success, 404 when the path doesn't exist in the run, and 400 on a malformed path. The delete SHALL trigger a best-effort single-file Gitea push with message `"source — delete {path}"` (R-200-172 best-effort semantics) ; Gitea history is intentionally NOT rewritten — the deleted file survives in the git log (mirror of R-200-155 audit choice).

**Rationale.** Closes Q-200-019. Symmetric with the live-docs `DELETE` route (R-200-153) — operators expect the same surface across both tree views. Recursive directory delete is deliberately NOT in v1 : multi-blob atomicity introduces failure modes the v1 UX doesn't compensate for ; per-file deletes are sufficient for the operator action set (right-click → confirm → DELETE one file at a time).

### 5.19 Prompt-attached references (files + excerpts)

> A first-class mechanism to attach 1..N files and/or text excerpts to a chat turn so the LLM sees them as explicit context (Tranche B, 2026-05-20). Two attach modes : whole-file reference (right-click on tree) or text-range reference (select text in an open document, right-click). References apply uniformly to live-docs and source-files surfaces.

#### R-200-180

```yaml
id: R-200-180
version: 1
status: draft
category: functional
```

The C3 chat `send_message` request body SHALL accept an optional `references: list[PromptReference]` field (entity E-200-008). Each reference SHALL specify `kind: "file"|"excerpt"`, `source: "live-docs"|"source"`, `path: str`, and for excerpts a `range: { start_line: int, end_line: int }` (1-indexed inclusive). A request MAY carry up to 10 references ; exceeding the cap SHALL return 400.

**Rationale.** A side channel that keeps the user's `payload` (the natural prompt) clean and lets the system inline references deterministically. The 10-reference cap is a sanity floor against accidental dumps ; it can be relaxed later with usage data.

#### R-200-181

```yaml
id: R-200-181
version: 1
status: draft
category: functional
```

C3 SHALL resolve each `PromptReference` server-side by reading its content from the matching backend (live-docs or source-files) at request time, then inject it into the LLM context as a **separate system block** preceding the user message, formatted as `<reference path="{path}" kind="{kind}"{range_attr}>\n{content}\n</reference>`. References SHALL NOT be concatenated into the visible user `payload`. The combined inlined content SHALL NOT exceed **32 K tokens** (approximated via 4 chars/token heuristic in v1, refined when C8 surfaces real tokenizers) ; an over-cap request SHALL return 413 with a structured payload listing which references would have to be dropped or truncated.

**Rationale.** Separation keeps the audit trail of "what the user typed" intact and lets the LLM treat references as authoritative context rather than user text. The token cap protects the C8 budget per R-800-022 (cost discipline).

#### R-200-182

```yaml
id: R-200-182
version: 1
status: draft
category: functional
```

The persisted `MessagePublic` SHALL gain an optional `references: list[PromptReference]` field carrying the **metadata only** (path, kind, range) — NEVER the resolved content. The content is reconstructable on demand by re-reading the backend at the recorded `(path, range, timestamp)`. Resolution at audit time MAY return "content no longer available" when the file has been deleted ; the metadata reference SHALL remain visible in the message timeline regardless.

**Rationale.** Audit-friendliness without storage explosion. A long chat with frequent 30 K-token attachments would multiply storage cost ; storing pointers preserves traceability at constant cost.

#### R-200-183

```yaml
id: R-200-183
version: 1
status: draft
category: functional
```

Reference resolution SHALL enforce the same RBAC as direct access to the underlying resource : `source` references require `project_editor`+ (per R-200-171) ; `live-docs` references require any project member. A reference whose resolution would 403 SHALL fail the entire `send_message` request with 403 (no partial silent drop).

**Rationale.** Avoids privilege escalation by reference. Atomic failure surfaces the issue at compose time rather than letting the LLM see a stub or nothing.

#### R-200-184

```yaml
id: R-200-184
version: 1
status: draft
category: functional
```

The C3 contract `PromptReference` SHALL be registered via `register_contract(...)` per §8.4 of CLAUDE.md. UX consumers SHALL import the producer's Pydantic model directly ; the auth matrix catalog SHALL declare the extended `send_message` body shape via a single representative entry (existing `POST /api/v1/conversations/{cid}/messages` row, body-shape extension only).

**Rationale.** Same coherence discipline as every other public contract.

---

## 6. Interfaces & Contracts

### 6.1 REST API (overview)

The orchestrator SHALL expose a minimal REST surface for conversational clients (C3) and admin tooling:

```
POST   /api/v1/orchestrator/runs
  body: { project_id, session_id, initial_prompt }
  creates a run, returns run_id, status=brainstorm

GET    /api/v1/orchestrator/runs/{run_id}
  returns the run record (RunPublic — includes the 200 most recent TraceEvents per R-200-201)

POST   /api/v1/orchestrator/runs/{run_id}/feedback
  body: { phase, user_feedback }
  advances interactive phases (brainstorm/spec/plan) that await user input

POST   /api/v1/orchestrator/runs/{run_id}/resume   (admin-only)
  body: { strategy: "retry" | "skip-phase" | "abort" }
  used to unblock a halted run after architectural review

GET    /api/v1/orchestrator/runs/{run_id}/events?since=<ts>
  SSE-style stream of NATS events for the run (admin / expert-mode UI)

GET    /api/v1/orchestrator/runs/{run_id}/trace?before=<iso_ts>&limit=<=200    (R-200-201)
  paginated back-in-time read of the run's TraceEvent ledger, ordered newest-first

POST   /api/v1/orchestrator/runs/{run_id}/steer    (R-200-202..203)
  body: { message: str }
  queues a steering hint, consumed at the next phase / sub-agent-tour boundary

# Live-docs operator-driven CRUD — §5.17

POST   /api/v1/projects/{project_id}/documents/mkdir     (R-200-160, R-200-161)
  body: { path: str }

POST   /api/v1/projects/{project_id}/documents/rename    (R-200-160, R-200-162, R-200-163)
  body: { from_path: str, to_path: str }

POST   /api/v1/projects/{project_id}/documents/move      (R-200-160, R-200-162, R-200-163)
  body: { from_path: str, to_dir: str }

# Source-files tree projection & operator-driven CRUD — §5.18

GET    /api/v1/projects/{project_id}/source/tree           (R-200-170)
  returns the recursive tree of source-files nodes

POST   /api/v1/projects/{project_id}/source/mkdir          (R-200-171)
  body: { path: str }

POST   /api/v1/projects/{project_id}/source/rename         (R-200-171)
  body: { from_path: str, to_path: str }

POST   /api/v1/projects/{project_id}/source/move           (R-200-171)
  body: { from_path: str, to_dir: str }

GET    /api/v1/projects/{project_id}/source/file/{path}/meta   (R-200-173)
  returns metadata for one source file (size, modified, last commit, kg_indexed)

# C3 send-message body extension — §5.19 (the route itself is unchanged)

POST   /api/v1/conversations/{cid}/messages    (existing route, body extended per R-200-180)
  body: { ..., references?: list[PromptReference] }
```

Full OpenAPI schema lives in E-200-005.

### 6.2 NATS events

See R-200-070. Payload schema in E-200-004.

### 6.3 Contract-critical entities

#### E-200-001: `c4_runs` ArangoDB collection schema

```yaml
id: E-200-001
version: 2
status: draft
category: architecture
```

```json
{
  "_key": "<run_id>",
  "run_id": "<uuid>",
  "project_id": "<project-id>",
  "session_id": "<session-id>",
  "tenant_id": "<tenant-id>",
  "user_id": "<user-id>",
  "domain": "code",
  "current_phase": "generate",
  "status": "running | completed | blocked",
  "started_at": "2026-04-23T12:00:00Z",
  "completed_at": null,
  "concerns": [ { "phase": "plan", "message": "..." } ],
  "fix_attempts": { "<artifact_id>": 2 },
  "enrichment_rounds": { "plan": 1 },
  "events_emitted": 42,
  "minio_root": "c4-runs/<run_id>/",
  "trace": [ /* list[TraceEvent] — see E-200-006, R-200-200 ; append-only */ ],
  "pending_steer": [ /* list[str] — see R-200-202..203 ; FIFO, drained at phase boundaries */ ]
}
```

v2 (2026-05-20) adds `trace` and `pending_steer`. Indexes unchanged : `(project_id, session_id)`, `(status, started_at)`. `trace` SHALL NOT be indexed in v1 (the back-in-time read of R-200-201 sorts by event timestamp client-side from the embedded array ; an index becomes warranted if a run's trace grows past O(10⁴) events, which is well beyond v1 expectations).

#### E-200-002: agent completion envelope

```yaml
id: E-200-002
version: 1
status: draft
category: architecture
```

Every agent completion returns:

```json
{
  "agent": "planner",
  "run_id": "<uuid>",
  "phase": "plan",
  "status": "DONE | DONE_WITH_CONCERNS | NEEDS_CONTEXT | BLOCKED",
  "output": { /* phase-specific */ },
  "concerns": [ { "severity": "low|medium|high", "message": "..." } ],
  "needs_context": { "queries": ["..."] },
  "blocker": { "reason": "...", "suggested_action": "..." },
  "duration_ms": 12345,
  "llm_calls": [ /* references to C8 call_ids */ ]
}
```

Only one of `concerns`, `needs_context`, `blocker` is populated, matching the status.

#### E-200-003: domain descriptor

```yaml
id: E-200-003
version: 1
status: draft
category: architecture
```

Domain plug-ins register via a YAML descriptor:

```yaml
# domains/code/descriptor.yaml
domain: code
artifact_mime_types:
  - text/x-python
  - text/x-typescript
validation_artifact_type: pytest_test
gate_b:
  check: "validation_runs_red"
  implementation: "ay_platform_core.domains.code.checks:run_validation_red"
gate_c:
  check: "validation_runs_green_fresh"
  implementation: "ay_platform_core.domains.code.checks:run_validation_green_fresh"
agents:
  implementer:
    llm_features_additional: [tool_calling]
```

Loaded at C4 startup per R-200-062.

#### E-200-004: NATS event payloads

```yaml
id: E-200-004
version: 1
status: draft
category: architecture
```

Every event shares the envelope defined in E-300-003 (reused). `payload` varies per event:

- `phase.started` / `phase.completed`: `{ "phase": "plan", "agent": "planner", "status": "DONE" }`
- `agent.invoked` / `agent.completed`: the full agent envelope (E-200-002) minus LLM-call references.
- `sub_agent.dispatched`: `{ "sub_agent_id": "...", "task": "...", "pod_name": "..." }`
- `gate.passed` / `gate.blocked`: `{ "gate": "B", "artifact_id": "...", "reason": "..." }`
- `review.requested`: `{ "fix_attempts": 3, "artifact_id": "...", "history": [...] }`

#### E-200-005: orchestrator OpenAPI reference

```yaml
id: E-200-005
version: 1
status: draft
category: architecture
```

Canonical path: `api/openapi/orchestrator-v1.yaml`. Every endpoint in §6.1 SHALL be declared with request/response schemas, auth requirements (bearer JWT per E-100-001), and error examples.

#### E-200-006: `TraceEvent`

```yaml
id: E-200-006
version: 1
status: draft
category: architecture
```

```json
{
  "kind": "agent-dispatch | gate-eval | fix-attempt | phase-boundary | steer-applied",
  "ts": "2026-05-20T12:34:56.789Z",
  "phase": "brainstorm | spec | plan | generate | review",
  "label": "human-readable short description",
  "duration_ms": 1234,
  "ok": true,
  "payload": { /* kind-specific extra fields ; e.g. { sub_agent_id, gate, fix_round, count, sample } */ }
}
```

`duration_ms`, `ok`, and `payload` are optional. `kind` is the discriminator ; future kinds SHALL extend the union, never reuse an existing value. The C3 `InlineEvent` (registered contract for chat) and this `TraceEvent` SHALL stay structurally siblings — both kind-discriminated, both append-only — but they are distinct contracts (chat vs orchestrator) and SHALL NOT be conflated.

#### E-200-007: `RunSteer`

```yaml
id: E-200-007
version: 1
status: draft
category: architecture
```

```json
{
  "message": "string — operator hint, ≤ 4 KB"
}
```

A single message per request. The server appends it to `c4_runs.pending_steer` ; consumption is described in R-200-203. Multi-message batching is NOT supported — operators issue one steer per click.

#### E-200-008: `PromptReference`

```yaml
id: E-200-008
version: 1
status: draft
category: architecture
```

```json
{
  "kind": "file | excerpt",
  "source": "live-docs | source",
  "path": "POSIX relative path, no .. , no leading /",
  "range": { "start_line": 12, "end_line": 47 }
}
```

`range` SHALL be present iff `kind == "excerpt"` ; for `kind == "file"` it SHALL be absent. Line numbers are 1-indexed inclusive on both ends. The server resolves `(source, path, range?)` at `send_message` time and inlines the content per R-200-181. The persisted `MessagePublic.references` carries the metadata only (path, kind, range) per R-200-182.

---

## 7. Open Questions

| ID | Question | Owning decision | Target resolution |
|---|---|---|---|
| Q-200-001 | Sub-agent pod image: single baseline image with language-specific containers layered, or one image per domain? | D-007, D-012 | v1 (baseline: single image with domain plug-ins bundled; per-domain images if size exceeds 1 GB) |
| Q-200-002 | Where do sub-agents run: same K8s namespace as C4, or an isolated "workers" namespace? | D-007 | v1 (isolated namespace `c4-workers` for network-policy scoping) |
| Q-200-003 | Conversational feedback during interactive phases (brainstorm/spec/plan): inline SSE vs async NATS event? | D-008 | v1 (NATS event + C3 SSE relay — consistent with other runtime events) |
| Q-200-004 | "Plan approval" in invisible mode: how does the UI surface implicit approval? Via a confirm-to-continue prompt? | D-008 | v1 (UI concern; spec in 500-SPEC; C4 treats any `POST /feedback` with `approved=true` as approval) |
| Q-200-005 | Retention of MinIO run artifacts (`c4-runs/<run_id>/`): 30 days? tenant-configurable? | — | v1 (baseline: 30 days, tenant override for regulatory retention) |
| Q-200-006 | Sub-agent isolation: strict `emptyDir` + no shared cache, or shared read-only cache mount for common libraries? | D-007 | v1 (strict isolation; shared cache deferred until cold-start latency proves prohibitive) |
| Q-200-007 | Three-fix rule scope: per artifact or per phase? The spec reads "per artifact"; must clarify artifact granularity for multi-file generations. | D-007 | v1 (per "logical artifact" = unit of change addressable by the domain plug-in; concrete definition lives in the domain descriptor) |
| Q-200-008 | Dual review ordering (spec compliance first vs quality first)? Does one block the other? | D-007 | v1 (baseline: parallel both reviewers; merge concerns; either `BLOCKED` halts the run) |
| Q-200-009 | Resumption strategy `"skip-phase"` in admin endpoint: allowed only on non-gate phases? Allowed on `review`? | D-007 | v2 (admin override semantics need governance; defer until first production run) |
| Q-200-010 | Architectural-review request: human response channel? Email? Slack? In-platform comment? | D-007, D-008 | v2 (baseline: in-platform comment; external channels via webhook per tenant config) |
| Q-200-011 | Agent LLM call caching: does the orchestrator pre-populate `X-Cache-Hint: static` for architect/planner prompts? | D-011 | v1 (yes — architect/planner carry large stable system prompts that benefit from caching) |
| Q-200-012 | Cross-domain sub-agents: can a run mix `code` and `documentation` domain tasks within one pipeline? | D-012 | v2 (a single run binds to one domain in v1; cross-domain is a v2 concern when the second domain lands) |
| Q-200-013 | Trace ledger eviction policy: when does a run's `trace` array start truncating older events? Age-based (e.g. 90 days), size-based (e.g. 10⁴ events), or never? | — | v2 (no eviction in v1 per R-200-201 ; revisit once a real cost signal emerges from operating data) |
| Q-200-014 | Should live-docs `mkdir` / `rename` / `move` (§5.17) also be exposed as LLM-callable tools (option B.2 of the cadrage), letting the chat issue `"renomme draft.md en final.md"` directly? | D-015 | v2 (REST-only for v1 — UX trigger ; tools deferred until a real chat use-case appears) |
| Q-200-015 | Source-files rename/move (§5.18) backend ordering: MinIO first then Gitea best-effort (option (i), current choice) vs Gitea as source of truth then pull MinIO (option (ii)). Option (i) matches R-200-146 ; option (ii) would make the Gitea push the precondition. | D-015 | v1 (option (i) — MinIO source of truth, Gitea best-effort, consistent with R-200-146 and R-200-155) |
| Q-200-016 | Source-file metadata (§5.18 R-200-173): should `mime_type` be detected by content-sniffing (libmagic) in addition to extension-based, and should `last_commit_*` be cached or always live-fetched? | — | v2 (extension-only + live Gitea fetch in v1 ; sniffing and caching deferred behind a benchmark) |
| Q-200-017 | Source-files structural ops (§5.18) : project-wide aggregation across all artifact runs, OR scoped to one run_id at a time via `?run_id=` query param. v1 picks per-run scoping (current UI surfaces one run via the run picker) ; project-wide deferred. | — | v2 (per-run v1 ; aggregation v2 when the UX picks ONE canonical "live source" concept) |
| Q-200-018 | Source-file metadata (R-200-173) `kg_indexed` field : v1 emits null (no C7 query path from C4) ; v2 will cross-call C7's `/index/lookup?path=` once that endpoint exists. | — | v2 |
| Q-200-019 | Source-files DELETE endpoint (R-200-175 — DONE in P2.2.a, single-file delete with editor+ RBAC + Gitea push) ; source-side `PromptReference` (run_id ambiguity) STILL deferred — v1 UI rejects `source=source` references with 400. | — | source-DELETE DONE v1 ; source-ref v2 (paired with Q-200-017 resolution) |
| Q-200-020 | Sub-agent dispatcher selection (R-200-030 v1 note) : `C4_DISPATCHER_BACKEND` env (`in-process` vs `k8s`). v1 ships both ; full K8s dev devloop with Skaffold/Tilt deferred. | — | v2 |

---

## 8. Appendices

### 8.1 Phase transition state machine (informative)

```
                    ┌────────────┐
                    │ brainstorm │
                    └──────┬─────┘
                           │ DONE
                           ▼
                    ┌────────────┐
                    │    spec    │
                    └──────┬─────┘
                           │ DONE
                           ▼
                    ┌────────────┐       ┌───────────────────┐
                    │    plan    │──────►│ Gate A: approved? │
                    └──────┬─────┘       └──┬────────────────┘
                           │ approved       │ not approved → loop back to plan
                           ▼
                    ┌────────────┐       ┌─────────────────────────────┐
                    │  generate  │──────►│ Gate B: validation red OK?  │
                    └──────┬─────┘       └──┬──────────────────────────┘
                           │                │ not OK → BLOCKED
                           │ pass
                           ▼
                    ┌────────────┐       ┌─────────────────────────────┐
                    │   review   │──────►│ Gate C: validation fresh OK?│
                    └──────┬─────┘       └──┬──────────────────────────┘
                           │                │ not OK → BLOCKED
                           │ DONE
                           ▼
                      ┌────────┐
                      │ COMPLETED
                      └────────┘
```

### 8.2 Agent → LLM feature mapping (reference only — normative in 800-SPEC §4.6)

See `800-SPEC-LLM-ABSTRACTION.md` §4.6 for the authoritative agent → required features table. This section is informational and must not drift from that source of truth.

---

**End of 200-SPEC-PIPELINE-AGENT.md v3 (Tranche B expansion — live tracing & steering ; live-docs operator CRUD ; source-files tree projection & operator CRUD ; prompt-attached references).**
