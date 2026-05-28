# =============================================================================
# File: parsers.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c6_validation/domains/code/parsers.py
# Description: Lightweight parsers for the `code` domain:
#              - extract_markers: scans a CodeArtifact for `@relation` markers,
#                returning structured RelationMarker rows. Invalid markers
#                surface as MarkerSyntaxError descriptors (consumed by the
#                `marker-syntax` check).
#              - is_dead_module: decides whether a Python artifact is exempt
#                from check #2 (code-without-requirement).
#
#              Uses stdlib `re` + `ast` (cf. A-1: no tree-sitter in v1).
#
# @relation implements:R-700-040
# @relation implements:R-700-041
# =============================================================================

from __future__ import annotations

import re
from dataclasses import dataclass

from ay_platform_core.c6_validation.models import (
    CodeArtifact,
    RelationMarker,
    RelationVerb,
)

# Covers comments AND docstrings: any line containing `@relation ...`.
# The target group captures up to the end-of-line (no in-line code after the
# marker is expected, matching the convention used across ay_platform_core).
_MARKER_RE = re.compile(
    r"@relation\s+(?P<verb>[a-zA-Z][a-zA-Z0-9_-]*)\s*:\s*(?P<targets>[^\n]*)",
)

# Entity id (optionally version-pinned). Mirrors the C5 pattern but accepts
# relaxed whitespace after commas.
_ENTITY_REF_RE = re.compile(
    r"^(?:(?:R|T|E|Q)-M?[0-9]{3}-[0-9]{3}|D-[0-9]{3})(?:@v[0-9]+)?$",
)

# Sentinels that don't reference an entity — recognised by the `ignore-*`
# check helpers.
_IGNORE_MODULE_MARKER = "@relation ignore-module"
_IGNORE_TEST_FILE_MARKER = "@relation ignore-test-file"


@dataclass(frozen=True)
class MarkerSyntaxError:
    """A malformed `@relation` marker."""

    artifact_path: str
    line: int
    reason: str
    raw: str


def _strip_target_version(target: str) -> str:
    """`R-300-100@v2` → `R-300-100`. Used when we only need the base id."""
    return target.split("@", 1)[0]


def extract_markers(
    artifact: CodeArtifact,
) -> tuple[list[RelationMarker], list[MarkerSyntaxError]]:
    """Scan ``artifact.content`` for ``@relation`` markers.

    Returns:
        A pair ``(markers, errors)``.
        - ``markers``: one entry per valid ``@relation <verb>:<targets>``,
          with all targets parsed.
        - ``errors``: one entry per malformed marker (unknown verb or bad
          entity reference). Malformed markers are NOT placed in ``markers``.

    The scan is line-based so the line number in markers can be used by
    findings.
    """
    markers: list[RelationMarker] = []
    errors: list[MarkerSyntaxError] = []
    known_verbs = {v.value for v in RelationVerb}

    for lineno, line in enumerate(artifact.content.splitlines(), start=1):
        m = _MARKER_RE.search(line)
        if not m:
            continue
        verb_raw = m.group("verb")
        targets_raw = m.group("targets").strip()
        # Strip trailing ``*/ -->`` or similar closing tokens coming from
        # the host format. v1 only consumes Python, but the parser is
        # format-tolerant.
        targets_raw = re.sub(r"[`\*/>#]+$", "", targets_raw).strip()

        # Sentinels (no target) first.
        if verb_raw in {"ignore-module", "ignore-test-file"} and not targets_raw:
            # Represented with an empty-targets marker keyed by the sentinel
            # verb name. Not a protocol verb, so we don't record it in
            # `markers`; downstream helpers scan raw content for sentinels.
            continue

        if verb_raw not in known_verbs:
            errors.append(
                MarkerSyntaxError(
                    artifact_path=artifact.path,
                    line=lineno,
                    reason=f"Unknown verb {verb_raw!r}",
                    raw=line.strip(),
                )
            )
            continue

        targets = [t.strip() for t in targets_raw.split(",") if t.strip()]
        if not targets:
            errors.append(
                MarkerSyntaxError(
                    artifact_path=artifact.path,
                    line=lineno,
                    reason="Empty target list",
                    raw=line.strip(),
                )
            )
            continue

        invalid = [t for t in targets if not _ENTITY_REF_RE.match(t)]
        if invalid:
            errors.append(
                MarkerSyntaxError(
                    artifact_path=artifact.path,
                    line=lineno,
                    reason=f"Invalid entity reference(s): {invalid}",
                    raw=line.strip(),
                )
            )
            continue

        markers.append(
            RelationMarker(
                artifact_path=artifact.path,
                line=lineno,
                verb=RelationVerb(verb_raw),
                targets=targets,
            )
        )

    return markers, errors


def artifact_contains_sentinel(artifact: CodeArtifact, sentinel: str) -> bool:
    """Return True iff ``artifact.content`` contains the sentinel token."""
    return sentinel in artifact.content


def is_exempt_module(artifact: CodeArtifact) -> bool:
    """Check-#2 exemptions. A module is exempt iff:

    - its path is an `__init__.py`
    - OR its path is inside `tests/`
    - OR it carries the `@relation ignore-module` sentinel
    """
    if artifact.is_test:
        return True
    if artifact.path.endswith("/__init__.py") or artifact.path == "__init__.py":
        return True
    if artifact.path.startswith("tests/") or "/tests/" in artifact.path:
        return True
    return artifact_contains_sentinel(artifact, _IGNORE_MODULE_MARKER)


def is_exempt_test_file(artifact: CodeArtifact) -> bool:
    """Check-#5 exemptions. A test file is exempt iff:

    - it sits under `tests/fixtures/` or is named `conftest.py`
    - OR it carries the `@relation ignore-test-file` sentinel
    """
    path = artifact.path
    if "tests/fixtures/" in path:
        return True
    if path.endswith("/conftest.py") or path == "conftest.py":
        return True
    return artifact_contains_sentinel(artifact, _IGNORE_TEST_FILE_MARKER)
