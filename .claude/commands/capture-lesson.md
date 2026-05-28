<!-- =============================================================================
File: capture-lesson.md
Version: 1
Path: .claude/commands/capture-lesson.md
Description: Slash command for structured capture of a learned rule into
             .claude/learned/. Invoked via /capture-lesson.
============================================================================= -->

# /capture-lesson

Capture a project-specific learned rule into `.claude/learned/` with the
canonical format defined in `.claude/learned/README.md`.

## Behaviour

When the user invokes `/capture-lesson` (optionally followed by a free-text
draft of the rule), perform the following steps **in order**:

1. **Clarify if needed.** If the draft is ambiguous or missing context,
   ask **one** targeted question before proceeding. Do NOT proceed on
   assumptions.

2. **Elicit the four mandatory fields** if not already provided:
   - **Context**: what task was underway when the issue surfaced.
   - **Rule**: the actionable directive, stated in SHALL/SHALL NOT form.
   - **Rationale**: why this rule matters (root cause, referenced spec
     IDs if any).
   - **Trigger conditions**: when this rule applies (scope, file patterns,
     task types).

3. **Classify the scope.** Map the rule to one scope tag from:
   `python | arangodb | kubernetes | minio | n8n | litellm | requirements |
   frontend | devcontainer | workflow | other`.
   If uncertain, ask the user.

4. **Generate the filename.**
   - Date: current session date in `YYYY-MM-DD` format.
   - Slug: derive from the rule's core concept, lowercase,
     hyphen-separated, <= 5 words.
   - Full path: `.claude/learned/YYYY-MM-DD-<slug>.md`.
   - If a file with the same slug already exists for today, append `-2`,
     `-3`, etc.

5. **Check for duplicates.** Before writing, list existing files in
   `.claude/learned/` whose slug overlaps semantically. If a close match
   exists, ask the user whether to:
   - Create a new file (distinct rule),
   - Extend the existing file (refinement),
   - Merge into the existing file (consolidation).

6. **Write the file** following the template in
   `.claude/learned/README.md`. Include all frontmatter fields with
   `status: learned`.

7. **Report.** End the turn with:
   - The full path of the created file.
   - A one-line summary of the rule.
   - A reminder that the rule is advisory until promoted via audit.

## Anti-patterns to reject

If the draft falls into one of these categories, do NOT capture. Instead,
explain to the user why and suggest the correct destination:

- Generic behavioural rule (tone, workflow, critique mindset) -> belongs in
  `CLAUDE.md` §1 or user preferences.
- One-off fix tied to a specific file that won't recur -> not a rule.
- Restatement of existing spec content -> reference the spec instead.
- Duplicate of an existing `.claude/conventions/` entry -> no capture,
  suggest updating the convention.

## Output discipline

- Respond in French (per `CLAUDE.md` §1.4).
- Keep the interaction concise. Steps 2 and 3 are elicitation, not
  dissertation.
- List the created/modified file at the end, per `CLAUDE.md` §1.3.
