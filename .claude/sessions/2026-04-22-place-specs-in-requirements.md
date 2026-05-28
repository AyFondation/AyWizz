<!-- =============================================================================
File: 2026-04-22-place-specs-in-requirements.md
Version: 1
Path: .claude/sessions/2026-04-22-place-specs-in-requirements.md
============================================================================= -->

# Session — 2026-04-22 — Place specs in requirements/

## Context

Immediately following the `2026-04-22-setup-devcontainer-and-test-infra`
session, `requirements/` was empty (the monorepo skeleton had shipped
with only a `.gitkeep`). The 8 existing specs were in Claude's project
knowledge upload space but not on disk, and the Coherence 1 test
(`test_relation_markers.py`) had no corpus to scan. The user asked to
populate `requirements/` and to flag any missing pieces.

## Decisions taken

- **Physical placement** confirmed (moved into `SESSION-STATE.md` §3):
  - Authored specs at `requirements/` root: `100-SPEC-ARCHITECTURE.md`,
    `300-SPEC-REQUIREMENTS-MGMT.md`, `800-SPEC-LLM-ABSTRACTION.md`,
    `999-SYNTHESIS.md`.
  - Methodology moved into the canonical sub-directory:
    `requirements/meta/100-SPEC-METHODOLOGY.md` (its own `path:`
    frontmatter demanded this location).
  - Prior internal work placed at `requirements/references/`:
    `data-Extractor-specifications.md`, `simplechat-specification_backtend.md`,
    `simplechat-specification_frontend.md`. Rationale: consistent with
    "Open source reuse over reinvention" (`999-SYNTHESIS.md` §3.3), but
    clearly separated from platform specs because they do not follow
    the `NNN-SPEC-<slug>.md` naming convention.

- **Scaffolding for missing specs** — five `NNN-SPEC-*.md` files listed
  in `meta/100-SPEC-METHODOLOGY.md` §2 were absent (`200`, `400`, `500`,
  `600`, `700`). Created as **minimal scaffolds**: frontmatter compliant
  with `R-M100-040`, §1 Purpose/Scope referencing the relevant
  `D-XXX` decisions, explicit note `STATUS: SCAFFOLD`, zero `R-NNN-*`
  entities. No content was invented. Purpose: prevent cross-reference
  breakage in the corpus (other specs reference these identifiers via
  `derives-from` / `impacts`).

- **`CHANGELOG.md`** created at `requirements/` root per
  `meta/100-SPEC-METHODOLOGY.md` R-M100-130, initialised with an
  `[Unreleased]` section listing every file added this session.

## Deliverables

Placed (copied from project knowledge):
- `requirements/100-SPEC-ARCHITECTURE.md` (v2, unchanged)
- `requirements/300-SPEC-REQUIREMENTS-MGMT.md` (v1, unchanged)
- `requirements/800-SPEC-LLM-ABSTRACTION.md` (v1, unchanged)
- `requirements/999-SYNTHESIS.md` (v4, unchanged)
- `requirements/meta/100-SPEC-METHODOLOGY.md` (v2, unchanged)
- `requirements/references/data-Extractor-specifications.md`
- `requirements/references/simplechat-specification_backtend.md`
- `requirements/references/simplechat-specification_frontend.md`

Scaffolds (new):
- `requirements/200-SPEC-PIPELINE-AGENT.md` (v1, scaffold)
- `requirements/400-SPEC-MEMORY-RAG.md` (v1, scaffold)
- `requirements/500-SPEC-UI-UX.md` (v1, scaffold)
- `requirements/600-SPEC-CODE-QUALITY.md` (v1, scaffold)
- `requirements/700-SPEC-VERTICAL-COHERENCE.md` (v1, scaffold)

Supporting:
- `requirements/CHANGELOG.md` (new, initialised)

State updates:
- `.claude/SESSION-STATE.md` (v1 -> v2) — resolved "Specs placement"
  open question, added placement decision in §3.

## Open questions carried forward

Unchanged from previous session except for the resolved one:
- **C5/C7 async DB access** — still pending.
- **First component choice** — still pending user confirmation (C2
  default).

## Next step at end of session

Same as previous session's next step: transition to Claude Code in
VS Code, bootstrap using the updated state, and attack the first
component (C2 Auth Service recommended). The specs corpus is now
ready for Étape 1.
