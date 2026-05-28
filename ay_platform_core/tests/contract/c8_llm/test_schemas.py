# =============================================================================
# File: test_schemas.py
# Version: 1
# Path: ay_platform_core/tests/contract/c8_llm/test_schemas.py
# Description: Contract tests — C8 public schemas (ChatCompletionRequest,
#              ChatCompletionResponse, CostSummary, BudgetStatus,
#              CallRecord) are valid Pydantic models and registered with
#              the expected consumer set.
# =============================================================================

from __future__ import annotations

from typing import ClassVar

import pytest
from pydantic import BaseModel

from ay_platform_core.c8_llm.models import (
    BudgetStatus,
    CallRecord,
    ChatCompletionRequest,
    ChatCompletionResponse,
    CostSummary,
)
from tests.fixtures.contract_registry import find_by_producer


@pytest.mark.contract
class TestPublicSchemas:
    def test_chat_request_is_pydantic(self) -> None:
        assert issubclass(ChatCompletionRequest, BaseModel)

    def test_chat_response_is_pydantic(self) -> None:
        assert issubclass(ChatCompletionResponse, BaseModel)

    def test_cost_summary_is_pydantic(self) -> None:
        assert issubclass(CostSummary, BaseModel)

    def test_budget_status_is_pydantic(self) -> None:
        assert issubclass(BudgetStatus, BaseModel)

    def test_call_record_is_pydantic(self) -> None:
        assert issubclass(CallRecord, BaseModel)


@pytest.mark.contract
class TestContractRegistration:
    EXPECTED: ClassVar[set[str]] = {
        "ChatCompletionRequest",
        "ChatCompletionResponse",
        "CostSummary",
        "BudgetStatus",
        "CallRecord",
    }

    def test_all_expected_contracts_registered(self) -> None:
        registered = {c.name for c in find_by_producer("C8_llm")}
        missing = self.EXPECTED - registered
        assert not missing, f"Missing C8 contracts: {missing}"

    def test_no_unexpected_contracts(self) -> None:
        registered = {c.name for c in find_by_producer("C8_llm")}
        extra = registered - self.EXPECTED
        assert not extra, f"Unexpected C8 contracts: {extra}"

    def test_chat_request_consumed_by_llm_calling_components(self) -> None:
        for contract in find_by_producer("C8_llm"):
            if contract.name == "ChatCompletionRequest":
                # Every LLM-calling component SHOULD consume the request schema
                assert "C3_conversation" in contract.consumers
                assert "C4_orchestrator" in contract.consumers
                return
        pytest.fail("ChatCompletionRequest not found")

    def test_all_contracts_have_consumers(self) -> None:
        for contract in find_by_producer("C8_llm"):
            assert contract.consumers, f"{contract.name} has no consumers"

    def test_all_contracts_use_rest_transport(self) -> None:
        for contract in find_by_producer("C8_llm"):
            assert contract.transport == "rest"
