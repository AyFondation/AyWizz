# =============================================================================
# File: test_models.py
# Version: 1
# Path: ay_platform_core/tests/unit/c5_requirements/test_models.py
# Description: Unit tests for C5 Pydantic models — validation of entity IDs,
#              references, closed-set enums, and frontmatter strictness
#              (R-300-005, R-M100-020, R-M100-041).
# =============================================================================

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ay_platform_core.c5_requirements.models import (
    DocumentCreate,
    DocumentFrontmatter,
    DocumentStatus,
    EntityCategory,
    EntityCreate,
    EntityFrontmatter,
    EntityType,
    RequirementStatus,
    is_valid_entity_id,
    is_valid_entity_reference,
)


@pytest.mark.unit
class TestEntityIdValidation:
    def test_typed_entity_ids_accepted(self) -> None:
        for eid in ("R-300-001", "T-600-042", "E-100-099", "Q-700-010"):
            assert is_valid_entity_id(eid)

    def test_decision_id_accepted(self) -> None:
        assert is_valid_entity_id("D-005")

    def test_meta_doc_range_accepted(self) -> None:
        assert is_valid_entity_id("R-M100-040")

    def test_invalid_ids_rejected(self) -> None:
        for bad in ("X-300-001", "R-30-001", "R-300-1", "r-300-001", "random"):
            assert not is_valid_entity_id(bad)

    def test_versioned_reference_accepted(self) -> None:
        assert is_valid_entity_reference("R-300-001@v3")
        assert is_valid_entity_reference("D-005@v1")

    def test_unversioned_reference_accepted(self) -> None:
        assert is_valid_entity_reference("R-300-001")


@pytest.mark.unit
class TestEntityFrontmatter:
    def test_minimal_valid(self) -> None:
        fm = EntityFrontmatter(
            id="R-300-001",
            version=1,
            status=RequirementStatus.DRAFT,
            category=EntityCategory.FUNCTIONAL,
        )
        assert fm.id == "R-300-001"
        assert fm.override is None

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EntityFrontmatter.model_validate({
                "id": "R-300-001",
                "version": 1,
                "status": "draft",
                "category": "functional",
                "unknown_field": "bad",
            })

    def test_invalid_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EntityFrontmatter(
                id="not-an-id",
                version=1,
                status=RequirementStatus.DRAFT,
                category=EntityCategory.FUNCTIONAL,
            )

    def test_alias_fields_parsed(self) -> None:
        fm = EntityFrontmatter.model_validate({
            "id": "R-300-001",
            "version": 1,
            "status": "draft",
            "category": "functional",
            "derives-from": ["D-005"],
            "tailoring-of": "R-100-001",
            "override": True,
            "superseded-by": "R-300-002",
            "deprecated-reason": "redundant",
        })
        assert fm.derives_from == ["D-005"]
        assert fm.tailoring_of == "R-100-001"
        assert fm.override is True
        assert fm.superseded_by == "R-300-002"

    def test_impacts_accepts_wildcards(self) -> None:
        fm = EntityFrontmatter(
            id="R-300-001",
            version=1,
            status=RequirementStatus.DRAFT,
            category=EntityCategory.FUNCTIONAL,
            impacts=["R-300-*", "T-600-017"],
        )
        assert "R-300-*" in fm.impacts

    def test_invalid_reference_in_derives_from_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EntityFrontmatter.model_validate({
                "id": "R-300-001",
                "version": 1,
                "status": "draft",
                "category": "functional",
                "derives-from": ["invalid-reference"],
            })


@pytest.mark.unit
class TestDocumentFrontmatter:
    def test_valid_document(self) -> None:
        fm = DocumentFrontmatter(
            document="300-SPEC-TEST",
            version=1,
            path="projects/p/requirements/300-SPEC-TEST.md",
            language="en",
            status=DocumentStatus.DRAFT,
        )
        assert fm.language == "en"

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DocumentFrontmatter.model_validate({
                "document": "300-SPEC-TEST",
                "version": 1,
                "path": "x",
                "language": "en",
                "status": "draft",
                "surprise": "rejected",
            })


@pytest.mark.unit
class TestRequestModels:
    def test_entity_create_valid(self) -> None:
        req = EntityCreate(
            entity_id="R-300-500",
            type=EntityType.R,
            category=EntityCategory.FUNCTIONAL,
            title="Example",
            body="Body text.",
        )
        assert req.status == RequirementStatus.DRAFT

    def test_entity_create_rejects_invalid_id(self) -> None:
        with pytest.raises(ValidationError):
            EntityCreate(
                entity_id="bogus",
                type=EntityType.R,
                category=EntityCategory.FUNCTIONAL,
                title="x",
                body="y",
            )

    def test_document_create_slug_validation(self) -> None:
        with pytest.raises(ValidationError):
            DocumentCreate(slug="bad slug here")
