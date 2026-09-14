<!-- =============================================================================
File: 2026-09-10-gate-conformity-and-llm-resolution.md
Version: 1
Path: .claude/sessions/2026-09-10-gate-conformity-and-llm-resolution.md
Description: Closed the Gate C conformance gap (the derivation manufactured the
             exact anti-pattern R-200-012 forbids), wired traceability for both
             gates, then found by READING — at zero provider cost — that the
             OpenHands generate engine had NO authentication path at all: it
             talks to the proxy directly, bypassing the app-tier credential
             injection, and every proxy tier is deliberately credential-free.
             Fixed via a proxy pre-call hook resolving against an internal,
             bearer-guarded endpoint (option 2), after correcting an overstated
             security argument of my own. Finally, on the operator's call, the
             engine's MODEL now comes from the platform's own LLM configuration
             instead of a manifest env var. CI All-OK (2568), deployed to K8s.
             The real run is deliberately NOT done — it is the operator's, and
             ours to do together.
============================================================================= -->

# Session — Gate conformity + app-driven LLM resolution (2026-09-10)

## Context

Follow-on from 2026-09-09, which established that gate evidence must be
OBSERVED rather than asserted and fixed Gate B. This session finished that
thought (Gate C), then tried to actually run the OpenHands engine on
Kubernetes — the operator's explicit requirement being "au plus proche de la
version cible finale", i.e. the target runtime, not docker-compose.

## Bloc 1 — Gate C: the spec was already right

R-200-012 reads: verification evidence dated after the last artifact write,
and "cached or stale verification results are rejected... preventing the
green-when-last-measured anti-pattern".

`_derive_gate_c_evidence` synthesised `validation_runs_green: True` with an
`evidence_timestamp` placed exactly one second ahead of a FABRICATED
`last_artifact_write`. It manufactured the very anti-pattern the requirement
exists to forbid, and every review passed Gate C with nothing verified.

**No spec amendment was needed — only conformance.** The derivation is gone
(`in_process` v7); an absent block leaves `runs_green` False and Gate C blocks.
Same treatment Gate B received the day before, and the same §10.4 case-D
contract change in the tests, documented in the test file header.

Accepted consequence, stated loudly at the time: on the active path
(`C4_GENERATE_ENGINE=in_process` + a single-shot sub-agent that cannot execute)
**both gates now block**. That is the spec applying, not a regression — and it
turns enabling the engine from an improvement into a prerequisite.

## Bloc 2 — Traceability closed

`@relation implements:R-200-011` on `generate_engine.py` and `in_process.py`;
`validates:` on both test files; `060-IMPLEMENTATION-STATUS.md` regenerated.

One self-correction worth recording: I first put `implements:R-200-012` on
`in_process.py` too. Wrong — since v7 that module contributes NOTHING to Gate
C. Removing a fabrication is a conformance decision, not an implementation of
the requirement, and the marker would have inflated the audit with a file that
does not carry the behaviour. Removed, audit regenerated.

The regeneration also surfaced pre-existing staleness unrelated to this work
(a `c4_workers` namespace file deleted by the uncommitted infra workstream,
`R-500-005` at v3 since the Markdown session). Verified before assuming.

## Bloc 3 — The engine had no authentication path, found by reading

The K8s wiring went in first: a registry-shaped C15 image
(`k8s_build_images.sh --c15`), an OPT-IN kustomize layer
(`base/c4_openhands`: image swap + engine env + an agent-sized resource
envelope), and `run.sh --openhands` carrying a cost warning. C4 came up on the
C15 image with the engine wired and no import error.

Then, before spending anything, the config was read rather than exercised.
**Every tier in `model_list` is `model: "*"` with
`configurable_clientside_auth_params`** — no upstream model, no `api_base`, no
key. That is D-011 option B by design: the proxy is a credential-free
forwarder, and the app tier supplies all three per request via
`C8LLMClient._inject_upstream_key`.

`_default_runner` builds `LLM(model=..., base_url=gateway, api_key=bearer)` —
where the bearer authenticates to the PROXY, not to a provider. The OpenHands
SDK therefore talks to the proxy DIRECTLY, never through the C8 client, so
`RegistryKeyProvider` never runs. Every call would land on a tier with nothing
to route to and nothing to authenticate with.

R-200-029 was satisfied in FORM (base_url is the proxy, never a provider) but
not in SUBSTANCE. Same class of problem already recorded for C13: any direct
caller of the proxy has no credential path. Cost of this discovery: zero.

## Bloc 4 — Option 2, after correcting my own argument

Three options were put to the operator; I recommended the proxy-side hook
(option 2) but argued it "introduces a decrypted key over an internal HTTP
hop". **Challenged, and the code proved me wrong**: `client.py` already does
`body["api_key"] = target.api_key` on every single call. That hop exists by
design. The claim was withdrawn.

