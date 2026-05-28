<!-- =============================================================================
File: 2026-05-26-dev-stack-litellm-and-runtime-diagram.md
Version: 1
Path: .claude/sessions/2026-05-26-dev-stack-litellm-and-runtime-diagram.md
Description: Session journal — full dev-stack rebuild for manual user testing :
             fixed a C7 boot crash, wired LiteLLM into the dev stack (and
             fixed its cost-forwarder mount), set the dev=real-LLM /
             CI=mock+Ollama policy, and added a runtime execution diagram.
============================================================================= -->

# Session — Dev stack rebuild, LiteLLM-in-dev, runtime diagram (2026-05-26)

## Context

Operator asked to rebuild + relaunch the whole stack via `e2e_stack.sh`
(`down -v` then `dev`) for manual browser testing, then to clarify what is
testable, then to diagram the real runtime. Three real defects surfaced and
were fixed in the process; none were caught by CI (they are container/runtime
issues, not unit/integration concerns).

## Fix 1 — C7/C3 boot crash (tree-sitter at import time)

`c7_memory/kg/code_extractor.py` loaded the tree-sitter Python parser at
MODULE IMPORT (`_PYTHON_PARSER = get_parser("python")`). The runtime `app`
user is created `--no-create-home` (`Dockerfile.api`), so the language-pack's
Rust core had no writable HOME for its grammar cache → `IO error: Permission
denied (os error 13)`. C7 crashed at boot; C3 cascaded (it imports
`c7_memory.service`). CI never saw it (tests run as a user with FS access).

Fix : lazy `@functools.cache def _python_parser()` — loaded on first use, not
at import. A single optional feature (code-AST extraction) must never prevent
the service from booting. `code_extractor.py` v2 ; full CI green (1665).
Code-AST extraction itself still needs the container HOME fix to actually run
(deferred — it is not a primary flow).

## Fix 2 — LiteLLM in the dev stack + cost-forwarder mount

Operator decision : **user-test stack = production reality** (C8 → LiteLLM →
Claude), **CI/CD = mock_llm + Ollama** to avoid provider cost. So:

- `e2e_stack.sh` v7 : `dev` now passes `--profile litellm` (brings up the
  proxy + C8 cost receiver) ; `up`/`full` (CI-ish) stay litellm-free. `status`
  made litellm-aware. `dev` requires `.env.secret` (ANTHROPIC_API_KEY +
  C8_GATEWAY_API_KEY).
- The proxy crashed on first real boot (pre-existing, never started via
  compose before) : litellm resolves a `callbacks` entry as a FILE next to the
  config (`/app/cost_forwarder.py`), NOT via PYTHONPATH. The override mounted
  the callbacks DIR + set PYTHONPATH. Fix (`docker-compose.dev.override.yml`
  v11) : bind-mount the module file at `/app/cost_forwarder.py`, drop the dir
  mount + PYTHONPATH. Proxy then booted clean ("Set models: claude-opus-
  flagship, claude-sonnet-midtier, claude-haiku-fast", liveliness 200).

## Decision (for SESSION-STATE §3)

- **LLM backend split** : `e2e_stack.sh dev` (manual user tests) runs the real
  LiteLLM proxy → Claude (per-agent routing, c3-rag/c3-docgen → Haiku) ; the
  CI/CD flows (`run_tests.sh ci`, `up`, `full`, pytest) keep using mock_llm /
  Ollama and never bill a provider. This is the operationalised form of the
  V2#1 decision, made concrete in the wrapper.

## Diagram

Added `requirements/051-RUNTIME-execution-flow.svg` (v1) — a concrete runtime
sequence : (A) RAG chat turn [UI → C2 auth → C3 → C7 hybrid retrieval → C8
LiteLLM → Claude → SSE → persist → async cost], (B) DocGen tool-loop [tool
call → C4 CRUD → Gitea versioned commit (vN) + MinIO → ⟳ until done]. Colour =
acting component. Complements (does not replace) the conceptual blueprint
`050-WORKFLOW-prompt-execution.svg`, which is now PARTIALLY STALE (its
"planned grey" A.b/A.c/eval items are done) — refresh deferred.

## Verification

`e2e_stack.sh dev` → 17 containers healthy (incl. ay-litellm, ay-c8-cost-
receiver, C3, C7). Reseed created a conversation (vs failing before litellm).
`run_tests.sh ci` green (1665 passed) for the C7 code fix. Compose coherence
intact. NOTE : end-to-end chat depends on `.env.secret` keys being valid — the
proof is the operator sending a chat message (a real, billed Anthropic call).

## Files

- `ay_platform_core/src/ay_platform_core/c7_memory/kg/code_extractor.py` (v2)
- `ay_platform_core/scripts/e2e_stack.sh` (v7)
- `ay_platform_core/tests/docker-compose.dev.override.yml` (v11)
- `requirements/051-RUNTIME-execution-flow.svg` (v1, NEW)

## Next

- C7 code-AST extraction in-container : give the `app` user a writable
  HOME/cache (Dockerfile.api or a compose env) — needed before that endpoint
  works in the container (§5.2 : Dockerfile change needs approval).
- Optionally refresh `050-WORKFLOW` (mark A.b/A.c/eval done).
- Resume the D-017 follow-ups (T3 D-011 cross-family provider ; #3 de-stub).
