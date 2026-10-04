# =============================================================================
# File: test_schemas.py
# Version: 1
# Path: ay_platform_core/tests/contract/c5_requirements/test_schemas.py
# Description: Contract tests — C5 public schemas are valid Pydantic models,
#              JSON-serialisable, no bare Any fields, and registered in the
#              contract registry with the expected consumer set.
# =============================================================================

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from ay_platform_core.c5_requirements.models import (
    DocumentPublic,
    EntityPublic,
    HistoryEntry,
    RelationEdge,
)
from tests.fixtures.contract_registry import find_by_producer


@pytest.mark.contract
class TestPublicSchemas:
    def test_entity_public_is_pydantic(self) -> None:
        assert issubclass(EntityPublic, BaseModel)

    def test_document_public_is_pydantic(self) -> None:
        assert issubclass(DocumentPublic, BaseModel)

    def test_relation_edge_is_pydantic(self) -> None:
        assert issubclass(RelationEdge, BaseModel)

    def test_history_entry_is_pydantic(self) -> None:
        assert issubclass(HistoryEntry, BaseModel)

    def test_no_bare_any_on_public_models(self) -> None:
        for model in (EntityPublic, DocumentPublic, HistoryEntry, RelationEdge):
            for name, info in model.model_fields.items():
                assert info.annotation is not Any, (
                    f"{model.__name__}.{name} has bare Any annotation"
                )


@pytest.mark.contract
class TestContractRegistration:
    # Inventory of C5's exposed contract surface. Extending it is a
    # deliberate act: `test_no_unexpected_contracts` fails until a newly
    # registered contract is listed here, which is how an unnoticed surface
    # growth is caught. The object-grain contracts below were added with
    # 310-SPEC-DOC-TRACEABILITY §4.1 (increment 1).
    EXPECTED: ClassVar[set[str]] = {
        "EntityPublic",
        "DocumentPublic",
        "HistoryEntry",
        "RelationEdge",
        "DocObjectPublic",
        "WorkingDraft",
        "ObjectLock",
        "CyclePublic",
        "WorkflowPublic",
        "Allocation",
        "CoverageLink",
        "RequirementCoverage",
        "ContainerCoverage",
        "SpeculativeMarking",
        "SuppliedRequirement",
        "QualityFinding",
        "Fragment",
        "SplitProposal",
        "ReworkRequest",
        # Change absorption (§4.8, increment 5). ImpactEdge / ImpactNode /
        # ImpactSet are declared in `traversal.py`, not `models.py`: they are
        # both the walk's output and the stored, exposed shape, and one
        # definition is what §8.4 asks for.
        "ImpactEdge",
        "ImpactNode",
        "ImpactSet",
        "QualificationProposal",
        "Disposition",
        "ChangeTicket",
        "ClosureReport",
        # Negotiated piloting (§4.9, increment 6).
        "StepEstimate",
        "PlanStep",
        "Ratification",
        "TreatmentPlan",
        "TreatmentReport",
        # Baselines (§4.11, increment 8). ManifestObject / ManifestLink are
        # deliberately distinct from DocObjectPublic / CoverageLink: a
        # historical record needs a schema independent of the live model it
        # photographed (R-310-204). See the registry for the full reason.
        "ManifestObject",
        "ManifestLink",
        "BaselineManifest",
        "BaselineReadiness",
    }

    def test_all_expected_contracts_registered(self) -> None:
        registered = {c.name for c in find_by_producer("C5_requirements")}
        missing = self.EXPECTED - registered
        assert not missing, f"Missing C5 contracts: {missing}"

    def test_no_unexpected_contracts(self) -> None:
        registered = {c.name for c in find_by_producer("C5_requirements")}
        extra = registered - self.EXPECTED
        assert not extra, f"Unexpected C5 contracts: {extra}"

    def test_entity_public_consumed_by_downstream_components(self) -> None:
        for contract in find_by_producer("C5_requirements"):
            if contract.name == "EntityPublic":
                assert "C4_orchestrator" in contract.consumers
                assert "C6_validation" in contract.consumers
                assert "C1_gateway" in contract.consumers
                return
        pytest.fail("EntityPublic not found in registry")

    def test_all_contracts_have_consumers(self) -> None:
        for contract in find_by_producer("C5_requirements"):
            assert contract.consumers, (
                f"{contract.name} has no declared consumers"
            )

    def test_all_contracts_use_rest_transport(self) -> None:
        for contract in find_by_producer("C5_requirements"):
            assert contract.transport == "rest", (
                f"{contract.name} uses transport {contract.transport!r}; "
                "expected 'rest'"
            )
