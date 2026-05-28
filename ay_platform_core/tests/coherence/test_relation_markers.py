# =============================================================================
# File: test_relation_markers.py
# Version: 3
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
def test_requirements_directory_exists() -> None:
    """The requirements/ directory SHALL exist at the monorepo root and be a directory."""
    assert REQUIREMENTS_ROOT.exists(), (
        f"requirements/ missing at expected location: {REQUIREMENTS_ROOT}"
    )
    assert REQUIREMENTS_ROOT.is_dir()
