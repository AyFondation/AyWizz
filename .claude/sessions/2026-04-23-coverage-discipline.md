<!-- =============================================================================
File: 2026-04-23-coverage-discipline.md
Version: 1
Path: .claude/sessions/2026-04-23-coverage-discipline.md
============================================================================= -->

# Session — 2026-04-23 — Coverage discipline

## Context

User requested a coverage gate on top of the existing test discipline:
≥ 80% line coverage on `src/` and "all branches tested". The second
was nuanced during discussion — strict 100% branch coverage is
unrealistic for async + error-handling code, and enforcing it
mechanically would push Claude toward anti-patterns (tautological
tests, `# pragma: no cover` abuse, etc.).

Chosen balance (Option C from the proposal): **line 80% blocking**,
**branch measured and reported but NOT blocking**. Branch gaps are
reviewed by the user on a case-by-case basis via `reports/latest/`.

This required a `pyproject.toml` change to wire the gate, and a new
behavioural rule (§11) in `CLAUDE.md` mirroring §10. The rule exists
because a coverage number is easy to game without improving test
quality — the same root cause §10 addresses from the "make tests
pass" side.

## Decisions taken

- **Gate configuration** (`ay_platform_core/pyproject.toml` v4):
  - `--cov=src --cov-branch --cov-fail-under=80` in pytest addopts.
  - `--cov-report=term-missing:skip-covered` for visibility.
  - HTML report under `reports/latest/htmlcov/`.
  - `[tool.coverage.report].fail_under = 80` (advisory for standalone
    `coverage report`; pytest's value is authoritative).
  - `[tool.coverage.report].exclude_also` curated (type-checking
    imports, `NotImplementedError`, `__name__ == "__main__"`, `pass`,
    `...`, `__repr__`/`__str__`, `@abstractmethod`).
  - `branch = true` in `[tool.coverage.run]`.
  - `precision = 2` for readable coverage numbers.

- **Behavioural rule** (`CLAUDE.md` v11 §11):
  - §11.1 Gate and metrics (what is blocking vs. reported).
  - §11.2 Eight forbidden anti-patterns: tautological tests,
    `# pragma: no cover` abuse on live code, extending
    `exclude_also` without approval, lowering the gate, assertion-
    free "hits", mocking to avoid coverage work, file splitting,
    deleting production code to raise the ratio.
  - §11.3 Legitimate ways to raise coverage: tests traced to
    `R-NNN-XXX`, branch classification per §10.3 A/B/C/D, edge-case
    tests matching spec pre/post-conditions.
  - §11.4 Escalation path when the gate legitimately blocks: move
    to integration tier, fix measurement, simplify code. Lowering
    the gate is last resort, requires approval + journal entry.
  - §11.5 Explicit mirror relationship with §10: same root cause
    (pressure for green bar without functional substance), same
    A/B/C/D diagnosis applies to coverage gaps.

- **Rachet policy**: threshold may be **raised**, never **lowered**,
  without user approval and a dedicated journal entry.

- **Next action updated**: before implementing C5, audit current
  coverage of C1/C2/C3. If any are below 80%, fix via legitimate
  tests (per §11.3), never via anti-patterns (per §11.2).

## Deliverables

- `ay_platform_core/pyproject.toml` v3 -> v4 (coverage gate wired).
- `CLAUDE.md` v10 -> v11 (§11 added).
- `.claude/SESSION-STATE.md` v7 -> v8 (§1 governance line updated,
  §3 decision added, §5 next action reoriented toward coverage
  audit, §6 archive updated).
- `.claude/sessions/2026-04-23-coverage-discipline.md` (this file,
  v1).

## Open questions carried forward

- C4 Orchestrator blocked on empty 200-SPEC scaffold.
- **New**: current coverage status of C1/C2/C3 unknown. Will be
  resolved by the audit step in §5.

## Next step at end of session

Audit coverage of C1/C2/C3 (line + branch) via `scripts/run_tests.sh`
per-component and globally. Document findings in
`reports/latest/`. If any component is below 80% line, address by
adding tests that trace to spec requirements — NOT by relaxing the
gate or applying any §11.2 anti-pattern. Only after the audit passes,
proceed with C5.
