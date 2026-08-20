<!-- =============================================================================
File: 2026-08-19-provider-independence-d011.md
Version: 1
Path: .claude/sessions/2026-08-19-provider-independence-d011.md
Description: Made the LLM egress FULLY provider-independent (D-011). Removed
             every Anthropic-specific model entry + `os.environ/ANTHROPIC_API_KEY`
             from the LiteLLM config; the platform now routes to neutral tiers
             resolved per call by the in-app encrypted registry (endpoint + key
             injected clientside). Proven end-to-end by a new litellm
             testcontainers test. Bootstrap is Option B (registry empty until
             the operator configures a provider via the HMI). Cost moved from
             the config catalog to the registry. Backend CI: 2245 passed (only
             an unrelated K8s-cert env error). Deploy pending a kubeconfig
             refresh after the user's docker-desktop cluster reset.
============================================================================= -->

# Session — LLM provider-independence (D-011) (2026-08-19)

## Context
The operator required that the platform have NO adherence to any particular LLM
provider — Anthropic is used today but everything about LLM access must be
managed in-app. Investigation showed D-011 ("provider-agnostic by design") was
only ~4/5 realised: the client-side registry injection existed, but the LiteLLM
proxy still shipped static `anthropic/…` model entries with
`os.environ/ANTHROPIC_API_KEY`, the platform's `agent_routes` pointed at
Anthropic-named aliases, and cost was computed from the config catalog. Closed
the gap in five verified blocks; the operator chose **Option B** for bootstrap
(no seed env — the operator registers a provider via the HMI at first boot).

## Blocs
- **Bloc 1 — proxy honours per-call creds (the spike).** Added the
  `model_name: "*"` catch-all with `configurable_clientside_auth_params`
  (`api_key` + an anti-SSRF `api_base` allowlist) to both the canonical config
  and the K8s configmap. Proven by a NEW testcontainers test
  (`tests/integration/c8_llm/test_litellm_clientside_auth.py`): a real litellm
  proxy + a sibling mock OpenAI upstream on a shared network (DooD-safe: config
  written in-container via base64, no bind mounts) verifies the injected
  per-call `api_key`+`api_base` reach the upstream, and that a base outside the
  allowlist is refused.
- **Bloc 2+3 — neutral routing (coupled by the validator, which requires exact
  model_list membership).** Replaced the 3 Anthropic entries with 3
  provider-NEUTRAL tiers `flagship`/`balanced`/`fast` (`model: "*"` + clientside
  auth + full feature set); `agent_routes` now reference the tiers; removed every
  `os.environ/ANTHROPIC_API_KEY`. Canonical config v6, configmap config v4.
- **Bloc 4 — Option B bootstrap.** The registry seed now skips every
  pass-through entry (`model == "*"`), so seeding creates NOTHING — the registry
  is EMPTY until the operator registers a provider + model via the HMI and maps
  the tiers. `seed.py` v3.
- **Bloc 5 — cost from the registry.** Provider-independence zeroes the config
  catalog costs, so cost computation moved to the registry: new
  `build_registry_cost_catalog(models, providers)` keys `<wire>/<upstream>` →
  operator-set `provider_cost`; the cost receiver layers it over the config
  catalog per event (queried live so an HMI change takes effect immediately).
  `cost_tracker.py` v4, `c8_llm/main.py` v2.

## Tests (§10.3-D coordinated updates, flagged)
- New: litellm clientside-auth testcontainers proof; registry-cost-catalog unit
  tests; a synthetic-config seed-mechanism test.
- Updated to the new contract: `test_registry_seed` (canonical → empty seed +
  seedable-config mechanism), `test_config_schema` / `test_cost_receiver`
  (neutral tiers), `test_app_factory` (empty registry, Option B),
  `test_cost_receiver_api` + `test_quota_attribution_pipeline_e2e` (cost from a
  seeded registry model via the resolved `<wire>/<upstream>`).
- **CI: 2245 passed.** The only failure is `test_k8s_dispatcher_e2e::
  test_smoke_create_pod_and_watch` — an SSL cert error to
  `kubernetes.docker.internal` (the local cluster cert), unrelated to the LLM
  work and NOT masked (§10).

## Env / docs
- `.env.secret.example` (root + dev overlay v3): REMOVED `ANTHROPIC_API_KEY`
  (no provider key in env anymore) and ADDED `AY_SECRET_MASTER_KEY` (encrypts
  the provider keys the operator stores in the registry via the HMI).

## Deploy status
Images rebuilt after the cluster reset. Full redeploy is BLOCKED by a stale
kubeconfig: the docker-desktop reset regenerated the cluster CA but
`~/.kube/config` still carries the old CA (`x509: certificate signed by unknown
authority`). Fix is user-side (fully restart Docker Desktop so it rewrites
`~/.kube/config`); redeploy + verify pending that.

## Follow-ons
- Register the first provider via the HMI to lift Option B (no LLM call works
  until then). A documented seed/HMI flow would help.
- The static-vs-dynamic per-agent model selection question (Q-100-022) is
  unchanged; the neutral tiers are the routing surface it would build on.
