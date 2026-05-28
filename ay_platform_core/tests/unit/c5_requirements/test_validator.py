# =============================================================================
# File: test_validator.py
# Version: 1
# Path: ay_platform_core/tests/unit/c5_requirements/test_validator.py
# Description: Unit tests for the C5 methodology validator — tailoring,
#              deprecation, rationale extraction (R-300-050 / R-300-051).
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c5_requirements.adapter.validator import (
    check_deprecated_reason,
    check_tailoring,
    rationale_excerpt,
)
from ay_platform_core.c5_requirements.models import (
    EntityCategory,
    EntityFrontmatter,
    RequirementStatus,
)


def _make_entity(**overrides: object) -> EntityFrontmatter:
    defaults: dict[str, object] = {
        "id": "R-300-500",
        "version": 1,
        "status": RequirementStatus.DRAFT,
        "category": EntityCategory.FUNCTIONAL,
    }
    defaults.update(overrides)
    return EntityFrontmatter.model_validate(defaults)


_RATIONALE_BODY = """Some prose.

### Tailoring rationale

This project needs a stricter rate than the platform default because …
"""


@pytest.mark.unit
class TestCheckTailoring:
    def test_no_tailoring_no_issues(self) -> None:
        entity = _make_entity()
        issues = check_tailoring(
            entity, "some body", is_project_level=True, platform_parent=None
        )
        assert issues == []

    def test_override_without_target_is_violation(self) -> None:
        entity = _make_entity(override=True)
        issues = check_tailoring(
            entity, "some body", is_project_level=True, platform_parent=None
        )
        assert any(i.rule == "R-300-050" for i in issues)

    def test_platform_level_tailoring_rejected(self) -> None:
        entity = _make_entity(
            **{"tailoring-of": "R-100-001", "override": True}
        )
        issues = check_tailoring(
            entity,
            _RATIONALE_BODY,
            is_project_level=False,  # platform-level attempting to tailor
            platform_parent=_make_entity(id="R-100-001"),
        )
        assert any(i.rule == "R-300-051" for i in issues)

    def test_missing_override_is_violation(self) -> None:
        entity = _make_entity(**{"tailoring-of": "R-100-001"})
        parent = _make_entity(id="R-100-001")
        issues = check_tailoring(
            entity, _RATIONALE_BODY, is_project_level=True, platform_parent=parent
        )
        assert any("override: true" in i.message for i in issues)

    def test_missing_parent_is_violation(self) -> None:
        entity = _make_entity(
            **{"tailoring-of": "R-100-999", "override": True}
        )
        issues = check_tailoring(
            entity,
            _RATIONALE_BODY,
            is_project_level=True,
            platform_parent=None,
        )
        assert any("does not resolve" in i.message for i in issues)

    def test_deprecated_parent_is_violation(self) -> None:
        entity = _make_entity(
            **{"tailoring-of": "R-100-001", "override": True}
        )
        parent = _make_entity(
            id="R-100-001", status=RequirementStatus.DEPRECATED
        )
        issues = check_tailoring(
            entity, _RATIONALE_BODY, is_project_level=True, platform_parent=parent
        )
        assert any("deprecated" in i.message for i in issues)

    def test_missing_rationale_section_is_violation(self) -> None:
        entity = _make_entity(
            **{"tailoring-of": "R-100-001", "override": True}
        )
        parent = _make_entity(id="R-100-001")
        issues = check_tailoring(
            entity,
            "Some body without rationale",
            is_project_level=True,
            platform_parent=parent,
        )
        assert any("Tailoring rationale" in i.message for i in issues)

    def test_valid_tailoring_no_issues(self) -> None:
        entity = _make_entity(
            **{"tailoring-of": "R-100-001", "override": True}
        )
        parent = _make_entity(id="R-100-001")
        issues = check_tailoring(
            entity, _RATIONALE_BODY, is_project_level=True, platform_parent=parent
        )
        assert issues == []


@pytest.mark.unit
class TestDeprecatedReason:
    def test_deprecated_without_reason_flagged(self) -> None:
        entity = _make_entity(status=RequirementStatus.DEPRECATED)
        issues = check_deprecated_reason(entity)
        assert len(issues) == 1
        assert issues[0].rule == "R-M100-040"

    def test_deprecated_with_reason_ok(self) -> None:
        entity = _make_entity(
            status=RequirementStatus.DEPRECATED,
            **{"deprecated-reason": "replaced by R-300-501"},
        )
        assert check_deprecated_reason(entity) == []

    def test_draft_without_reason_ok(self) -> None:
        entity = _make_entity()
        assert check_deprecated_reason(entity) == []


@pytest.mark.unit
class TestRationaleExcerpt:
    def test_extracts_subsection_content(self) -> None:
        excerpt = rationale_excerpt(_RATIONALE_BODY)
        assert "project needs a stricter rate" in excerpt

    def test_respects_max_chars(self) -> None:
        body = "### Tailoring rationale\n\n" + ("x" * 1000)
        excerpt = rationale_excerpt(body, max_chars=50)
        assert len(excerpt) == 50

    def test_missing_section_returns_empty(self) -> None:
        assert rationale_excerpt("No rationale here") == ""
