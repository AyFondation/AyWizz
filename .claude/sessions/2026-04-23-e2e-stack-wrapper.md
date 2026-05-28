<!-- =============================================================================
File: 2026-04-23-e2e-stack-wrapper.md
Version: 1
Path: .claude/sessions/2026-04-23-e2e-stack-wrapper.md
============================================================================= -->

# Session — 2026-04-23 — e2e stack wrapper allowlist

## Context

Claude Code (working on the upcoming C4 Orchestrator implementation)
needed a cross-component e2e test stack: one shared ArangoDB + one
shared MinIO, orchestrated via `docker compose`. The lifecycle
(up/down, reset, logs) was to be driven by a new shell wrapper
`ay_platform_core/scripts/e2e_stack.sh`, invoked by Claude directly.

Two constraints in tension:

1. `docker compose` is in the `deny` list of
   `.claude/settings.json` (has been since v3). Legitimate concern:
   `docker compose down -v` destroys volumes; `docker compose build`
   / `pull` / `run` have persistent effects. Direct invocation
   bypasses intent review.
2. Forcing user approval at each `docker compose up` / `down` during
   e2e test runs defeats the automation benefit.

User chose the **wrapper-script pattern** (already implicit with
`run_tests.sh` and `run_coherence_checks.sh`): the wrapper is the
allowlisted entry point; the inner `docker compose` call remains a
sub-process of the wrapper that Claude Code's matcher never sees.

## Decisions taken

- **New allowlist entry for `e2e_stack.sh`** in
  `.claude/settings.json` v4 -> **v5**, using the standard 4 forms
  matching prior wrappers:
  - `./scripts/e2e_stack.sh:*`
  - `ay_platform_core/scripts/e2e_stack.sh:*`
  - `bash scripts/e2e_stack.sh:*`
  - `bash ay_platform_core/scripts/e2e_stack.sh:*`

- **`docker compose` remains denied**. Direct invocation is refused;
  the wrapper script is the sanctioned entry point.

- **Script location**: `ay_platform_core/scripts/e2e_stack.sh`,
  consistent with `run_tests.sh` and `run_coherence_checks.sh`. NOT
  at the monorepo root (`scripts/` top-level is not listed as
  legitimate in `CLAUDE.md` §5.2, and adding a new top-level for
  one script was rejected).

- **Wrapper-script pattern formalised** as `CLAUDE.md` v12 -> **v13**
  §5.3: destructive tooling stays denied; intents that legitimately
  need these tools are encapsulated in purpose-specific shell
  wrappers under `ay_platform_core/scripts/`. New wrappers SHALL
  follow the 4-form allowlist convention.

- **§5.3 also updated** with the refreshed allow / deny summary:
  explicit mention of the three wrappers (`run_tests.sh`,
  `run_coherence_checks.sh`, `e2e_stack.sh`) and of the denied
  `docker compose` / `sed -i` lines.

## Deliverables

- `CLAUDE.md` v12 -> **v13** (§5.3 rewritten to document the
  wrapper-script pattern and to list the three current wrappers).
- `.claude/settings.json` v4 -> **v5** (4 new allow entries for
  `e2e_stack.sh`; `docker compose` still in `deny`).
- `.claude/SESSION-STATE.md` v11 -> **v12** (governance line in §1,
  new decision in §3 on the wrapper pattern, §7 reference to
  `CLAUDE.md v13`, §6 archive entry).
- `.claude/sessions/2026-04-23-e2e-stack-wrapper.md` (this file, v1).

No change to `ay_platform_core/pyproject.toml` (v6 preserved).
The `e2e_stack.sh` script itself is NOT produced by this session —
it will be authored by Claude Code during the C4 Orchestrator
implementation session, along with the e2e test suite under
`ay_platform_core/tests/e2e/`.

## Open questions carried forward

Unchanged from the previous resync session.

## Next step at end of session

Same as before: **C4 Orchestrator implementation**. During that
session, Claude Code will author `e2e_stack.sh` (the wrapper is
now pre-authorised), the first cross-component e2e tests under
`ay_platform_core/tests/e2e/`, and the C4 module itself. No
additional permission prompt for `docker compose` is expected as
long as the wrapper is the invocation point.

If the wrapper pattern is broken (e.g. Claude tries to invoke
`docker compose` directly instead of through `e2e_stack.sh`), the
denial is authoritative: the session will be blocked until the
invocation goes through the wrapper.
