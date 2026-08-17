<!-- =============================================================================
File: 2026-08-17-realtime-agentic-transparency.md
Version: 1
Path: .claude/sessions/2026-08-17-realtime-agentic-transparency.md
Description: Real-time exhaustive agentic feedback in the UI ("watch the agent
             work", Claude-Code-like) delivered in three increments over the
             persisted TraceEvent / InlineEvent stream, plus a per-request cost
             breakdown by model. Inc 1 live SSE runs, Inc 2 extended-thinking +
             verbosity toggle, Inc 3 sub-agent tree. Part B: per-request token
             cost + model-mix, stored and viewable. Both CIs green; deployed.
============================================================================= -->

# Session — Real-time agentic transparency + per-request cost breakdown (2026-08-17)

## Context
The operator asked to see, in the platform UI, an exhaustive real-time view of
the agentic harness's reasoning and steps — "comme sur claude code" — and to be
able to see a request's token cost and its per-model split (e.g. global 10 Mtok:
Opus 2.5 Mtok 25%, Haiku 7.5 Mtok 75%), stored and inspectable in the
consumption info. Specs were amended first (200-SPEC v4: R-200-206/207/208;
800-SPEC v12: R-800-146/147; 100-SPEC v21: R-100-140-adjacent). Delivered in
verified blocks (CI green + deployed per block), not one risky jet.

## Part B — per-request cost + model mix (delivered first)
- `CallTags` gained `turn_id`; C8 client `_headers` now emits `X-Run-Id` /
  `X-Turn-Id` (previously only C13 tagged run_id), and the sub-agent runtime
  forwards `run_id=envelope.run_id` so C4 runs tag their spend.
- c8 quota: `RequestCostBreakdown` + `ModelCostShare`; `request_breakdown(
  correlation, by, tenant_id)` service; repo `breakdown_by_model(field, value,
  tenant_id)` (field whitelist run_id/turn_id, AQL COLLECT by model).
- Endpoint `GET /admin/v1/quota/requests/{correlation}/breakdown?by=run|turn`.
- UI: apiClient `getRequestBreakdown` + a lookup panel on `/operator/quotas`.

## Inc 1 — live SSE runs (R-200-206)
- C4 SSE endpoint `GET /api/v1/orchestrator/runs/{run_id}/events` (replay-then-
  tail over the append-only TraceEvent ledger; done when run ≠ RUNNING);
  `service.get_full_trace(run_id) -> (list[TraceEvent], RunStatus)`.
- UI: `streamOrchestratorEvents(runId, {onTrace,onDone}, signal)` (fetch +
  `body.getReader()`, NOT EventSource — needs the forward-auth headers) →
  `liveTrace` state on the pipeline page, preferred over the polled `run.trace`.

## Inc 2 — extended thinking + verbosity toggle (R-200-207 / R-800-147)
- C8 client: `_apply_adaptive_thinking(body)` sets `thinking: {type:"adaptive"}`
  (does not clobber an explicit value); threaded via a `reasoning_verbose` flag
  on `chat_completion(_stream)`.
- C3: `_extract_delta_reasoning(chunk)` reads `delta.reasoning_content`; when
  verbose, per-chunk `reasoning` inline events stream and a final `reasoning`
  event (full text) is persisted in `MessagePublic.events`.
- MessageRequest `reasoning_verbose`; C2 user pref `reasoning_verbosity ∈
  {normal, verbose}`. UI: 🧠 composer toggle + `ReasoningFormatter` (violet
  `<details>`).

## Inc 3 — sub-agent tree (R-200-208)
- Backend: `TraceEvent` gained `sub_agent_id` / `parent_agent`; `_append_trace`
  accepts them; both agent-dispatch (start + end) markers now tag the dispatched
  agent. The sub-agent runtime does not emit ledger events, so the tree is the
  per-phase agents beneath the run — future nested spawns set `parent_agent`.
- UI: `buildSubAgentTree(events)` (pure derivation: group by `sub_agent_id`,
  nest by `parent_agent`, run-level events returned aside) + `<SubAgentTree>`
  (status from the terminal `ok`, summed duration) + a Timeline / Sub-agent-tree
  toggle on the pipeline page.

## Verification & deploy
- Backend `run_tests.sh ci` → All stages OK (ruff → mypy → pytest).
- UI `npm run ci` → 435 tests, 4 coverage thresholds passed (82.25% stmts).
- Traceability regenerated (`060`): R-200-206/207/208, R-800-146/147 all
  **tested** (added `_apply_adaptive_thinking` + sub-agent-tree unit tests to
  close R-800-147 / R-200-208 from implemented → tested).
- Images rebuilt (`k8s_build_images.sh`) + `run.sh dev --restart --wait` → all
  Deployments Available, for each of Part B / Inc 1 / Inc 2 / Inc 3.

## Notes
- The live stream is safe to index-tail because the TraceEvent ledger is
  append-only with no eviction (R-200-200/201).
- Inline `reasoning` rides the existing unified `event: inline` pipeline (one
  more `kind` + one more formatter), not a new channel.
