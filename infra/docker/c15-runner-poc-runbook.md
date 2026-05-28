<!--
File: c15-runner-poc-runbook.md
Version: 2
Path: infra/docker/c15-runner-poc-runbook.md
Description: Operator runbook for the OpenHands `generate`-phase Q13 POC
             (V2 #2 / R-200-029). Covers building the C15 runner image and
             executing Phase 1 / Phase 2 of the POC defined in
             requirements/references/aywiz-architecture-synthesis-v4.md §8 (Q13).
             This file documents OPERATOR steps that cannot run in the Claude
             Code sandbox (docker build, K8s, provider egress).
-->

# C15 Runner — OpenHands `generate` POC Runbook (Q13)

Scope: validate that the OpenHands V1 SDK, routed through C8/LiteLLM to the
active provider, can drive the `generate` phase. The adapter lives behind the
gated seam `pipeline/generate_engine.py` (`OpenHandsGenerateEngine`,
R-200-029); the heavy `openhands` extra is installed **only** in this image.

Provider for this POC: **Anthropic direct** (reuses `ANTHROPIC_API_KEY`; the
synthesis reference is Databricks — swappable later, no code change).

## 0. Prerequisites

- C8/LiteLLM proxy + cost receiver running (V2 #1). `ANTHROPIC_API_KEY` and
  `C8_GATEWAY_API_KEY` set in `.env.secret` (Tier-2, operator-authored).
- The C8 `model_list` exposes `claude-opus-flagship` (→ `anthropic/claude-opus-4-7`),
  see `infra/c8_gateway/config/litellm-config.yaml`.

## 1. Run via docker-compose (recommended — before any k8s)

The dev stack ships an opt-in `c4_openhands` service (profile `openhands`)
that builds the C15-runner image and runs C4 with the engine flag on. From
`ay_platform_core/tests/`:

```bash
docker compose --profile openhands up c4_openhands
```

This builds `ay-c15-runner:local` from `infra/docker/Dockerfile.c15-runner`
(the `ay-api` image **plus** `ay_platform_core[openhands]` = openhands-sdk +
openhands-tools, and `tmux` + `git`) and starts C4 with
`COMPONENT_MODULE=c4_orchestrator` + `C4_GENERATE_ENGINE=openhands`. The
shared `ay-api` image is unaffected. (Standalone build, if needed:
`docker build -f infra/docker/Dockerfile.c15-runner -t ay-c15-runner:local .`
from the monorepo root.)

## 2. Engine configuration (env)

The relevant settings (defaults in `.env.example`) :

```
C4_GENERATE_ENGINE=openhands
C4_OPENHANDS_MODEL=litellm_proxy/claude-opus-flagship   # a C8 model_list name
C4_OPENHANDS_MAX_ITERATIONS=50
C4_LLM_GATEWAY_URL=<C8 OpenAI-compatible base, e.g. http://c8:8000/v1>
C8_GATEWAY_API_KEY=<the shared gateway key>
```

OpenHands' LiteLLM client is configured by the adapter with
`model="litellm_proxy/..."` + `base_url` = the C8 gateway + `api_key` = the
shared key, so **all** LLM egress goes through C8 (R-200-029). Per-turn
attribution headers (`X-Run-Id`, `X-Sub-Agent-Id`, `X-Agent-Name`,
`X-Phase=generate`) are forwarded for `llm_calls` aggregation (R-200-035).

## 3. Phase 1 — viability (pass criteria from Q13)

Run 3 trivial `generate` tasks (e.g. "read a small spec, write a Python
module + a pytest, run the test"). Verify:

- [ ] End-to-end success on the 3 sample tasks (OpenHands `FINISHED`; the
      adapter returns `DONE` with `output.files`).
- [ ] Cache-hit ratio > 0% on the 2nd run of the same task — query the C8
      `llm_calls` collection (`cache_read_tokens` / `cache_creation_tokens`).
- [ ] OTel traces visible end-to-end.

## 4. Phase 2 — discipline (outline)

- In-pod git commit hook after file mutations (R-200-036 prototype).
- Working-data MCP server (`code.symbol_search`, `tests.last_status`) — R-200-037.
- 10-task sample, completion ≥ 70%; proxy latency < 200ms p50 / < 500ms p95;
  cost rows include cache fields + per-agent attribution.

## 5. KNOWN finding — Gate B (Q1, decision input, NOT a defect)

The adapter returns `DONE` + `output.files` but **does not** emit
`gate_b_evidence` (it does not fabricate TDD red-first proof it has not
verified). The code-domain Gate B
(`domains/code/plugin.py::evaluate_gate_b`) therefore **blocks** such a
completion, and files are not materialised (materialisation is post-Gate-B).

This is the unresolved **Q1** (single-shot OpenHands run vs. gated sub-steps).
Capture the behaviour during the POC; the resolution (and the R-200-029..038
spec amendment) follows POC success per synthesis §8 ("POC before spec
amendment").
