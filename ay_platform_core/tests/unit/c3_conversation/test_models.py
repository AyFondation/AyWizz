# =============================================================================
# File: test_models.py
# Version: 1
# Path: ay_platform_core/tests/unit/c3_conversation/test_models.py
# Description: Unit tests for C3 Pydantic models — serialisation, defaults,
#              field constraints.
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ay_platform_core.c3_conversation.models import (
    ConversationCreate,
    ConversationPublic,
    ConversationUpdate,
    MessagePublic,
    MessageRequest,
    MessageRole,
)


@pytest.mark.unit
class TestConversationPublic:
    def test_round_trip(self) -> None:
        now = datetime.now(UTC)
        c = ConversationPublic(
            id=uuid4(),
            owner_id="u1",
            project_id=None,
            title="Test",
            created_at=now,
            updated_at=now,
            message_count=5,
        )
        data = c.model_dump()
        assert data["owner_id"] == "u1"
        assert data["message_count"] == 5
        assert data["project_id"] is None

    def test_json_serialisable(self) -> None:
        now = datetime.now(UTC)
        c = ConversationPublic(
            id=uuid4(), owner_id="u1", title="x",
            created_at=now, updated_at=now,
        )
        s = c.model_dump_json()
        assert "owner_id" in s


@pytest.mark.unit
class TestMessagePublic:
    def test_role_enum_values(self) -> None:
        assert MessageRole.USER.value == "user"
        assert MessageRole.ASSISTANT.value == "assistant"

    def test_round_trip(self) -> None:
        m = MessagePublic(
            id=uuid4(),
            conversation_id=uuid4(),
            role=MessageRole.USER,
            content="hello",
            timestamp=datetime.now(UTC),
        )
        assert m.role == MessageRole.USER


@pytest.mark.unit
class TestConversationCreate:
    def test_default_title(self) -> None:
        c = ConversationCreate()
        assert c.title == "New Conversation"

    def test_custom_title(self) -> None:
        c = ConversationCreate(title="My project chat")
        assert c.title == "My project chat"

    def test_title_max_length(self) -> None:
        with pytest.raises(ValidationError):
            ConversationCreate(title="x" * 256)


@pytest.mark.unit
class TestConversationUpdate:
    def test_all_optional(self) -> None:
        u = ConversationUpdate()
        assert u.title is None
        assert u.project_id is None


@pytest.mark.unit
class TestMessageRequest:
    def test_content_required(self) -> None:
        with pytest.raises(ValidationError):
            MessageRequest(content="")

    def test_valid_content(self) -> None:
        m = MessageRequest(content="hello")
        assert m.content == "hello"
