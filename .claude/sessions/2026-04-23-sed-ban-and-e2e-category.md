<!-- =============================================================================
File: 2026-04-23-sed-ban-and-e2e-category.md
Version: 1
Path: .claude/sessions/2026-04-23-sed-ban-and-e2e-category.md
============================================================================= -->

# Session — 2026-04-23 — `sed -i` ban and `tests/e2e/` formalisation

## Context

User observed Claude attempting to apply multi-substitution `sed -i`
commands to modify Python source files (adding type hints, chaining
5 substitutions per invocation). Two issues surfaced simultaneously:

- **Workflow bypass**: `sed -i` writes to disk without surfacing a
  diff in VS Code. This defeats the user's "review diff before
  accept" discipline that applies to all other code edits via
  Claude Code's native Edit tool.
- **Undocumented directory**: the target file lived under
  `ay_platform_core/tests/e2e/`, a sub-directory that had been
  silently introduced by a previous Claude Code session without
  being formalised in `CLAUDE.md` §8.2 (test categories) or
  `SESSION-STATE.md` §2.

The user validated that `tests/e2e/` is a legitimate test tier
(golden-path multi-component tests) but wanted it formally
documented. Separately, the user wanted the `sed -i` ban made
permanent rather than handled case-by-case.

## Decisions taken

### Decision 1 — Ban `sed -i` for code edits

- **`CLAUDE.md` v12 §5.2** now explicitly forbids `sed -i` (and
  `sed --in-place`) for source file edits. All code modifications
  SHALL go through Claude Code's native Edit / `str_replace` tool.
- **`.claude/settings.json` v4** moves `Bash(sed -i:*)` and
  `Bash(sed --in-place:*)` to `deny`. Both variants are refused
  even on prompt.
- **`sed -n`** (pattern extraction, no write) remains legitimate
  and is added to `allow` for diagnostic purposes.
- Rationale: the user's review-before-accept workflow depends on
  VS Code surfacing diffs. Stream editors bypass that surface.

### Decision 2 — Formalise `tests/e2e/` category

- **`CLAUDE.md` v12 §8.2** now lists four test categories:
  unit / contract / integration / coherence (gate-blocking) plus
  **e2e** (not gate-blocking, added opportunistically).
- **`tests/e2e/` definition** (chosen by user from three options):
  golden-path workflows through FastAPI TestClient wiring multiple
  components together, real dependencies via testcontainers
  (ArangoDB, MinIO). NO real deployed infrastructure (no C1
  Traefik, no K8s, no docker-compose) — those are reserved for
  a future `tests/system/` category.
- Rationale: documented category prevents future sessions from
  treating e2e additions as §5.2 violations, while preserving the
  distinction from full-stack system tests.

### Decision 3 — Tests/system reserved for later

Not created now. Documented in §8.2 as the future home for tests
that exercise real deployed infrastructure (Traefik, K8s,
docker-compose). Opening the slot avoids conflating it with e2e.

## Deliverables

- `CLAUDE.md` v11 -> v12 (§5.2 amended, §8.2 rewritten).
- `.claude/settings.json` v3 -> v4 (`sed -i` / `sed --in-place`
  denied; `sed -n` allowed).
- `.claude/SESSION-STATE.md` v8 -> v9 (governance line updated,
  §3 two new decisions, §6 archive).
- `.claude/sessions/2026-04-23-sed-ban-and-e2e-category.md` (this
  file, v1).

## Open questions carried forward

Unchanged from previous sessions:
- Coverage audit of C1/C2/C3 still pending (per §5 of
  `SESSION-STATE.md`).
- C4 Orchestrator still blocked on empty 200-SPEC scaffold.

Implicitly resolved this session:
- **`tests/e2e/` status**: legitimate, documented.

## Next step at end of session

Same as previous: before C5 implementation, audit current coverage
of C1/C2/C3 against the §11 gate. When e2e tests are generated
later, they go under `tests/e2e/` without further approval needed.
`sed -i` edits are now mechanically blocked by `settings.json` v4;
if Claude attempts them, Claude Code will refuse and Claude is
expected to switch to the Edit tool without user intervention.
