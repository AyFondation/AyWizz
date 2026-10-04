# =============================================================================
# File: test_object_schemas.py
# Version: 1
# Path: ay_platform_core/tests/contract/c5_requirements/test_object_schemas.py
# Description: Contract tests for the object-grain document model
#              (310-SPEC-DOC-TRACEABILITY §4.1, E-310-001). Asserts wire
#              stability of the enum vocabularies, JSON round-trip fidelity,
#              registry declaration, and that the public projection does not
#              leak storage-side fields.
#
# @relation validates:R-310-006
# @relation validates:R-310-007
# @relation validates:R-310-010
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from ay_platform_core.c5_requirements.objects.models import (
    DocObject,
    DocObjectPublic,
    HolderKind,
    ObjectLock,
    ObjectType,
    ProducedBy,
    ReviewDecision,
    ReviewRecord,
    ReviewState,
    WorkingDraft,
)
from tests.fixtures.contract_registry import find_by_producer

NOW = datetime(2026, 9, 30, 10, 0, 0, tzinfo=UTC)

_OBJECT_MODELS: tuple[type[BaseModel], ...] = (
    DocObject,
    DocObjectPublic,
    WorkingDraft,
    ObjectLock,
    ReviewRecord,
    ProducedBy,
)


def _object() -> DocObject:
    return DocObject(
        object_id="OBJ-1120",
        project_id="adas-brake-ctrl",
        container="030-ARCHITECTURE-DESIGN",
        type=ObjectType.PARAGRAPH,
        body="The primary actuator shall deliver up to 140 Nm.",
        ordinal=3,
        version=12,
        review_state=ReviewState.STALE,
        last_review=ReviewRecord(
            decision=ReviewDecision.ACCEPT, actor="o.mathieu", at=NOW
        ),
        produced_by=ProducedBy(workflow="WF-002", workflow_version=3),
        cycle_version=4,
        created_at=NOW,
        created_by="agent:architect",
        updated_at=NOW,
        updated_by="o.mathieu",
    )


@pytest.mark.contract
class TestObjectSchemas:
    def test_all_are_pydantic_models(self) -> None:
        for model in _OBJECT_MODELS:
            assert issubclass(model, BaseModel), model

    def test_no_bare_any_on_object_models(self) -> None:
        for model in _OBJECT_MODELS:
            for name, info in model.model_fields.items():
                assert info.annotation is not Any, (
                    f"{model.__name__}.{name} has bare Any annotation"
                )

    def test_object_json_round_trip_is_lossless(self) -> None:
        obj = _object()
        restored = DocObject.model_validate_json(obj.model_dump_json())
        assert restored == obj

    def test_public_json_round_trip_is_lossless(self) -> None:
        pub = DocObjectPublic.from_object(_object(), has_working_draft=True)
        restored = DocObjectPublic.model_validate_json(pub.model_dump_json())
        assert restored == pub

    def test_lock_json_round_trip_is_lossless(self) -> None:
        lock = ObjectLock(
            object_id="OBJ-1124",
            holder="agent:architect",
            holder_kind=HolderKind.AGENT,
            acquired_at=NOW,
            expires_at=NOW + timedelta(minutes=15),
        )
        assert ObjectLock.model_validate_json(lock.model_dump_json()) == lock


@pytest.mark.contract
class TestWireVocabularyStability:
    """The wire values are the contract; renaming one breaks every consumer."""

    def test_object_type_values(self) -> None:
        assert {t.value for t in ObjectType} == {
            "heading",
            "paragraph",
            "list",
            "table",
            "figure",
        }

    def test_review_state_values(self) -> None:
        # R-310-007: `auto-accepted` is a distinct wire value, never folded
        # into `accepted` — a coverage figure must be able to separate them.
        assert {s.value for s in ReviewState} == {
            "proposed",
            "accepted",
            "auto-accepted",
            "stale",
            "rejected",
        }

    def test_review_decision_values(self) -> None:
        assert {d.value for d in ReviewDecision} == {
            "accept",
            "auto-accept",
            "reject",
            "confirm-unchanged",
        }

    def test_holder_kind_values(self) -> None:
        assert {h.value for h in HolderKind} == {"human", "agent"}

    def test_review_state_serialises_as_its_wire_value(self) -> None:
        payload = DocObjectPublic.from_object(_object()).model_dump(mode="json")
        assert payload["review_state"] == "stale"


@pytest.mark.contract
class TestSchemaIsolation:
    def test_public_projection_withholds_storage_fields(self) -> None:
        # project_id is a routing/storage concern; the REST surface scopes by
        # path, so exposing it here would duplicate it on the wire.
        public_fields = set(DocObjectPublic.model_fields)
        assert "project_id" not in public_fields

    def test_public_projection_carries_the_review_decision(self) -> None:
        # R-310-010: the decision that produced a version is part of the
        # contract, not an internal detail — consumers render attribution.
        assert "last_review" in DocObjectPublic.model_fields


@pytest.mark.contract
class TestContractRegistration:
    EXPECTED: ClassVar[set[str]] = {
        "DocObjectPublic",
        "WorkingDraft",
        "ObjectLock",
    }

    def test_all_object_contracts_registered(self) -> None:
        registered = {c.name for c in find_by_producer("C5_requirements")}
        missing = self.EXPECTED - registered
        assert not missing, f"Missing C5 object contracts: {missing}"

    def test_object_contracts_are_rest_exposed(self) -> None:
        for c in find_by_producer("C5_requirements"):
            if c.name in self.EXPECTED:
                assert c.transport == "rest", c.name

    def test_storage_shape_is_not_a_registered_contract(self) -> None:
        # DocObject is the MinIO source-of-truth shape (R-310-001) with no
        # cross-component consumer today. Registering it would declare a
        # consumer relationship that does not exist; it is registered when
        # C6 actually imports it, not before.
        registered = {c.name for c in find_by_producer("C5_requirements")}
        assert "DocObject" not in registered
