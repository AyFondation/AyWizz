# =============================================================================
# File: validator.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/adapter/validator.py
# Description: Methodology-level validation rules applied on write (R-300-005,
#              R-300-050..052, R-300-082). The adapter's Pydantic validation
#              covers field shapes; this module covers cross-field and
#              cross-entity rules that need context (tailoring resolution,
#              rationale subsection detection).
#
# @relation implements:R-300-005
# @relation implements:R-300-050
# @relation implements:R-300-051
# =============================================================================

from __future__ import annotations

import re
from dataclasses import dataclass

from ay_platform_core.c5_requirements.models import (
    EntityFrontmatter,
    RequirementStatus,
)

# Tailoring rationale subsection heading per R-M100-101
_RATIONALE_HEADING = re.compile(
    r"^\#+\s+tailoring\s+rationale\b",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """Structured validation failure surfaced through HTTP 422."""

    rule: str
    entity_id: str
    message: str


def check_tailoring(
    entity: EntityFrontmatter,
    entity_body: str,
    *,
    is_project_level: bool,
    platform_parent: EntityFrontmatter | None,
) -> list[ValidationIssue]:
    """Enforce R-300-050 and R-300-051.

    Parameters
    ----------
    entity
        The candidate entity (frontmatter already Pydantic-validated).
    entity_body
        The Markdown prose body of the entity, used for the rationale check.
    is_project_level
        True if the candidate lives in a project corpus, False for platform.
    platform_parent
        Resolved platform-level target of `tailoring-of`, or None if missing.

    Returns
    -------
    List of validation issues. Empty on success.
    """
    issues: list[ValidationIssue] = []
    if entity.tailoring_of is None:
        if entity.override is True:
            issues.append(
                ValidationIssue(
                    rule="R-300-050",
                    entity_id=entity.id,
                    message="`override: true` requires a `tailoring-of:` target",
                )
            )
        return issues

    # Cross-project tailoring is forbidden (R-300-051). Tailoring chain can
    # only go project → platform.
    if not is_project_level:
        issues.append(
            ValidationIssue(
                rule="R-300-051",
                entity_id=entity.id,
                message=(
                    "Platform-level entities SHALL NOT carry `tailoring-of:`; "
                    "tailoring is strictly project → platform"
                ),
            )
        )
        return issues

    if entity.override is not True:
        issues.append(
            ValidationIssue(
                rule="R-300-050",
                entity_id=entity.id,
                message="`tailoring-of:` requires `override: true`",
            )
        )

    if platform_parent is None:
        issues.append(
            ValidationIssue(
                rule="R-300-050",
                entity_id=entity.id,
                message=(
                    f"`tailoring-of: {entity.tailoring_of}` does not resolve to "
                    "an existing platform-level entity"
                ),
            )
        )
    elif platform_parent.status == RequirementStatus.DEPRECATED:
        issues.append(
            ValidationIssue(
                rule="R-300-050",
                entity_id=entity.id,
                message=(
                    f"`tailoring-of: {entity.tailoring_of}` targets a deprecated "
                    "platform entity"
                ),
            )
        )

    if not _RATIONALE_HEADING.search(entity_body):
        issues.append(
            ValidationIssue(
                rule="R-300-050",
                entity_id=entity.id,
                message=(
                    "Entity body must contain a '### Tailoring rationale' "
                    "subsection (R-M100-101)"
                ),
            )
        )

    return issues


def check_deprecated_reason(entity: EntityFrontmatter) -> list[ValidationIssue]:
    """R-M100-040: `deprecated-reason:` is mandatory when status = deprecated."""
    if entity.status == RequirementStatus.DEPRECATED and not entity.deprecated_reason:
        return [
            ValidationIssue(
                rule="R-M100-040",
                entity_id=entity.id,
                message="`deprecated-reason:` is required when status = deprecated",
            )
        ]
    return []


def rationale_excerpt(entity_body: str, *, max_chars: int = 500) -> str:
    """Extract the rationale subsection body (R-300-052), truncated."""
    match = _RATIONALE_HEADING.search(entity_body)
    if not match:
        return ""
    after = entity_body[match.end():]
    # Rationale ends at next heading of equal or higher level. Keep it simple:
    # take up to the next heading line of any level.
    next_heading = re.search(r"^\#+\s+", after, re.MULTILINE)
    content = after[: next_heading.start()] if next_heading else after
    return content.strip()[:max_chars]
