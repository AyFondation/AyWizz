---
captured: 2026-09-11
topic: "@relation markers are assertions to verify, not paperwork"
scope: requirements
status: learned
session-ref: sessions/2026-09-10-gate-conformity-and-llm-resolution.md
---

# `@relation` markers are assertions to verify, not paperwork

## Context

Twice in three days, a `@relation` marker was written without reading the
requirement it named.

1. `implements:R-200-012` was put on `dispatcher/in_process.py` in the same
   change that REMOVED that file's entire contribution to Gate C. The file no
   longer carried the behaviour at all.
2. `implements:R-800-011` was put on `call_target_router.py`,
   `credential_injector.py` and their test. R-800-011 governs C8's ACCESS MODE
   ("HTTP only, never an importable Python SDK"). None of the three files
   implement that; they implement credential resolution.

Both were caught by re-reading, not by any tool. The second also masked a real
§8.1 structural gap: the per-call credential injection had NO requirement at
all, which is why `key_provider.py` had never carried a marker. Claiming a
neighbouring requirement made the gap invisible in
`060-IMPLEMENTATION-STATUS.md`.

## Rule

Before writing `@relation implements:R-NNN-XXX` or `validates:R-NNN-XXX`,
Claude SHALL open that requirement and read its normative sentence, then
confirm the file actually carries the behaviour that sentence describes.

When no requirement covers the behaviour, Claude SHALL leave the marker OFF
and raise a §8.1 structural gap — never substitute the nearest-looking
requirement id.

When a change REMOVES a file's contribution to a requirement, Claude SHALL
remove that file's marker in the same change.

## Rationale

The marker is the load-bearing half of "traceability by construction". A false
marker is worse than a missing one: `060-IMPLEMENTATION-STATUS.md` is generated
from these markers, so a wrong claim makes a requirement look implemented and
hides an unspecified behaviour from the very audit built to surface it. A
missing marker shows up as `not-yet` and gets fixed; a wrong one is invisible.

The failure mode is treating the marker as an end-of-task formality rather than
a claim about the system. It is a claim.

## Trigger conditions

- Writing or editing any `@relation implements:` / `validates:` comment.
- Adding a new module, router, or test file under `ay_platform_core/src` or
  `ay_platform_core/tests` that will be audited.
- Removing behaviour from a file that carries markers.
- Any task that ends with regenerating `060-IMPLEMENTATION-STATUS.md` — if the
  diff shows a requirement gaining or losing a file, verify that movement was
  intended.

## Example

```python
# WRONG — nearest-looking id, never read
# @relation implements:R-800-011      # …governs HTTP-only ACCESS to C8

# RIGHT — no requirement exists for this behaviour yet
#              NO `@relation` marker: the per-call credential injection has no
#              `R-` entity in the corpus. §8.1 structural gap, raised <date>.
```
