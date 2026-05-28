<!-- =============================================================================
File: 2026-05-22-c8-litellm-proxy-and-cost-tracking.md
Version: 1
Path: .claude/sessions/2026-05-22-c8-litellm-proxy-and-cost-tracking.md
============================================================================= -->

# Session — 2026-05-22 — V2 #1 : C8 LiteLLM proxy + per-agent routing + cost tracking

## Context

First of the three V2 features (operator order : #1 LiteLLM → #2 OpenHands →
#3 Graphiti). V2 #1 also closes the V1 remainder item "LiteLLM deploy +
per-agent routing (Q-100-021)". Done in three stages, all verified.

## Work delivered (3 stages)

**Stage 1 — proxy + routing + key consolidation.**
- `litellm-config.yaml` v2 : `general_settings.master_key` + `litellm_settings.callbacks`
  (cost forwarder) added ; `LiteLLMConfig` v3 accepts those two proxy-native
  passthrough sections (root stays extra=forbid). agent_routes route the
  orchestrator roles + C3/C7 service agents to Claude tiers by quality/cost
  (Opus/Sonnet/Haiku).
- `infra/c8_gateway/callbacks/cost_forwarder.py` : standalone LiteLLM
  CustomLogger (litellm + httpx only, no ay_platform_core import) that POSTs a
  compact envelope to the cost receiver. Mounted into the off-the-shelf proxy
  (§4.5 — image unmodified).
- Dev compose : `litellm` service wired (config + forwarder mount + PYTHONPATH
  + COST_RECEIVER_URL).
- **Key consolidation** : `C3_C8_BEARER_TOKEN` / `SUB_AGENT_C8_BEARER_TOKEN` /
  `C4_K8S_SUB_AGENT_C8_BEARER_TOKEN` + the hardcoded `c4-orchestrator` bearer →
  ONE `C8_GATEWAY_API_KEY` (client Bearer + proxy master_key) via
  `ClientSettings.gateway_api_key` + `effective_bearer` ("no-auth" placeholder
  preserves R-800-012 enforcement + the mock/Ollama path). `ANTHROPIC_API_KEY`
  (provider) lives ONLY in the proxy. Ollama = local-dev-only, not a proxy
  route (operator clarification — see [[feedback-llm-routing-claude-not-ollama]]).
- `.env.dev` (v16), `.env.test`, `.env.example` updated ; env-completeness
  coherence green.

**Stage 2 — cost tracking (R-800-070).**
- `c8_llm/models.py` : `CostCallEnvelope` wire model.
- `cost_tracker.py` v2 : `build_call_record(envelope, catalog)` reusing
  `_extract_tags` / `_provider_of` / `compute_cost`.
- `cost_sink_arango.py` : `ArangoCallRecordSink` → `llm_calls` collection.
- `c8_llm/main.py` : the C8 cost receiver FastAPI app (`COMPONENT_MODULE=c8_llm`),
  `POST /internal/llm-calls`, `CostReceiverConfig` (shared ARANGO_* aliases +
  `C8_LITELLM_CONFIG_PATH` for the catalog). Compose service `c8-cost-receiver`.
- Tests : unit (build_call_record + _load_catalog) + integration (receiver →
  real Arango testcontainer).

**Stage 3 — K8s base manifests.**
- `infra/c8_gateway/scripts/gen_k8s_c8_configmaps.py` → generated
  `infra/k8s/base/c8_gateway/c8-configmaps.yaml` (proxy config + forwarder ;
  RootOnly-safe in-tree pattern, like c12).
- `c8_gateway/` : litellm Deployment+Service (off-the-shelf, pinned) + receiver
  Deployment+Service ; added to the base kustomization (overlays auto-inherit).
- Validated : `k8s_validate.sh base` → 55 docs OK. The full overlay build fails
  on the PRE-EXISTING `c4-workers`↔`aywizz` namespace conflict (out of scope).

## Decisions / settings

- `.claude/settings.json` v15 : removed the broad `Read(**/.env)` deny so
  nested CONFIG `.env` (k8s overlays) are readable/editable ; `.env.secret`,
  `.env.local/.prod/.production`, root `.env`, secrets/, *.pem, *.key stay
  denied (operator request).
- SESSION-STATE §3 entry added (supersedes the 2026-05-19 "Dev DocGen LLM" +
  "Tier-2 .env.secret" entries) ; Q-100-021 marked RESOLVED.

## Verification

- Backend `run_tests.sh ci` : ruff OK, mypy OK, **1545 passed, 2 skipped**
  (k8s/nats optional), coverage **87.06%**.
- `k8s_validate.sh base` : 55 docs, all valid (kubeval runs in CI).
- Runtime (proxy/receiver) NOT exercised here (no cluster, paid API) →
  operator validates via `e2e_stack.sh dev --profile litellm`.

## Operator follow-ups

- Create `.env.secret` (root + each overlay) with `C8_GATEWAY_API_KEY` +
  `ANTHROPIC_API_KEY` (templates `*.env.secret.example` provided).
- Resolve the pre-existing `c4-workers`↔`aywizz` namespace conflict before
  `kustomize build overlays/*`.

## Next

V2 #2 OpenHands `generate` harness — building the GATED skeleton
(`pipeline/generate_engine.py` + `C4_GENERATE_ENGINE` flag, default in_process
unchanged) ; real `openhands-ai` dep + Q13 POC + §8.1 spec amendment
(R-200-029..038) deferred to operator/runtime.

## Files touched

Backend src : `c8_llm/{config.py,client.py,main.py,models.py,cost_sink_arango.py,
callbacks/cost_tracker.py}`, `c3_conversation/main.py`,
`c4_orchestrator/{main.py,config.py,dispatcher/k8s.py}`,
`_sub_agent/{config.py,runtime.py}`.
Infra : `infra/c8_gateway/{config/litellm-config.yaml,callbacks/cost_forwarder.py,
scripts/gen_k8s_c8_configmaps.py}`, `infra/k8s/base/c8_gateway/*`,
`infra/k8s/base/kustomization.yaml`, dev compose override.
Env : `.env.dev`, `.env.test`, `.env.example`, `.env.secret.example`,
`infra/k8s/overlays/dev/.env.secret.example`.
Tests : new `tests/unit/c8_llm/test_cost_receiver.py`,
`tests/integration/c8_llm/test_cost_receiver_api.py` ; updated
`test_config_schema.py`, `test_document_tools.py`, `test_k8s_dispatcher.py`,
`test_runtime.py`, `test_gitea_provisioning.py` (path-aware), env files.
