<!-- =============================================================================
File: 2026-05-19-docgen-2c-and-llm-provider-migration.md
Version: 1
Path: .claude/sessions/2026-05-19-docgen-2c-and-llm-provider-migration.md
Description: Append-only session journal entry. Immutable once written;
             corrections go in a new entry referencing this one.
============================================================================= -->

# Session — Phase 2.C DocGen end-to-end + unified inline pipeline + LLM provider migration

**Dates:** 2026-05-18 → 2026-05-19
**Outcome:** Phase 2.C (chat-direct DocGen, D-015) is **done and operator-validated end-to-end**: creating *and* modifying a document via chat works. Backend CI **1332 passed**, coverage 87.95%, `run_tests.sh ci` All stages OK.

---

## 1. What shipped

### 1.1 Unified inline-event pipeline (registered-contract change, §8.4)

Replaced the two parallel siloed mechanisms (`StageRecord` + the short-lived
`ToolCallRecord`) with a single abstraction, per operator's architectural
request ("un point d'entrée commun qui traite/stocke/formate/affiche"):

- **Model**: one `InlineEvent` (discriminated by `kind`: `stage` | `tool_call`
  | future). `MessagePublic.events: list[InlineEvent] | None` replaces
  `MessagePublic.stages`. C3 `models.py` v4, `repository.py` v4, `service.py`
  v10/v11.
- **Audit/traceability (Tier-2 of the operator's "local UI vs DB audit"
  split)**: terminal (`done`) events are persisted in ArangoDB on the
  assistant message — the inline log is now a queryable audit ledger, not
  ephemeral client telemetry. Survives reload by construction.
- **Legacy shim**: `_legacy_stage_to_inline` projects pre-unification persisted
  `stages[]` into `events` (kind=`stage`) at read time — **no data migration**.
- **SSE**: one channel `event: inline` (`_inline_sse`) replaces
  `event: stage` + `event: tool_call`.
- **Frontend**: one `<InlineLog>` (`components/inline-log.tsx`) with a per-kind
  **formatter registry** + a generic fallback (an unknown future kind is never
  dropped). `apiClient` exposes one `onInlineEvent`. `lib/types.ts` v11
  (`InlineEvent`, `Message.events`). Conversations page + ChatSidebar rewritten
  onto `<InlineLog>` (bespoke `PipelineChip`/`StageTimelineFull`/amber-strip
  deleted). Adding an event kind = one formatter, zero plumbing.

### 1.2 C8 robustness (root-caused from runtime evidence)

- `ChatMessage.content` made **Optional** (`models.py` v2). OpenAI spec: a
  tool-call assistant message has `content: null`. Ollama always sent a string
  so this was latent; the first spec-compliant hosted provider returned null
  and a `ChatCompletionResponse` validation error broke the whole tool loop
  *before* `tool_calls` could be read.
- `chat_completion` retries **HTTP 429** ≤3× honouring `Retry-After` /
  OpenRouter `retry_after_seconds` (cap 20 s) — `client.py` v2. Smooths
  transient free-tier throttling mid tool-loop instead of failing the turn.
  Non-429 non-200 still raises immediately.

### 1.3 DocGen prompt + tolerant parser (incremental, evidence-driven)

- `_DOCGEN_TOOL_DIRECTIVE` injected only when the tool loop is active; hardened
  to a step-numbered modify workflow + explicit FORBIDDEN list (no
  fenced-content-as-if-saved, no "specify/save the file", no `[placeholder]`,
  no stop-after-read).
- `_strip_json_comma_artifacts` added to `_lenient_json_object` (string-literal
  aware) — fixed qwen2.5:3b's malformed `,,` tool-call JSON (Phase 2.C.3
  defect, proven by c3 logs).

### 1.4 UX

- `hideOpenLink`: the "Open in Working area" deep-link is suppressed inside the
  Working area itself (we're already there).
- Conversations page v13: messages pane is content-height (capped + scroll) —
  no empty void above or below; composer sits under the content. Explicit
  "✓ Génération terminée — à vous" end-of-turn cue + composer auto-refocus;
  "Génération en cours…" spinner during the non-streaming tool loop.
- Working area page v3: `flex flex-col h-[calc(100dvh-8rem)]` + grid
  `flex-1 min-h-0` — the chat composer's Send button is always visible (the
  old `100vh-6rem` under-budgeted navbar+footer chrome → overflow).

