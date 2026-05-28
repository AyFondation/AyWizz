<!-- =============================================================================
File: 2026-05-22-openhands-generate-poc.md
Version: 1
Path: .claude/sessions/2026-05-22-openhands-generate-poc.md
============================================================================= -->

# Session — 2026-05-22 — V2 #2 : OpenHands `generate`-phase POC adapter

## Context

Second of the three V2 features (operator order : #1 LiteLLM ✓ → #2 OpenHands
→ #3 Graphiti). The previous session shipped the GATED skeleton (a stub engine
behind `C4_GENERATE_ENGINE`). This session turned the stub into the real Q13
POC adapter. Operator decisions taken up front : web research authorised,
provider = **Anthropic direct**, packaging = **optional extra**, build scope =
**adapter + Dockerfile + tests + runbook**, and on the Gate B fork (below) =
**no fabricated evidence, Gate B blocks (honest)**.

## API research (no guessing — CLAUDE.md §5.5)

OpenHands ships a dedicated **V1 SDK** (`OpenHands/software-agent-sdk`, PyPI
`openhands-sdk` + `openhands-tools`, both 1.23, Python ≥3.12). Confirmed via
the SDK README + source + PyPI JSON :
- `from openhands.sdk import LLM, Agent, Conversation, Tool` ;
  `from openhands.tools.{terminal,file_editor,task_tracker} import …`.
- `LLM(model=…, base_url=…, api_key=…, extra_headers={…}, usage_id=…)` ; the
  `litellm_proxy/` model prefix routes through a LiteLLM proxy (= C8).
- `Conversation(agent, workspace, max_iteration_per_run).send_message(…).run()`
  (sync) ; final state via `conversation.state.execution_status`
  (`ConversationExecutionStatus`: terminal = FINISHED / ERROR / STUCK).
- `openhands-tools` is HEAVY (libtmux→tmux, tree-sitter, browser-use/Playwright)
  — confirms the "optional extra in a separate image" decision.

## Work delivered

- **`pipeline/generate_engine.py` v2** : stub → real adapter. The blocking SDK
  run is delegated to an injectable `runner` (the SOLE importer of
  `openhands.*`, R-200-029) so the mapping layer is unit-testable with a fake.
  `OpenHandsGenerateEngine.invoke` runs the runner via `asyncio.to_thread`,
  reads produced text files from the temp workspace into `output.files`
  (skipping `.git`/internals/binaries/oversized), cleans up, and maps :
  FINISHED → `DONE` + files ; ERROR/STUCK/missing-extra/ANY error → loud
  `BLOCKED` (a gated engine must never crash the run). LLM wired to C8/LiteLLM
  with the R-200-035 attribution headers (`X-Run-Id`/`X-Sub-Agent-Id`/
  `X-Agent-Name`/`X-Phase`).
- **`config.py` v3** : `C4_OPENHANDS_MODEL` (default
  `litellm_proxy/claude-opus-flagship`, a C8 `model_list` name) +
  `C4_OPENHANDS_MAX_ITERATIONS`.
- **`main.py` v6** : builds `OpenHandsEngineConfig` from the shared C8 client
  settings (same gateway URL + bearer as the dispatcher) + the cfg knobs.
- **`pyproject.toml` v10** : `openhands` OPTIONAL extra (`openhands-sdk>=1.23`,
  `openhands-tools>=1.23`), deliberately NOT in `all` ; mypy override
  `openhands.*` = ignore_missing_imports.
- **`infra/docker/Dockerfile.c15-runner` v1** : the `ay-api` image + the
  `openhands` extra + `tmux`/`git`. The shared `ay-api` image stays lean. POC
  model = run C4 from this image with `C4_GENERATE_ENGINE=openhands`.
- **`infra/docker/c15-runner-poc-runbook.md` v1** : operator steps for
  Phase 1/2 (build, env, pass criteria, the Gate B finding).
- **`tests/.../test_generate_engine.py` v2** + `.env.example` / `.env.test`.

## Decisions / findings

- **Gate B / Q1 (open finding, NOT a defect)** : the adapter returns `DONE` +
  `output.files` but emits NO `gate_b_evidence` — it does not fabricate
  red-first TDD proof. `domains/code/plugin.py::evaluate_gate_b` therefore
  blocks such completions ; files never materialise (materialisation is
  post-Gate-B). This is the unresolved synthesis Q1 (single-shot OpenHands run
  vs gated sub-steps). Operator captures it during the POC. See
  [[project-v2-openhands-poc]].
- **Spec posture (§8.1)** : R-200-029..038 are PROPOSED in synthesis §9, NOT
  ratified in the spec corpus. Synthesis mandates "POC before spec amendment",
  so `generate_engine.py` carries NO `@relation implements:R-200-029` marker
  (the coherence test `test_relation_markers.py` confirmed this — it failed on
  the marker, fixed by removing it). Marker added once the POC passes + the
  requirement is ratified.
- **Provider = Anthropic direct** (reuses `ANTHROPIC_API_KEY` ; synthesis
  reference is Databricks, swappable later, no code change).
- **§10.4 case D** : the v1 "POC stub" test (`assert "POC stub" in reason`) was
  replaced by adapter-mapping tests (FINISHED/ERROR/STUCK/missing-extra/
  runtime-error/cleanup) that mock the DEPENDENCY (the runner), never the
  engine under test (§10.2).

## Verification

`run_tests.sh ci` — ruff OK → mypy OK → pytest **1562 passed, 2 skipped**,
coverage **87.03%**. (Two intermediate CI failures fixed at root : N818
exception-name + unused `# noqa: BLE001` ; and the coherence marker above.)

## Operator follow-ups

- Build `ay-c15-runner:local` and run the real Q13 POC (Phase 1 : 3 tasks,
  cache>0%, OTel ; Phase 2 : git hook + working-data MCP + 10-task ≥70%).
  Needs the cluster + provider budget — out of the Claude Code sandbox.
- After POC success : ratify R-200-029..038 into `200-SPEC-PIPELINE-AGENT.md`,
  resolve Q1, then add the `@relation` markers.
