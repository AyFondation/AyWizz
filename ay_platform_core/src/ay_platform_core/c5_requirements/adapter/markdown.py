# =============================================================================
# File: markdown.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/adapter/markdown.py
# Description: Markdown+YAML parse/serialise utilities for C5.
#              - parse_document() extracts the document-level frontmatter
#                and returns (DocumentFrontmatter, body).
#              - parse_entities() scans the body for entity frontmatter
#                blocks prefixed by a heading and returns a list of
#                (EntityFrontmatter, heading, entity_body) tuples.
#              - serialise_document() re-emits a document byte-exactly up
#                to whitespace normalisation (R-300-004).
#
# @relation implements:R-300-001
# @relation implements:R-300-003
# @relation implements:R-300-004
# @relation implements:R-300-005
# =============================================================================

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, cast

import yaml
from pydantic import ValidationError

from ay_platform_core.c5_requirements.models import (
    DocumentFrontmatter,
    EntityFrontmatter,
)

# Frontmatter delimiter is the sequence `---\n` at line start. Allow optional
# trailing whitespace on the fence line but not arbitrary content after it.
_FENCE = "---"


class AdapterError(ValueError):
    """Raised when parsing or validation fails at the adapter layer."""


@dataclass(frozen=True, slots=True)
class ParsedEntity:
    """One entity extracted from a document body."""

    frontmatter: EntityFrontmatter
    heading: str
    body: str


def parse_document(content: str) -> tuple[DocumentFrontmatter, str]:
    """Split a document into (frontmatter, body).

    Raises AdapterError on missing fence, malformed YAML, or unknown fields.
    """
    fm_text, body = _split_frontmatter(content)
    try:
        raw = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as exc:
        raise AdapterError(f"Invalid YAML frontmatter: {exc}") from exc
    if not isinstance(raw, dict):
        raise AdapterError(
            f"Document frontmatter must be a YAML mapping, got {type(raw).__name__}"
        )
    try:
        return DocumentFrontmatter.model_validate(raw), body
    except ValidationError as exc:
        raise AdapterError(f"Document frontmatter validation failed: {exc}") from exc


def parse_entities(body: str) -> list[ParsedEntity]:
    """Scan the body for entity YAML blocks prefixed by a heading.

    The recognised pattern is:

        #### <heading text containing the entity id>

        ```yaml
        id: <entity-id>
        ...
        ```

        prose body ...

    The heading level is not enforced (`#`, `##`, `###`, `####` all accepted).
    The entity body ends at the next heading or end of document.
    """
    entities: list[ParsedEntity] = []
    for match in _ENTITY_BLOCK.finditer(body):
        heading = match.group("heading").strip()
        yaml_block = match.group("yaml")
        try:
            raw = yaml.safe_load(yaml_block)
        except yaml.YAMLError as exc:
            raise AdapterError(
                f"Invalid YAML in entity block under heading {heading!r}: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise AdapterError(
                f"Entity frontmatter under {heading!r} must be a mapping"
            )
        try:
            fm = EntityFrontmatter.model_validate(raw)
        except ValidationError as exc:
            raise AdapterError(
                f"Entity validation failed under {heading!r}: {exc}"
            ) from exc
        entity_body = _extract_entity_body(body, match.end())
        entities.append(ParsedEntity(frontmatter=fm, heading=heading, body=entity_body))
    return entities


def serialise_document(frontmatter: DocumentFrontmatter, body: str) -> str:
    """Serialise (frontmatter, body) back into a `.md` document.

    Round-trip fidelity (R-300-004) holds up to whitespace normalisation
    and the canonical YAML key order the source document used. Callers
    that need byte-exact round-trips should operate on the raw content.
    """
    fm_yaml = _dump_yaml(
        frontmatter.model_dump(mode="json", by_alias=True, exclude_none=True)
    )
    return f"{_FENCE}\n{fm_yaml}{_FENCE}\n{body}"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

_ENTITY_BLOCK = re.compile(
    r"""
    ^\#+\s+(?P<heading>[^\n]+)\n        # Heading line (# / ## / ### / ####)
    (?:[^\n]*\n)*?                      # Optional intervening lines
    ^```ya?ml\s*\n                      # ```yaml or ```yml fence
    (?P<yaml>.*?)                       # YAML content (non-greedy)
    ^```\s*$                            # Closing fence
    """,
    re.MULTILINE | re.DOTALL | re.VERBOSE,
)

# Entity bodies extend through any prose and nested sub-sections (including
# `### Tailoring rationale` which is at level 3 per R-M100-101). They only
# terminate at the next `####`-level entity marker or at a higher-level
# document section heading that starts a new major chapter (`##` or `#`).
_NEXT_HEADING = re.compile(r"^(?:\#{4}\s+\S|\#{1,2}\s+\S)", re.MULTILINE)


def _split_frontmatter(content: str) -> tuple[str, str]:
    """Strip the `---\\n ... \\n---\\n` fence, returning (yaml_text, body)."""
    if not content.startswith(f"{_FENCE}\n") and not content.startswith(f"{_FENCE}\r\n"):
        raise AdapterError("Document must start with a '---' frontmatter fence")
    # Find closing fence after the opening one
    start = 4  # length of "---\n"
    # Handle CRLF gracefully by normalising to \n
    normalised = content.replace("\r\n", "\n")
    closing_idx = normalised.find(f"\n{_FENCE}\n", 4)
    if closing_idx == -1:
        # Maybe the file ends on the fence without trailing newline
        if normalised.rstrip().endswith(f"\n{_FENCE}"):
            closing_idx = normalised.rstrip().rfind(f"\n{_FENCE}")
        else:
            raise AdapterError("Unterminated YAML frontmatter (missing closing '---')")
    fm_text = normalised[start:closing_idx]
    body_start = closing_idx + len(f"\n{_FENCE}\n")
    body = normalised[body_start:] if body_start < len(normalised) else ""
    return fm_text, body


def _extract_entity_body(body: str, start: int) -> str:
    """Return the prose body of an entity, from `start` to next heading or EOF."""
    rest = body[start:]
    match = _NEXT_HEADING.search(rest)
    if match is None:
        return rest.strip()
    return rest[: match.start()].strip()


def _dump_yaml(data: dict[str, Any]) -> str:
    """Dump a mapping back to YAML, preserving insertion order."""
    return cast(
        str,
        yaml.safe_dump(
            data,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        ),
    )