### 1.5 Infra / config

- **Tier-2 `.env.secret`** introduced: `C3_C8_BEARER_TOKEN` (provider API key)
  loaded LAST in c3/c4 dev-override `env_file` with `required: false`
  (absent → Ollama/pytest unaffected). Git-ignored, operator-authored, never
  committed. `test_compose_dev_profile.py` v2: env_file helper made
  long-syntax aware (the `path:`/`required:` dict entry).
- Dev `ollama` resource envelope raised **2 GB/2 CPU → 12 GB/4 CPU**
  (`docker-compose.dev.override.yml`). Root cause of every recurring
  `llama runner ... signal: killed` was the **2 GB container cgroup cap**, not
  the Docker Desktop VM size (proven via `docker inspect` `OOMKilled=true` +
  Ollama `system memory` logs). The VM-size bumps the operator made were red
  herrings until this cap was lifted.

---

## 2. The LLM provider odyssey (decision rationale)

The DocGen tool loop is **agentic multi-step tool-calling** (read→update
chaining). Each candidate's exact failure mode (from c3 instrumentation logs):

| Provider / model | Failure |
|---|---|
| `qwen2.5:3b` (Ollama) | malformed `,,` tool-call JSON |
| `qwen2.5-coder:7b` (Ollama) | refuses to chain → narrates the write, never calls `update_document` |
| `llama3.1:8b` (Ollama) | inline call with Llama `parameters` key + corrupted `]"`-wrapped content |
| `hermes3:8b` (Ollama) | asks the user permission instead of acting |
| Gemini `2.0-flash` (AI Studio free) | 429 `limit:0` — no free tier on the account |
| Gemini `1.5-flash` | 404 NOT_FOUND on the account |
| OpenRouter `deepseek-v4-flash:free` | structured tool_calls OK (proved the `content=null` fix) but upstream 429 throttling mid-loop |
| **Anthropic `claude-haiku-4-5`** (OpenAI-compat) | **works — reliable structured tool-calling, seconds/turn. ADOPTED.** |

Also established: local 7-8B on a 4-CPU Docker VM ran ~4 min/turn — unusable
latency regardless of correctness. Anthropic exposes an OpenAI-compatible
surface (`/v1/chat/completions`, `Authorization: Bearer`, `claude-*`, tools
mapped) so the existing C8 client works **unchanged** — only `C8_GATEWAY_URL`
+ `C8_DEFAULT_MODEL` (Tier-1 `.env.dev`) and the Tier-2 key changed.

**Cost policy** (operator: "tests LLM le plus possible via ollama / un 3B"):
pytest e2e already uses `mock_llm` (deterministic, **zero cost** — strictly
better than routing tests at a live model). The paid Anthropic key is opt-in
*only* for the dev DocGen tool-loop; reverting the two `.env.dev` lines
restores local Ollama 3B for cost-free non-DocGen dev. The correct long-term
answer — per-agent C8 routing so only `c3-docgen` uses the hosted model — is
recorded as **Q-100-021** (needs the C8 router / litellm `agent_routes` wired
in dev; currently a single global `C8_GATEWAY_URL`).

---

## 3. Reserves / known limitations

- Anthropic API is **paid**: every dev DocGen turn bills tokens (Haiku chosen
  to minimise this). `.env.secret` must never be committed.
- Refresh-survival of the inline **audit trail** is done (persisted `events`).
  **Increment 3** (Tier-1 UI store + SSE-loop moved into a `(protected)`
  provider so a *live* generation survives tab navigation) is **not done** —
  next sizable piece, tracked in SESSION-STATE §5.
- Pre-existing unrelated `observability/.../test_elasticsearch_integration.py`
  flakes on ES testcontainer connectivity in some environments — not touched,
  not caused by this work; full `ci` run here was green.

---

## 4. Pointers

- ADR D-015: `requirements/999-SYNTHESIS.md`.
- Contract: `MessagePublic`/`InlineEvent` in `c3_conversation/models.py`;
  registered via `tests/fixtures/contract_registry.py`.
- Provider/cost wiring: `.env.dev` (Tier-1) + `.env.secret` (Tier-2) +
  `ay_platform_core/tests/docker-compose.dev.override.yml`.
