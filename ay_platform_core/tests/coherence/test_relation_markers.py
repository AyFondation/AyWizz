# =============================================================================
# File: test_relation_markers.py
# Version: 4
# Path: ay_platform_core/tests/coherence/test_relation_markers.py
# Description: Coherence 1 - spec<->code traceability.
#              Scans src/ for @relation markers in comments/docstrings and
#              verifies that every declared relation points to an existing
#              entity ID in the requirements corpus.
#
#              Path resolution (monorepo layout):
#                __file__ = <repo>/ay_platform_core/tests/coherence/test_*.py
#                parent             = tests/coherence
#                parent.parent      = tests
#                parent.parent.parent = ay_platform_core  -> SRC_ROOT here
#                parent^4           = <repo>              -> REQUIREMENTS_ROOT here
#
#              Placeholder implementation: full traceability rules are
#              defined in requirements/700-SPEC-VERTICAL-COHERENCE.md.
# =============================================================================

from __future__ import annotations

import re
from pathlib import Path

import pytest

SUB_PROJECT_ROOT = Path(__file__).parent.parent.parent  # ay_platform_core/
MONOREPO_ROOT = SUB_PROJECT_ROOT.parent  # <repo>/
SRC_ROOT = SUB_PROJECT_ROOT / "src"
REQUIREMENTS_ROOT = MONOREPO_ROOT / "requirements"

# Pattern: @relation <kind>:<entity-id>
# kinds: implements, validates, derives-from
# entity-id: R-NNN-XXX, E-NNN-XXX, D-NNN, T-NNN-XXX
RELATION_PATTERN = re.compile(
    r"@relation\s+(?P<kind>implements|validates|derives-from):"
    r"(?P<entity>(?:R|E|D|T)-[A-Z0-9-]+)"
)

# Pattern matching entity declarations in requirements markdown
ENTITY_ID_PATTERN = re.compile(r"^id:\s*(?P<id>(?:R|E|D|T)-[A-Z0-9-]+)", re.MULTILINE)


def _collect_declared_entities() -> set[str]:
    """Parse requirements/ for all declared entity IDs."""
    if not REQUIREMENTS_ROOT.exists():
        return set()
    entities: set[str] = set()
    for md_file in REQUIREMENTS_ROOT.rglob("*.md"):
        text = md_file.read_text(encoding="utf-8")
        for match in ENTITY_ID_PATTERN.finditer(text):
            entities.add(match.group("id"))
    return entities


def _collect_referenced_entities() -> dict[str, list[tuple[Path, int, str]]]:
    """Parse src/ for @relation markers and return references grouped by entity."""
    references: dict[str, list[tuple[Path, int, str]]] = {}
    if not SRC_ROOT.exists():
        return references
    for py_file in SRC_ROOT.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for match in RELATION_PATTERN.finditer(line):
                entity = match.group("entity")
                kind = match.group("kind")
                references.setdefault(entity, []).append((py_file, line_no, kind))
    return references


@pytest.mark.coherence
def test_all_relation_markers_point_to_declared_entities() -> None:
    """Every @relation marker in src/ SHALL reference a declared entity.

    Precondition: src/ MUST contain at least one @relation marker. C2/C3
    implementing modules declare markers for R-100-*/E-100-* entities; an
    empty set means a regression (markers stripped or src layout broken).
    """
    declared = _collect_declared_entities()
    referenced = _collect_referenced_entities()

    assert referenced, (
        f"no @relation markers found under {SRC_ROOT} — C2/C3 modules are "
        "expected to declare markers"
    )

    missing: dict[str, list[tuple[Path, int, str]]] = {
        entity: refs for entity, refs in referenced.items() if entity not in declared
    }

    if missing:
        messages = []
        for entity, refs in missing.items():
            for path, line_no, kind in refs:
                rel_path = path.relative_to(MONOREPO_ROOT)
                messages.append(f"  {rel_path}:{line_no} - @relation {kind}:{entity}")
        msg = "The following @relation markers reference undeclared entities:\n" + "\n".join(
            messages
        )
        pytest.fail(msg)


@pytest.mark.coherence
def test_validating_tests_reach_the_code_they_claim_to_validate() -> None:
    """A `validates:` marker SHALL name a test that can REACH its implementer.

    The checks above prove markers are well-formed and point at declared
    entities. They do not prove the two halves meet: a test file can claim to
    validate a requirement while importing nothing that leads to the module
    claiming to implement it, and `060-IMPLEMENTATION-STATUS.md` will still
    report that requirement `tested` — on the strength of a comment.

    (The marker syntax is deliberately NOT written out anywhere in this
    docstring. The convention is greppable prose, so an EXAMPLE marker in a
    comment is indistinguishable from a real one: the first version of this
    test cited a requirement id as an illustration and promptly registered
    itself as that requirement's validator, then failed on its own claim.)

    This closes that gap with the import graph, seeded from each test's own
    imports PLUS its `conftest.py` chain (pytest injects fixtures by name,
    so a graph rooted at the test file alone sees nothing a fixture built).

    NECESSARY, NOT SUFFICIENT: reaching the module does not prove the test
    asserts anything useful about it — that is §10's territory and no static
    analysis settles it. But a test that cannot reach the code is certainly
    not validating it, and that much is mechanical.
    """
    import audit_validation_reachability as m  # noqa: PLC0415

    issues = m.check()
    assert not issues, (
        "Requirements reported `tested` whose validating tests reach no "
        "implementing module:\n" + "\n".join(issues)
    )


@pytest.mark.coherence
def test_requirements_directory_exists() -> None:
    """The requirements/ directory SHALL exist at the monorepo root and be a directory."""
    assert REQUIREMENTS_ROOT.exists(), (
        f"requirements/ missing at expected location: {REQUIREMENTS_ROOT}"
    )
    assert REQUIREMENTS_ROOT.is_dir()
