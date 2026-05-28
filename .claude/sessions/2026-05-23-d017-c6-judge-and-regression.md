<!-- =============================================================================
File: 2026-05-23-d017-c6-judge-and-regression.md
Version: 1
Path: .claude/sessions/2026-05-23-d017-c6-judge-and-regression.md
Description: Session journal — D-017 C6 evaluation slice : T3 LLM-as-judge
             verdict (R-700-032) + quality-regression detection (R-700-033).
             #3 interface-signature-drift de-stub DEFERRED.
============================================================================= -->

# Session — D-017 C6 slice: T3 judge + quality-regression (2026-05-23)

## Context

Continuation of the V2 work. The operator's directive was "D-017 complet ->
on fait les spec et on code", sequenced after the #8 de-stub and the V2 #2
compose increment (both done earlier, CI 1642 green). This session covers the
remaining D-017 C6 items: #3 de-stub, T3 LLM-judge, per-entity quality
time-series.

## #3 `interface-signature-drift` de-stub — DEFERRED (operator decision)

Investigation revealed #3 is **not** comparable to #8. Unlike #8
(`data-model-drift`, which nothing depended on), #3 is the **load-bearing
deterministic test probe**: C6 / C9-MCP / and **system-tier** tests invoke it
precisely because it always emits exactly one INFO finding regardless of
corpus. De-stubbing it to opt-in semantics (zero findings without an `E-*`
declaration) breaks ~6 files across integration + system tiers — and the two
system-tier files (`tests/system/test_mcp_tool_flows.py`,
`test_gateway_paths.py`) are **not runnable in the sandbox** (deployed-infra
tier), so the migration could not be verified locally. Immediate value is nil
(no `E-*` declares `signature:` yet, exactly like #8 did not declare
`fields:`). The asymmetry — identical value, ~10× cost incl. unverifiable
tiers — was surfaced; operator chose **defer**. #3 stays a documented STUB;
the de-stub is queued for the session that adds the first real `E-*` signature
consumer (and can co-design the probe replacement).

## T3 LLM-as-judge verdict — R-700-032 (DONE)

- **`c6_validation/grading_judge.py`** (NEW): `grade_judged(...)` asks an
  LLM-as-judge (C8 route `c6-judge`) to grade the run's artifacts against its
  requirements; maps a strict-JSON reply onto the shared `Verdict` envelope
  with `method=JUDGED`, `confidence` capped < 1.0. Lenient JSON parse (handles
  prose/fence wrapping), score/confidence clamped to [0,1]. **Best-effort**:
  any provider error or unparseable reply → `None` (the run keeps its T1
  verdict). Pure logic + injected client → unit-testable without a provider.
- **Wiring (additive, dormant by default)**: `ValidationService` gains an
  optional `llm_client`; T3 runs only when `C6_JUDGE_ENABLED=true` AND a
  client is wired. `ValidationRun.judged_verdict: Verdict | None` (additive,
  no break). `main.py` builds the C8 client like C7 (cheap, idle until the
  flag). Every existing fixture passes no client → T3 dormant → zero blast
  radius.
- **D-011 honesty**: every configured `agent_routes` entry is a Claude *tier*
  (same family). R-700-032 says the judge SHALL run on a DIFFERENT family;
  with Anthropic-only routing this is **not** satisfied today. Added the
  `c6-judge` route as a different-TIER placeholder (`claude-opus-flagship`)
  with an explicit comment + a `litellm-config.yaml` v3 header note: re-point
  at a non-Anthropic family before enabling the judge. Not papered over.

## Quality-regression detection — R-700-033 (DONE, Option 1)

Operator chose "régression à la complétion" over a read-only trend API or
deferral. The per-entity quality "time series" is realised **incrementally**:
each run compares itself to the project's previous completed run and emits
ADVISORY `quality-regression` findings — so the signal surfaces where it is
read (the run's own findings), with no separate stored series.

- **`c6_validation/regression.py`** (NEW, pure): `detect_regressions(...)`
  emits one ADVISORY per entity that **gained** blocking findings + one
  overall finding when the T1 score **dropped** vs the previous run.
  Improvement / steady-state → `[]`.
- **`repository.py` v2**: `get_latest_completed_run(project_id, domain)` (the
  running current run is naturally excluded by `status != completed`).
- **`service.py` v3**: T1 verdict graded **before** regression findings are
  appended (anti-feedback: a regression must never lower the score the next
  run compares against). `_detect_regressions` is best-effort — no
  predecessor / any read error → `[]`, never fails the run.
- **Blast radius nil**: `c6_repo` is function-scoped (unique DB per test) → no
  existing test ever has a predecessor. New integration test
  (`test_regression_flow.py`) does two runs (clean → degraded) in one DB to
  exercise it.

## Decisions

- D-017 C6 evaluation slice = T1 (done prior) + T2 #8 (done prior) + **T3
  judge (R-700-032)** + **regression (R-700-033)**. T2 #3 deferred.
- T3 is opt-in/best-effort; D-011 cross-family is an open operational gap
  (Anthropic-only routing) — tracked, not faked.

## Verification

`run_tests.sh ci` → **All stages OK** (ruff → mypy → pytest). **1665 passed,
1 skipped** (known K8s-cluster skip), coverage **87.53%**. 32 new unit tests
(13 judge + 6 regression + the prior batch) + 2 integration regression tests.
Coherence tier intact (`R-700-032`/`R-700-033` markers resolve; `ValidationRun`
contract additive; env-completeness updated for `C6_JUDGE_*`).

## Files

- `requirements/700-SPEC-VERTICAL-COHERENCE.md` (v4 — R-700-032 + R-700-033;
  v4 changelog note; #3 deferral note)
- `ay_platform_core/src/ay_platform_core/c6_validation/grading_judge.py` (v1, NEW)
- `ay_platform_core/src/ay_platform_core/c6_validation/regression.py` (v1, NEW)
- `ay_platform_core/src/ay_platform_core/c6_validation/service.py` (v3)
- `ay_platform_core/src/ay_platform_core/c6_validation/main.py` (v2)
- `ay_platform_core/src/ay_platform_core/c6_validation/models.py` (v4)
- `ay_platform_core/src/ay_platform_core/c6_validation/config.py` (v3)
- `ay_platform_core/src/ay_platform_core/c6_validation/db/repository.py` (v2)
- `infra/c8_gateway/config/litellm-config.yaml` (v3 — `c6-judge` route + D-011 note)
- `.env.example`, `ay_platform_core/tests/.env.test` (`C6_JUDGE_*` knobs)
- `ay_platform_core/tests/unit/c6_validation/test_grading_judge.py` (NEW)
- `ay_platform_core/tests/unit/c6_validation/test_regression.py` (NEW)
- `ay_platform_core/tests/integration/c6_validation/test_regression_flow.py` (NEW)

## Next

- **T3 D-011 enablement**: wire a second provider family + re-point `c6-judge`
  before `C6_JUDGE_ENABLED=true`.
- **#3 de-stub**: with the first real `E-*` `signature:` consumer.
- V2 #3-B (L2/L3) stays D-010-gated (no eval evidence). K8s after compose.
