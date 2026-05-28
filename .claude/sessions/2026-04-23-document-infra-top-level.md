<!-- =============================================================================
File: 2026-04-23-document-infra-top-level.md
Version: 1
Path: .claude/sessions/2026-04-23-document-infra-top-level.md
============================================================================= -->

# Session — 2026-04-23 — Document `infra/` top-level

## Context

Between the end of the previous Claude Code session (which delivered
C2, C1 Gateway as Traefik, and C3 Conversation Service) and the next
planned session (C5 Requirements Service), a drift was flagged:
`CLAUDE.md` v9 did not document the `infra/` top-level that had been
introduced by the C1 Traefik implementation. Per `CLAUDE.md` §5.2,
new top-level directories require explicit approval; `infra/` had
been approved implicitly at the time of the C1 decision but never
formalised in the governance file.

Consequence: a future session bootstrap would read an obsolete
description of the monorepo layout. Worse, Claude might treat a
future artifact under `infra/<new-component>/` as a §5.2 violation
and ask for approval that was already granted.

## Decisions taken

- **`infra/` formalised as a legitimate top-level**. Intro of
  `CLAUDE.md` updated to list four top-levels (`requirements/`,
  `ay_platform_core/`, `infra/`, future `ay_platform_ui/`).
- **Per-component structure inside `infra/`** codified as §4.5 of
  `CLAUDE.md`:
  - `infra/<component>/config/` — runtime configs (YAML, TOML).
  - `infra/<component>/k8s/` — raw K8s YAML (no Helm per existing C1
    decision).
  - `infra/<component>/docker/` — Dockerfiles, multi-stage preferred.
  - `infra/<component>/scripts/` — deployment / operational scripts.
  - `infra/scripts/` — scripts shared across components (no prefix).
- **Contract with `ay_platform_core/`** made explicit:
  - Component IDs SHALL match on both sides (e.g. `c2_auth` under
    `ay_platform_core/src/` and under `infra/`).
  - Configuration flows from `infra/<component>/config/` envs to
    Python runtime via `pydantic-settings`; no hardcoded values.
  - Dockerfiles build from monorepo root; `pyproject.toml` stays in
    `ay_platform_core/`.
- **Testing boundary**: `infra/` artifacts NOT covered by the
  `ay_platform_core/` pytest harness. Integration tests that need
  Docker / K8s still live under
  `ay_platform_core/tests/integration/<component>/`.
- **Header versioning** applies to all files under `infra/` (same
  rule as §4.3): `Version:` and `Path:` mandatory.
- **§5.2 updated**: the list of legitimate top-levels is now
  explicit (`requirements/`, `ay_platform_core/`, `infra/`,
  `.claude/`, `.devcontainer/`, future `ay_platform_ui/`). Any
  other top-level still requires approval.

## Deliverables

- `CLAUDE.md` v9 -> v10 (intro updated, §4.5 added, §5.2 clarified).
- `.claude/SESSION-STATE.md` v6 -> v7 (last updated bumped to
  2026-04-23, §3 decision added on `infra/` formalisation, §6
  archive entry added, §7 maintenance rule aligned with §9.1
  partial autonomy).
- `.claude/sessions/2026-04-23-document-infra-top-level.md`
  (this file, v1).

## Open questions carried forward

Unchanged:
- C4 Orchestrator blocked on empty 200-SPEC scaffold.
- C5/C7 async DB pattern settled (`asyncio.to_thread()`), no open
  question.

## Next step at end of session

Same as previous session: **C5 Requirements Service**. Has a
detailed spec (`300-SPEC-REQUIREMENTS-MGMT.md`). Should produce
deliverables under both `ay_platform_core/src/ay_platform_core/c5_requirements/`
and `infra/c5_requirements/` following the v10 convention.
