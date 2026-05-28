# =============================================================================
# File: test_endpoint_contracts.py
# Version: 2
# Path: ay_platform_core/tests/contract/c2_auth/test_endpoint_contracts.py
# Description: Contract tests — C2 contracts registered in contract_registry.
# =============================================================================

from __future__ import annotations

from typing import ClassVar

import pytest
from pydantic import BaseModel

from tests.fixtures.contract_registry import find_by_producer, get_registry


@pytest.mark.contract
class TestC2ContractRegistry:
    """Verify C2 Auth Service contracts are correctly registered."""

    C2_PRODUCER = "C2_auth"
    EXPECTED_CONTRACT_NAMES: ClassVar[set[str]] = {
        "JWTClaims", "LoginRequest", "TokenResponse", "UserPublic"
    }

    def test_c2_contracts_present(self) -> None:
        registry = get_registry()
        producers = {c.producer for c in registry}
        assert self.C2_PRODUCER in producers, "C2_auth contracts not registered"

    def test_all_expected_contracts_registered(self) -> None:
        contracts = find_by_producer(self.C2_PRODUCER)
        names = {c.name for c in contracts}
        missing = self.EXPECTED_CONTRACT_NAMES - names
        assert not missing, f"Missing C2 contracts: {missing}"

    def test_no_unexpected_contracts(self) -> None:
        contracts = find_by_producer(self.C2_PRODUCER)
        names = {c.name for c in contracts}
        extra = names - self.EXPECTED_CONTRACT_NAMES
        assert not extra, f"Unexpected C2 contracts: {extra}"

    def test_jwt_claims_has_correct_consumers(self) -> None:
        contracts = find_by_producer(self.C2_PRODUCER)
        jwt_contract = next((c for c in contracts if c.name == "JWTClaims"), None)
        assert jwt_contract is not None
        assert "C1_gateway" in jwt_contract.consumers
        assert "C3_conversation" in jwt_contract.consumers
        assert "C4_orchestrator" in jwt_contract.consumers

    def test_all_contracts_use_rest_transport(self) -> None:
        contracts = find_by_producer(self.C2_PRODUCER)
        for c in contracts:
            assert c.transport == "rest", (
                f"Contract {c.name} uses unexpected transport {c.transport!r}"
            )

    def test_all_contracts_have_pydantic_schema(self) -> None:
        contracts = find_by_producer(self.C2_PRODUCER)
        for c in contracts:
            assert issubclass(c.schema, BaseModel), (
                f"Contract {c.name} schema is not a Pydantic BaseModel"
            )

    def test_all_contracts_have_description(self) -> None:
        contracts = find_by_producer(self.C2_PRODUCER)
        for c in contracts:
            assert c.description, f"Contract {c.name} has no description"
