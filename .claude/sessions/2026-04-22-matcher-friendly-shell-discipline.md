<!-- =============================================================================
File: 2026-04-22-matcher-friendly-shell-discipline.md
Version: 1
Path: .claude/sessions/2026-04-22-matcher-friendly-shell-discipline.md
============================================================================= -->

# Session — 2026-04-22 — Matcher-friendly shell discipline

## Context

After deploying `.claude/settings.json` v2, the user observed that
Claude Code in VS Code still prompted for approval on many commands
despite their prefix being allow-listed. Analysis of the offending
commands revealed a structural limitation of the VS Code extension's
permission matcher, confirmed by public issues (#12604, #13340,
#15921, #18160):

- `2>&1` redirections defeat allow-list matching.
- Trailing pipes (`| head`, `| tail`, `| grep`) cause re-prompts.
- Command chaining (`cmd1 && cmd2`, `cmd1; cmd2`) requires the entire
  compound to match a single pattern — rarely the case.
- Inline env-var prefixes (`FOO=1 cmd`) are not expanded for matching.

Expanding the allow-list to cover all variants would grow unbounded
and still fail on combinatorial explosion. The user validated a
behavioural fix instead of an allow-list fix.

## Decisions taken

- **New `CLAUDE.md` §5.7 "Matcher-friendly shell discipline"**.
  Claude SHALL write bash invocations in their simplest matchable
  form: no `2>&1`, no trailing `| head`/`| tail`/`| grep`, no `&&`
  chaining, no inline env-var prefix. Use native tool flags
  (`--tb=short`, `-q`, `-x`, `-m "not integration"`) instead of shell
  composition. Multi-step tasks split into separate tool calls.
- **`.claude/settings.json` v2 -> v3**: added only the truly new
  script `run_coherence_checks.sh` (4 path variants: `./`,
  `ay_platform_core/`, `bash ./`, `bash ay_platform_core/`). No
  variants for redirections or pipes — those are handled by §5.7.
- **Explicit rationale**: §5.7 is flagged as a **workaround** for a
  Claude Code VS Code limitation, not a permanent preference. If the
  matcher is fixed upstream, the section can be relaxed.

## Deliverables

- `CLAUDE.md` v7 -> v8 (added §5.7).
- `.claude/settings.json` v2 -> v3 (added 4 allow entries for
  `run_coherence_checks.sh`).
- `.claude/SESSION-STATE.md` v3 -> v4 (governance line updated, new
  decision in §3, journal entry added to §6).
- `.claude/sessions/2026-04-22-matcher-friendly-shell-discipline.md`
  (this file, v1).

## Open questions carried forward

Unchanged from previous sessions:
- C5/C7 async DB access — still pending.
- First component choice — still pending user confirmation (C2 default).

New implicit open question:
- **When the matcher is fixed upstream**, review §5.7 for relaxation.
  No action until Anthropic addresses the referenced GitHub issues.

## Next step at end of session

Same as previous sessions: attack C2 (Auth Service) or C1 (Gateway).
The user's last command sample mentioned `c1_gateway` tests, which
may indicate a pivot toward C1-first rather than C2. To be confirmed
at the next session bootstrap.

With v8 active, test execution commands will be written in
matcher-friendly form (no `2>&1`, no `| tail`) and should largely
stop re-prompting.
