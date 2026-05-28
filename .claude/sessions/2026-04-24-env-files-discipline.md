<!-- =============================================================================
File: 2026-04-24-env-files-discipline.md
Version: 1
Path: .claude/sessions/2026-04-24-env-files-discipline.md
============================================================================= -->

# Session — 2026-04-24 — Environment files discipline

## Context

During a prior Claude Code session, Claude attempted three consecutive
actions that revealed gaps in the governance framework:

1. Two `python << 'EOF' ... open(p, 'w').write(...) EOF` heredocs to
   modify `/workspace/ay_platform_core/tests/.env.test`: one adding
   two `C7_EMBEDDING_OLLAMA_*` keys, the other flipping the active
   embedder from `deterministic-hash` to `ollama`.
2. A `mkdir -p /workspace/ay_platform_ui/app/(public) ...` to scaffold
   the Next.js frontend in one shot.

Three overlapping problems:

- **Workflow bypass**: Python heredoc with `open(p, 'w').write()` is
  functionally identical to `sed -i` — an in-place edit bypassing
  VS Code diff review. Already covered by §5.2's "any shell stream
  editor with in-place write", but not enforced for `.env.*`
  because the file was also denied at the Read level.
- **Permission mismatch**: `.env.test` was caught by the blanket
  `Read(.env.*)` deny pattern, intended for sensitive files. This
  prevented legitimate edits (via Edit tool) while motivating the
  heredoc bypass.
- **Semantic change disguised as config tweak**: the
  `deterministic-hash -> ollama` flip is an architectural decision
  (C7 goes from zero-dep embedder to external service dependency).
  It should never have been a silent `.env.test` edit; it belongs
  in `SESSION-STATE.md` §3 with rationale.

## Decisions taken

### Decision 1 — Tier the `.env.*` sensitivity

Two tiers formalised in `CLAUDE.md` v14 §4.6:

- **Tier 1 (versioned, non-secret, editable via Edit)**: `.env.test`,
  `.env.dev`, `.env.development`, `.env.example`, `.env.template`.
- **Tier 2 (sensitive, denied)**: `.env`, `.env.local`, `.env.prod`,
  `.env.production`, `.env.secret`.

User confirmed `.env.test`/`.env.dev` are versioned in git with test
values only (no real credentials).

### Decision 2 — Affine the `.env.*` deny pattern

`.claude/settings.json` v5 -> **v6**: the blanket `Read(.env.*)` is
replaced by explicit Tier 2 patterns (`.env`, `.env.local`,
`.env.prod`, `.env.production`, `.env.secret`, each paired with
`**/.env.<name>` to catch nested occurrences). Claude now has
Read+Edit access to Tier 1 files.

### Decision 3 — `export` added to allow

`Bash(export:*)` added to allow-list. Useful for test sessions that
need ephemeral env vars without touching `.env.test`. Low risk
(single-command, no composition per §5.7).

### Decision 4 — Edit-only, no shell writes

§4.6 reinforces §5.2: `.env.*` edits SHALL go through `Edit` /
`str_replace`. Shell in-place writes — `sed -i`, Python heredoc
with `open(..., 'w').write(...)`, `echo >> file`, or any variant —
remain banned. The diff MUST be visible in VS Code before accept.

### Decision 5 — Semantic-change gate

§4.6 introduces a distinction with teeth:

- **Non-semantic changes** (no gate): new variable with placeholder,
  port number adjustment, typo correction.
- **Semantic changes** (§3 decision + possibly §8.1 gate): adapter
  switch (`C7_EMBEDDING_ADAPTER` deterministic-hash -> ollama),
  provider switch (`C8_LLM_PROVIDER` mock -> anthropic), feature
  toggle (`C2_SSO_ENABLED` false -> true).

The previously-attempted `deterministic-hash -> ollama` flip would
have been a semantic change. Claude will now be required to propose
it as a §3 entry with rationale; user arbitrates in-session. The
outcome of that specific decision remains open (not prejudged by
this session).

## Deliverables

- `.claude/settings.json` v5 -> **v6**:
  - `Read(.env.*)` blanket replaced by 10 explicit Tier 2 patterns.
  - `Bash(export:*)` added to allow.
  - `sed -i` / `sed --in-place` deny preserved.
  - All other allow / deny entries preserved verbatim.
- `CLAUDE.md` v13 -> **v14**:
  - New §4.6 Environment files discipline (tiers, rules, examples).
  - End-of-file marker bumped.
  - All other sections untouched.
- `.claude/SESSION-STATE.md` v17 -> **v18**:
  - Header bumped, policy pointer updated to v14.
  - Last updated line: v18 delta noted + v17 delivery info preserved
    (Ollama default embedder, C5 import R-300-080, ay_platform_ui/
    scaffold, traceability back-fill, 739 tests, 90.75% coverage,
    125 distinct entity refs).
  - §1 governance line updated (v14, v6).
  - §3 new decision on env files discipline.
  - §3 existing decisions (`infra/`, e2e, sed ban, wrapper-script)
    harmonised to reference v14/v6.
  - §6 archive: new entry prepended.
  - §7 reference to v14.
- `.claude/sessions/2026-04-24-env-files-discipline.md` (this
  file, v1).

No touch on `ay_platform_core/pyproject.toml` (v6 preserved).
No touch on any Python module, test, or spec.

## Open questions carried forward

Unchanged from v17:
- 600-SPEC still scaffold.
- LiteLLM proxy deployment deferred.
- C5 outstanding (import 501 — NB: user-visible status says R-300-080
  was addressed in the session between v16 and v17; double-check).
- C7 ML adapters — and now, explicitly: the proposed
  `deterministic-hash -> ollama` default switch awaits a §3 decision
  per §4.6.
- C6 stubs #3 and #8.

## Next step at end of session

Unchanged project trajectory: **validate the compose stack** via
`./ay_platform_core/scripts/e2e_stack.sh full` on a docker-enabled
host. The env-files rules are now active: if Claude proposes
another Tier 1 semantic change during stack validation (likely, given
the ollama work in flight), it will surface the change as a §3
decision proposal — no more silent heredoc edits.

For the next Claude Code session, the prompt preamble should note:
"Tu tournes sur CLAUDE.md v14 + settings.json v6 + SESSION-STATE.md
v18. §4.6 est actif: les edits de `.env.test`/`.env.dev` passent
par le tool Edit, et tout changement sémantique (ex: switch
d'adapter C7) doit être tracé en §3 avant application."
