<!-- =============================================================================
File: 2026-04-24-script-path-forms.md
Version: 1
Path: .claude/sessions/2026-04-24-script-path-forms.md
============================================================================= -->

# Session — 2026-04-24 — Wrapper script canonical path forms

## Context

With `CLAUDE.md` v14 and `settings.json` v6 deployed, user observed
Claude Code VS Code prompting for permission on
`./ay_platform_core/scripts/e2e_stack.sh status` despite
`Bash(ay_platform_core/scripts/e2e_stack.sh:*)` being in the allow-list.

Root cause identified: the VS Code matcher does NOT normalise the
leading `./` in path-based commands. The pattern
`ay_platform_core/scripts/e2e_stack.sh` does not match the string
`./ay_platform_core/scripts/e2e_stack.sh` even though, from a
filesystem perspective, they resolve to the same file. Known issue
(GitHub #12604, #15921).

User's Claude Code build does NOT expose an "Always allow" button in
the permission dialog (only "Allow once" / "Deny"). This removes the
cheap escape hatch (persisting the exact form to
`settings.local.json`). Two options remained: enrich the allow-list
with the hybrid form, or rely purely on behavioural discipline.

User chose **option C** (both): allow-list enrichment + canonical
path rule in `CLAUDE.md`.

## Decisions taken

### Decision 1 — Canonical path forms for wrapper scripts

Codified in `CLAUDE.md` v15 §5.7. Two canonical forms:

- From the **monorepo root** (Claude's usual cwd):
  `ay_platform_core/scripts/X.sh ...` — no leading `./`.
- From **inside `ay_platform_core/`**:
  `./scripts/X.sh ...`.

The hybrid form `./ay_platform_core/scripts/X.sh` is explicitly
flagged as a matcher pitfall. Claude SHALL use one of the canonical
forms and stick to one cwd convention per session.

### Decision 2 — 5-forms convention for wrappers in settings.json

`settings.json` v6 -> **v7** now includes 5 allow-list entries per
wrapper:

1. `./scripts/X.sh` (from `ay_platform_core/`, canonical)
2. `ay_platform_core/scripts/X.sh` (from monorepo root, canonical)
3. `./ay_platform_core/scripts/X.sh` (hybrid, safety net — NEW)
4. `bash scripts/X.sh` (from `ay_platform_core/`)
5. `bash ay_platform_core/scripts/X.sh` (from monorepo root)

Applied to all 3 current wrappers: `run_tests.sh`,
`run_coherence_checks.sh`, `e2e_stack.sh`. 3 new entries total.

### Decision 3 — Safety net is not a primary path

`CLAUDE.md` v15 §5.7 + the `_comment_header` of `settings.json` v7
both state explicitly that the hybrid entry is a safety net, not the
preferred invocation. Claude SHALL prefer the canonical forms. The
safety net absorbs the inevitable lapses (VS Code build without
"Always allow", Claude reverting to the hybrid form under context
pressure, etc.) without blocking the session.

### Decision 4 — §5.3 wrapper-pattern convention updated

The documented "4 forms" becomes "5 forms" in `CLAUDE.md` v15 §5.3.
Any new wrapper added to the allow-list SHALL follow the 5-form
convention. Cross-referenced to §5.7 for the canonical form rule.

## Deliverables

- `.claude/settings.json` v6 -> **v7**:
  - Added 3 entries (`./ay_platform_core/scripts/run_tests.sh`,
    `run_coherence_checks.sh`, `e2e_stack.sh` — with `:*` wildcard).
  - Header comment updated.
  - All other allow / deny entries preserved verbatim.
- `CLAUDE.md` v14 -> **v15**:
  - §5.3 wrapper-pattern paragraph: "4 forms" -> "5 forms" with
    explicit enumeration and hybrid-form safety-net note.
  - §5.7 enriched with the canonical-path-forms rule.
  - End-of-file marker bumped.
  - All other sections untouched.
- `.claude/SESSION-STATE.md` v18 -> **v19**:
  - Header bumped, policy pointer updated to v15.
  - Last updated line: v19 delta + v18 prior + v17 delivery preserved.
  - §1 governance line updated (v15, v7).
  - §3 new decision on canonical path forms.
  - §3 existing wrapper-script-pattern decision: v13 reference
    kept (it is still the date of introduction; the 4-to-5 form
    upgrade is captured in the new decision that follows).
  - §6 archive: new entry prepended.
  - §7 reference to v15.
- `.claude/sessions/2026-04-24-script-path-forms.md` (this file, v1).

No touch on `ay_platform_core/pyproject.toml` (v6 preserved).
No Python, test, or spec file touched.

## Open questions carried forward

Unchanged from v18:
- 600-SPEC scaffold.
- LiteLLM proxy deployment deferred.
- C5 outstanding (import R-300-080 reported done in v17).
- C7 `deterministic-hash -> ollama` default switch — still awaits
  §3 decision per §4.6.
- C6 stubs #3 and #8.

New minor open question:
- If new wrappers are added in the future (e.g. a `deploy.sh` for
  K8s once AKS push starts), the 5-forms convention applies. If
  the convention becomes too noisy, consider automating allow-list
  entries from a script registry — deferred until we have more
  than 3 wrappers.

## Next step at end of session

Unchanged: **validate the compose stack** via
`./ay_platform_core/scripts/e2e_stack.sh status` or the appropriate
subcommand. With v7 in place, the hybrid form matches. Claude will
still be nudged by §5.7 to use the canonical form going forward.

If Claude writes the canonical `ay_platform_core/scripts/e2e_stack.sh status`
(no leading `./`), the pre-existing v5 entry matches directly — no
prompt. That is the target behaviour.