The real, narrower differences, which stand:

- **A key-DISPENSING endpoint is a different object from a key-CARRYING
  request.** Today a key travels with a unit of work, outbound, scoped to one
  call, under quota and cost tracking. An endpoint that returns keys on demand
  is independently reachable (SSRF from any pod, a misrouted C1 rule) and
  ENUMERABLE — every provider key, without making a single LLM call.
- **Trust direction inverts toward a third-party image.**
  `ghcr.io/berriai/litellm:main-stable` is off-the-shelf on a FLOATING tag.
  Recipient-of-credentials becomes requester-of-credentials.

Operator chose option 2 on that corrected basis, for the reason that has
nothing to do with security: it fixes the CLASS (C13 and any future direct
caller), not one adapter.

Shipped with both conditions the operator accepted:

- **Hosted on `c8_admin`**, which ALREADY holds `AY_SECRET_MASTER_KEY` (it
  encrypts provider keys on upsert) — so no new component learns the master
  key. The cost receiver was the obvious host (registry repos + `/internal/`
  convention already there) and was rejected precisely because it would have
  been a fourth holder.
- **Not routed by C1** — verified, no router matches `/internal/`.
- **Shared-bearer guarded** with `hmac.compare_digest` (a naive `==` leaks the
  key byte-by-byte through timing on a credential endpoint), **failing CLOSED**
  on an unset key, and `/internal` exempted from the USER forward-auth guard
  because the proxy has no user identity.
- **Decryption stays app-side.** The LiteLLM image receives one resolved
  credential per alias, exactly as it already receives one per request.

The hook (`infra/c8_gateway/callbacks/credential_injector.py`, mounted like the
existing cost forwarder, importing only `litellm` + `httpx`) **never overrides
the app tier**: a request already carrying `api_key` is returned untouched, so
the established path is unaffected by construction rather than by ordering.
Best-effort throughout — a credential resolver must never be the reason a call
fails that would otherwise work.

## Bloc 5 — The model comes from the application's configuration

Operator requirement, mid-session: *"je veux utiliser par défaut le paramétrage
des LLM faites dans l'application"*. Correct, and it killed what was about to
be shipped — a `litellm_proxy/claude-sonnet-5` pinned in the manifest, which
would have made the generate engine the ONE component pinning a model while
every other LLM caller honours the application's configuration.

The platform already had the mechanism: `build_registry_model_resolver` maps
the agent to a quality tier and returns the model the operator enabled FOR THAT
PROJECT in the HMI (D-011). The adapter was short-circuiting it with an env
var. `OpenHandsGenerateEngine` now takes that resolver and builds a per-call
config; the manifest carries NO model name, which also dissolves the D-011
conflict this session had introduced.

`require_tool_calling=True` is mandatory in that resolution: an agent loop that
cannot call tools cannot edit a file or run a test — such a model is not a
degraded choice, it is a broken one. `C4_OPENHANDS_MODEL` survives only as a
fallback for a registry that is not populated yet, and is deliberately absent
from the manifest.

## Defects fixed in passing

- **`run.sh` usage heredoc**: unescaped backticks ran `ingressClassName` as a
  command, so `--help` printed "command not found" and dropped the word.
- **My own `openhands_poc.sh` wrapper**, twice: it omitted
  `docker-compose.dev.override.yml` (where the `litellm` service is declared
  and where `c4_openhands` gets its env_file list), and `require_c8_running`
  matched the KUBERNETES litellm pod on a substring — the guard would have
  passed with nothing reachable on the compose network. Both found by using it.

## Open questions

- **The real run is not done.** Deliberate: the pipeline has human gates, and
  driving it by API would mean simulating the approvals the platform exists to
  enforce. To be done with the operator. It proves P1, allows ratifying
  R-200-029, and unblocks P3 (the eval harness) and everything after.
- Registry aliases are provider-NAMED (`claude-sonnet-5`), not neutral tiers.
  Harmless now that no manifest pins a model, but registering neutral aliases
  remains the clean D-011 posture.
- R-200-029 is still PROPOSED, not ratified.

## A lesson about method, not code

Twice in this session I wrote `# noqa: BLE001` for a rule that is not enabled
in this project, and ruff rejected both. The reflex was to suppress a finding
rather than check the actual configuration — the same instinct §12.3 forbids.
Worth capturing as a learned rule (§7) if it recurs.

## Verification

`run_tests.sh ci` — **All stages OK, 2568 passed**, coverage 89.46%. Deployed:
C4 on the C15 runner with no pinned model in its env (verified via
`kubectl get deployment -o jsonpath`), LiteLLM initialised with both callbacks
and no import error, 16/16 Deployments Available.
