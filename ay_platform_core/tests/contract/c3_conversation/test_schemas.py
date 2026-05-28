# =============================================================================
# File: test_schemas.py
# Version: 1
# Path: ay_platform_core/tests/contract/c3_conversation/test_schemas.py
# Description: Contract tests — C3 public schemas are valid Pydantic models,
#              JSON-serialisable, no bare Any fields, and registered in the
#              contract registry.
# =============================================================================

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from ay_platform_core.c3_conversation.models import (
    ConversationPublic,
    MessagePublic,
    MessageRole,
)
from tests.fixtures.contract_registry import get_registry


@pytest.mark.contract
class TestConversationPublicSchema:
    def test_is_pydantic_base_model(self) -> None:
        assert issubclass(ConversationPublic, BaseModel)

    def test_json_schema_valid(self) -> None:
        schema = ConversationPublic.model_json_schema()
        assert "properties" in schema
        for field in ("id", "owner_id", "title", "created_at", "updated_at"):
            assert field in schema["properties"]

    def test_no_bare_any_fields(self) -> None:
        for name, info in ConversationPublic.model_fields.items():
            assert info.annotation is not Any, f"Field '{name}' has bare Any annotation"


@pytest.mark.contract
class TestMessagePublicSchema:
    def test_is_pydantic_base_model(self) -> None:
        assert issubclass(MessagePublic, BaseModel)

    def test_json_schema_valid(self) -> None:
        schema = MessagePublic.model_json_schema()
        assert "properties" in schema
        for field in ("id", "conversation_id", "role", "content", "timestamp"):
            assert field in schema["properties"]

    def test_role_field_is_enum(self) -> None:
        info = MessagePublic.model_fields["role"]
        assert info.annotation is MessageRole


@pytest.mark.contract
class TestContractRegistration:
    EXPECTED: ClassVar[set[str]] = {"ConversationPublic", "MessagePublic"}

    def test_contracts_registered(self) -> None:
        registry = get_registry()
        c3_contracts = {c.name for c in registry if c.producer == "C3_conversation"}
        missing = self.EXPECTED - c3_contracts
        assert not missing, f"Missing C3 contracts in registry: {missing}"

    def test_contracts_have_consumers(self) -> None:
        registry = get_registry()
        for c in registry:
            if c.producer == "C3_conversation":
                assert c.consumers, f"Contract {c.name} has no declared consumers"
