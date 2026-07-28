# =============================================================================
# File: test_project_status_enforcement.py
# Version: 1
# Path: ay_platform_core/tests/unit/c3_conversation/test_project_status_enforcement.py
# Description: Unit tests for C3 record-derived project lifecycle enforcement
#              (E-100-002 v4, inc3b). A conversation bound to a non-`active`
#              project must refuse content access: `inactive` blocks reads AND
#              writes, `archived` is a read-only freeze (writes blocked, reads
#              allowed). This is the companion to the C2 `/verify` forward-auth
#              check, which cannot see a conversation's project (no
#              `{project_id}` in the URL).
#
# @relation validates:E-100-002
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from ay_platform_core.c3_conversation.models import ConversationCreate
from ay_platform_core.c3_conversation.service import ConversationService

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

USER = "user-a"


def _conv_doc(project_id: str | None) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "id": str(uuid4()),
        "owner_id": USER,
        "project_id": project_id,
        "title": "T",
        "created_at": now,
        "updated_at": now,
        "message_count": 0,
        "deleted": False,
    }


def _repo(status_value: str | None, *, project_id: str | None = "proj-x") -> MagicMock:
    repo = MagicMock()
    repo.get_conversation = AsyncMock(return_value=_conv_doc(project_id))
    repo.create_conversation = AsyncMock(return_value=_conv_doc(project_id))
    repo.list_messages = AsyncMock(return_value=[])
    repo.get_project_status = AsyncMock(return_value=status_value)
    return repo


async def test_read_blocked_on_inactive_project() -> None:
    svc = ConversationService(_repo("inactive"))
    with pytest.raises(HTTPException) as exc:
        await svc.get_conversation(uuid4(), USER)
    assert exc.value.status_code == 403
    assert "inactive" in exc.value.detail


async def test_read_allowed_on_archived_project() -> None:
    # `archived` is a read-only freeze — reads still pass.
    svc = ConversationService(_repo("archived"))
    result = await svc.get_conversation(uuid4(), USER)
    assert result.owner_id == USER


async def test_list_messages_blocked_on_inactive_project() -> None:
    svc = ConversationService(_repo("inactive"))
    with pytest.raises(HTTPException) as exc:
        await svc.list_messages(uuid4(), USER)
    assert exc.value.status_code == 403


async def test_create_blocked_on_archived_project() -> None:
    # Creating content is a write → refused on a read-only-frozen project.
    svc = ConversationService(_repo("archived"))
    with pytest.raises(HTTPException) as exc:
        await svc.create_conversation(USER, ConversationCreate(title="X", project_id="proj-x"))
    assert exc.value.status_code == 403
    assert "archived" in exc.value.detail


async def test_create_blocked_on_inactive_project() -> None:
    svc = ConversationService(_repo("inactive"))
    with pytest.raises(HTTPException) as exc:
        await svc.create_conversation(USER, ConversationCreate(title="X", project_id="proj-x"))
    assert exc.value.status_code == 403


async def test_access_allowed_on_active_project() -> None:
    svc = ConversationService(_repo("active"))
    result = await svc.get_conversation(uuid4(), USER)
    assert result.owner_id == USER


async def test_unknown_status_skips_enforcement() -> None:
    # Status None (project absent / C2 not co-located) → never blocked.
    svc = ConversationService(_repo(None))
    result = await svc.get_conversation(uuid4(), USER)
    assert result.owner_id == USER


async def test_conversation_without_project_skips_enforcement() -> None:
    # A conversation with no project_id is not project content — the status
    # probe must not even be consulted.
    repo = _repo("inactive", project_id=None)
    svc = ConversationService(repo)
    result = await svc.get_conversation(uuid4(), USER)
    assert result.owner_id == USER
    repo.get_project_status.assert_not_awaited()
