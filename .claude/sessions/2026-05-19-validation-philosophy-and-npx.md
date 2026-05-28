<!-- =============================================================================
File: 2026-05-19-validation-philosophy-and-npx.md
Version: 1
Path: .claude/sessions/2026-05-19-validation-philosophy-and-npx.md
============================================================================= -->

# Session — 2026-05-19 — Validation philosophy + npx UI tooling allowlist

## Context

Triggered by recurring prompt fatigue during the UI hardening pass
(Increment 3a follow-ups: biome lint + tsc typecheck cycles after
each `.tsx` edit). Claude Code was issuing prompts on every
`npx biome check ...` and `npx tsc ...` invocation, often chained
in §5.7-violating composed shell (`&&` triplets, `2>&1 | tail`).

User raised the structural question: *"my validations don't add
value at this granularity — I want autonomy for technical commands
while keeping validation at decision points."*

That triggered a deliberate clarification of what human-in-the-loop
means for this project, rather than ad-hoc widening of the
allow-list.

## Decisions taken

### Decision 1 — Codify validation philosophy

Captured in `CLAUDE.md` v16 §5.3 (new paragraph) and as a §3 entry
in `SESSION-STATE.md` v44:

- **Decision gates** = human-in-the-loop applies:
  - Architecture choices
  - Plan / todo validation
  - Semantic env changes per §4.6 (adapter switch, model ID,
    feature toggle)
  - New specs / requirements
  - Contract changes (§8.4 registry)
- **Execution gates** = human-in-the-loop does NOT apply:
  - Read-only commands (status, list, view, diff)
  - Test / lint / typecheck / build commands
  - Analysis commands (grep, find, wc)

The allow-list scope reflects this distinction and SHALL expand
over time as new lecture-only / test-only commands prove safe and
frequent.

### Decision 2 — npx UI tooling allowlist

`settings.json` v13 already covered the `npm run X` family
(test/lint/typecheck/build/ci/format). v14 extends to **direct npx
invocations** that Claude Code reaches for naturally:

- `npx biome check:*` (covers `check --write` via wildcard —
  consistent with the `npm run format` opt-in that already passes
  through `biome check --write` under the hood)
- `npx biome ci:*`
- `npx biome format:*`
- `npx tsc:*`
- `npx eslint:*`
- `npx prettier --check:*`
- `npx --version`, `npx biome --version`

`Bash(npx:*)` blanket was rejected — too wide (would allow `npx
<arbitrary-package>`).

### Decision 3 — §5.7 stays authoritative on shell composition

Allowlisting tools does NOT lift the §5.7 ban on composed shell.
The matcher sees `cmd1 && cmd2 && cmd3` as ONE command — even if
all three would individually match. Claude SHALL decompose into
separate tool calls. The v14 header comment reiterates this
explicitly.

User declined to relax §5.7 on the grounds that the discipline is
a workaround for a known VS Code matcher limitation, not a stylistic
preference. When the matcher is fixed upstream, the rule can relax.

### Decision 4 — Fix stale `CLAUDE.md v20` reference

`SESSION-STATE.md` v43 §3 governance line said "CLAUDE.md v20"
while the disk file was v15. Drift between governance reference
and reality. Corrected to v16 (post-this-session bump). End-of-file
marker in CLAUDE.md was also `v20` — corrected to v16.

Root cause: SESSION-STATE bumped 28 times (v15 → v43) while
CLAUDE.md never bumped between v15 and now. Someone (Claude Code
or user) typed "v20" in §3 anticipating a CLAUDE.md bump that
never landed. Lesson: §3 references to CLAUDE.md SHALL match the
actual disk version at write-time. The §7 maintenance rule already
covers this implicitly; no new rule needed.

## Deliverables

- `.claude/settings.json` v13 → **v14**:
  - 8 new `npx` entries in `allow`.
  - Header comment updated with v14 rationale and §5.7 reminder.
  - All other entries preserved verbatim.
- `CLAUDE.md` v15 → **v16**:
  - §5.3 new "Validation philosophy" paragraph at the top.
  - §5.3 summary updated: full wrapper list (incl. K8s scripts,
    docker_test_cleanup), full UI tooling list (npm run X +
    npx direct).
  - End-of-file marker fixed: `v20` → `v16`.
  - All other sections untouched.
- `.claude/SESSION-STATE.md` v43 → **v44**:
  - Header bumped.
  - `Last updated` line: v44 delta noted + v43 prior summary
    preserved (Phase 2.C DocGen, 1332 tests, 87.95% coverage).
  - §3 Governance line: `CLAUDE.md v20` → `CLAUDE.md v16`,
    `.claude/settings.json` → `.claude/settings.json v14`.
  - §3 new "Validation philosophy" decision inserted after
    Governance (option (b) — meta-principle placement).
  - §6 archive: new entry prepended.
- `.claude/sessions/2026-05-19-validation-philosophy-and-npx.md`
  (this file, v1).

No touch on `ay_platform_core/pyproject.toml`, specs, or any
component code / test.

## Open questions carried forward

Unchanged from v43:
- 600-SPEC scaffold.
- LiteLLM proxy deployment deferred.
- C5 import 501, ReqIF/point-in-time v2.
- C7 ML adapters optional extras.
- C6 stubs #3/#8 need machine-readable specs.
- Q-100-016/017/018, Q-100-019 (Turbopack), Q-100-020 (Gitea KMS),
  Q-100-021 (per-agent C8 routing).

No new open question introduced by this session.

## Next step at end of session

Unchanged from §5 v44: **Increment 3b** — move the SSE send-loop
out of `ChatSidebar`/`[cid]` `onSend` into `WorkspaceProvider` so
live generations survive cross-tab navigation. High-risk pass —
rewrites the exact onSend/SSE path that DocGen e2e was just
validated on. Mandatory DocGen e2e re-test before claiming done.

With v14 / v16 / v44 active:
- Claude SHALL decompose `cd && npx X && npx Y` triplets into
  separate tool calls per §5.7.
- Individual `npx biome check`, `npx tsc`, `npm run lint` calls
  will pass without prompt.
- `--write` calls via `npm run format` or `npx biome check --write`
  pass too — covered by the wildcard. Operator accepted the
  opt-in semantics knowing that the diff is reviewed via
  `git diff` after the fact rather than VS Code per-edit (the
  cost-benefit was explicit in this session).
