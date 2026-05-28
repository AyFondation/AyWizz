<!-- =============================================================================
File: README.md
Version: 1
Path: .claude/learned/README.md
Description: Format specification for learned rule files captured during
             Claude Code sessions. This directory is a quarantine for
             project-specific rules pending validation and promotion.
============================================================================= -->

# Learned Rules - Quarantine Directory

Files in this directory are **project-specific rules** captured during Claude
Code sessions (via `#` shortcut or `/capture-lesson`). They are advisory
until reviewed and promoted to `.claude/conventions/`.

## File naming

`YYYY-MM-DD-<short-topic-slug>.md`

- Date: capture date (not the date of the underlying issue).
- Topic slug: lowercase, hyphen-separated, <= 5 words, describes the domain
  or concern (e.g. `arangodb-aql-bind-vars`, `k8s-readiness-probe-deps`,
  `pydantic-validator-ordering`).

## File format

Every file SHALL follow this structure:

```markdown
---
captured: YYYY-MM-DD
topic: <short descriptive title>
scope: <one of: python | arangodb | kubernetes | minio | n8n | litellm |
               requirements | frontend | devcontainer | workflow | other>
status: learned   # learned | promoted | merged | deleted
session-ref: <optional short reference to the session or commit>
---

# <Title>

## Context
<One short paragraph: what task was underway, what went wrong or what was
noticed. Factual, no blame, no narrative fluff.>

## Rule
<The rule itself, stated as an actionable directive for future Claude Code
sessions. Use SHALL / SHALL NOT when appropriate. One rule per file.>

## Rationale
<Why this rule matters. Link to root cause. Reference specs (R-NNN-XXX,
D-XXX) when applicable.>

## Trigger conditions
<When does this rule apply? Be specific enough that Claude can decide
whether to load this file for a given task.>

## Example (optional)
<Short code or config snippet illustrating correct vs. incorrect
application, if useful.>
```

## Lifecycle

1. **Capture** (this directory): rule is advisory.
2. **Audit** (user-triggered): rule is reviewed.
3. **Promotion**: validated rules move to `.claude/conventions/<scope>.md`
   (aggregated by scope). File here is deleted or marked
   `status: promoted` and kept as archive.
4. **Deletion**: rules found obsolete or erroneous are removed outright.

## Anti-patterns (do NOT capture here)

- Generic behavioural preferences (belong in `CLAUDE.md` §1 or user prefs).
- One-off fixes tied to a specific file that won't recur.
- Rules restating content already in specs (`requirements/*.md`).
- Rules duplicating existing `.claude/conventions/` entries (merge instead).

## Token discipline

This directory is **not** auto-loaded by Claude Code. Files are read on
demand based on topic matching. Keep each file <= 80 lines. If a rule grows
beyond that, it likely belongs in a dedicated convention document.
