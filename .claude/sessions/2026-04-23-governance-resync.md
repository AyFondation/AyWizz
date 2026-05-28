<!-- =============================================================================
File: 2026-04-23-governance-resync.md
Version: 1
Path: .claude/sessions/2026-04-23-governance-resync.md
============================================================================= -->

# Session — 2026-04-23 — Governance resync (CLAUDE.md v11->v12, SESSION-STATE v10->v11)

## Context

Two Claude Code sessions ran in parallel on 2026-04-23:

- **Session A** (governance-focused, documented in
  `2026-04-23-sed-ban-and-e2e-category.md`): produced `CLAUDE.md`
  v12 + `.claude/settings.json` v4, banning `sed -i` and formalising
  `tests/e2e/` as a documented test category.
- **Session B** (delivery-focused): shipped C5 v1.5, C8 Python side,
  and populated 200-SPEC-PIPELINE-AGENT v2. Bumped
  `SESSION-STATE.md` to v10 and `ay_platform_core/pyproject.toml`
  to v6. This session did NOT observe session A's deliverables
  (running from an older workspace state).

As a result, at the end of 2026-04-23:
- Disk state: `CLAUDE.md` **v11**, `.claude/settings.json` **v3**,
  `SESSION-STATE.md` **v10** (referencing CLAUDE.md v11 throughout),
  plus session A's zip never applied to disk.
- Expected state (per design): v12 / v4 / v11 with both sessions'
  deltas integrated.

This session resyncs the governance artifacts without regressing any
delivery progress from session B.

## Decisions taken

- **Apply session A deltas on top of session B disk state**:
  - `CLAUDE.md` v11 -> v12 (§5.2 ban on `sed -i` / `--in-place`,
    §8.2 e2e category formalised).
  - `.claude/settings.json` v3 -> v4 (`sed -i` and `--in-place`
    denied, `sed -n` allowed).
  - `SESSION-STATE.md` v10 -> v11, preserving 100% of session B's
    content (C5, C8, 200-SPEC, python-arango thread-safety
    decision, C4 next action) and updating:
    - Governance line in §1 to reference v12 + v4.
    - §3 "End-to-end integration tests (NEW)" -> restated as
      "End-to-end tests" with direct pointer to `CLAUDE.md` v12
      §8.2 as the authoritative definition, and sub-summary of
      that definition for tool quick reference.
    - §3 new entry for the `sed -i` ban pointing to v12 §5.2.
    - §3 `infra/` decision: v10 reference -> v12.
    - §7 maintenance rule: v10 reference -> v12.
    - Policy header and §1 governance line aligned on v12.
  - Missing journal entry `2026-04-23-sed-ban-and-e2e-category.md`
    placed in `.claude/sessions/` (it had been authored upstream
    but never landed on disk).

- **No change to delivery artifacts**: `pyproject.toml` v6 preserved
  as-is; no component code modified; no test file touched.

- **Canonical recovery procedure captured** for future re-occurrence
  of parallel sessions:
  1. Compare disk state to the uploaded zip lineage.
  2. If disk is ahead on delivery and behind on governance (or
     vice-versa), merge the older dimension into the newer baseline
     file. Do not overwrite.
  3. Always preserve the most recent §1/§2/§5 deliverables content.
  4. Version bumps always required on files touched; add a journal
     entry documenting the merge.

## Deliverables

- `CLAUDE.md` v11 -> **v12** (content is session A's v12 verbatim —
  no divergence between session A's base v11 and disk v11).
- `.claude/settings.json` v3 -> **v4** (session A's v4 verbatim).
- `.claude/SESSION-STATE.md` v10 -> **v11** (session B's v10 with
  session A's governance deltas merged).
- `.claude/sessions/2026-04-23-sed-ban-and-e2e-category.md` v1
  (session A's entry, now landed).
- `.claude/sessions/2026-04-23-governance-resync.md` (this file, v1).

No change to `ay_platform_core/pyproject.toml` (stays at v6).
No change to any Python module under `src/` or `tests/`.

## Open questions carried forward

From session B, unchanged:
- 400-SPEC & 600-SPEC still scaffolds.
- LiteLLM proxy deployment deferred.
- C5 outstanding: import endpoint 501, ReqIF round-trip, point-in-time.

No new open question introduced by this resync.

## Next step at end of session

Unchanged from session B: **implement C4 Orchestrator**. 200-SPEC v2
provides 24 entities + 12 resolved Q-200-*. The C4 session will
introduce `tests/e2e/` per v12 §8.2 (already formalised). The
`sed -i` ban is mechanically enforced by `settings.json` v4 as of
this resync — any Claude Code attempt to use `sed -i` will be
refused at the tool level, forcing the use of Edit / `str_replace`.
