# =============================================================================
# File: test_service.py
# Version: 2
# Path: ay_platform_core/tests/unit/c3_conversation/test_service.py
# Description: Unit tests for ConversationService — mocked repository.
#              Covers CRUD access control, soft-delete, SSE generation,
#              and expert-mode stub.
#
#              v2: regression coverage for the chat-UX work — prompt
#              assembly switches between the RAG-augmented variant
#              and the general-knowledge fallback based on
#              `has_relevant_hits`, and threads the optional user /
#              project behavioural preambles in the user-mandated
#              order.
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from ay_platform_core.c3_conversation.models import (
    ConversationCreate,
    ConversationUpdate,
)
from ay_platform_core.c3_conversation.service import ConversationService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

USER_A = "user-a"
USER_B = "user-b"


def _make_conv_doc(owner_id: str = USER_A, deleted: bool = False) -> dict[str, Any]:
    conv_id = uuid4()
    now = datetime.now(UTC).isoformat()
    return {
        "id": str(conv_id),
        "owner_id": owner_id,
        "project_id": None,
        "title": "Test conversation",
        "created_at": now,
        "updated_at": now,
        "message_count": 0,
        "deleted": deleted,
    }


def _make_msg_doc(conv_id: UUID) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "conversation_id": str(conv_id),
        "role": "user",
        "content": "hello",
        "timestamp": datetime.now(UTC).isoformat(),
    }


def _make_repo(**overrides: Any) -> MagicMock:
    repo = MagicMock()
    repo.list_conversations = AsyncMock(return_value=[])
    repo.create_conversation = AsyncMock(return_value=_make_conv_doc())
    repo.get_conversation = AsyncMock(return_value=None)
    repo.update_conversation = AsyncMock(return_value=None)
    repo.soft_delete_conversation = AsyncMock(return_value=None)
    repo.list_messages = AsyncMock(return_value=[])
    repo.append_message = AsyncMock(return_value=_make_msg_doc(uuid4()))
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


# ---------------------------------------------------------------------------
# list_conversations
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_conversations_returns_public_models() -> None:
    doc = _make_conv_doc()
    repo = _make_repo(list_conversations=AsyncMock(return_value=[doc]))
    svc = ConversationService(repo)
    result = await svc.list_conversations(USER_A)
    assert len(result) == 1
    assert str(result[0].id) == doc["id"]
    assert result[0].owner_id == USER_A


# ---------------------------------------------------------------------------
# create_conversation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_conversation() -> None:
    doc = _make_conv_doc()
    repo = _make_repo(create_conversation=AsyncMock(return_value=doc))
    svc = ConversationService(repo)
    result = await svc.create_conversation(USER_A, ConversationCreate(title="My conv"))
    assert result.title == doc["title"]
    assert result.owner_id == USER_A


# ---------------------------------------------------------------------------
# get_conversation — access control
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_conversation_owner_allowed() -> None:
    doc = _make_conv_doc(owner_id=USER_A)
    repo = _make_repo(get_conversation=AsyncMock(return_value=doc))
    svc = ConversationService(repo)
    result = await svc.get_conversation(UUID(doc["id"]), USER_A)
    assert str(result.id) == doc["id"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_conversation_other_user_forbidden() -> None:
    doc = _make_conv_doc(owner_id=USER_A)
    repo = _make_repo(get_conversation=AsyncMock(return_value=doc))
    svc = ConversationService(repo)
    with pytest.raises(HTTPException) as exc_info:
        await svc.get_conversation(UUID(doc["id"]), USER_B)
    assert exc_info.value.status_code == 403


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_conversation_not_found_raises_404() -> None:
    repo = _make_repo(get_conversation=AsyncMock(return_value=None))
    svc = ConversationService(repo)
    with pytest.raises(HTTPException) as exc_info:
        await svc.get_conversation(uuid4(), USER_A)
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# update_conversation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_conversation_title() -> None:
    doc = _make_conv_doc(owner_id=USER_A)
    updated = {**doc, "title": "New title"}
    repo = _make_repo(
        get_conversation=AsyncMock(return_value=doc),
        update_conversation=AsyncMock(return_value=updated),
    )
    svc = ConversationService(repo)
    result = await svc.update_conversation(
        UUID(doc["id"]), USER_A, ConversationUpdate(title="New title")
    )
    assert result.title == "New title"


# ---------------------------------------------------------------------------
# delete_conversation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_conversation_calls_soft_delete() -> None:
    doc = _make_conv_doc(owner_id=USER_A)
    soft_delete = AsyncMock()
    repo = _make_repo(
        get_conversation=AsyncMock(return_value=doc),
        soft_delete_conversation=soft_delete,
    )
    svc = ConversationService(repo)
    await svc.delete_conversation(UUID(doc["id"]), USER_A)
    soft_delete.assert_called_once_with(UUID(doc["id"]))


# ---------------------------------------------------------------------------
# send_message_stream (SSE)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_message_stream_yields_sse_chunks() -> None:
    doc = _make_conv_doc(owner_id=USER_A)
    repo = _make_repo(get_conversation=AsyncMock(return_value=doc))
    svc = ConversationService(repo)
    stream = await svc.send_message_stream(UUID(doc["id"]), USER_A, "hello")
    chunks: list[str] = []
    async for chunk in stream:
        chunks.append(chunk)
    assert any(c.startswith("data: ") for c in chunks)
    assert chunks[-1] == "data: [DONE]\n\n"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_message_stream_persists_user_message() -> None:
    doc = _make_conv_doc(owner_id=USER_A)
    append = AsyncMock(return_value=_make_msg_doc(UUID(doc["id"])))
    repo = _make_repo(
        get_conversation=AsyncMock(return_value=doc),
        append_message=append,
    )
    svc = ConversationService(repo)
    stream = await svc.send_message_stream(UUID(doc["id"]), USER_A, "hello")
    async for _ in stream:
        pass
    # First call: user message
    first_call = append.call_args_list[0]
    assert first_call.kwargs["role"] == "user"
    assert first_call.kwargs["content"] == "hello"


# ---------------------------------------------------------------------------
# expert_mode_stream (NATS stub)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_expert_mode_stream_yields_unavailable_event() -> None:
    chunks: list[str] = []
    async for chunk in ConversationService.expert_mode_stream():
        chunks.append(chunk)
    assert len(chunks) == 1
    assert '"type":"unavailable"' in chunks[0]


# ---------------------------------------------------------------------------
# Prompt assembly — RAG vs general-knowledge fallback + user/project preambles
# ---------------------------------------------------------------------------
# These are pure functions ; we import them directly to keep the tests fast
# (no service / repo / mocks needed).


from ay_platform_core.c3_conversation.service import (  # noqa: E402
    _assemble_system_prompt,
)


@pytest.mark.unit
def test_prompt_uses_rag_variant_when_relevant_hits() -> None:
    """A non-zero `has_relevant_hits` SHALL select the RAG-augmented
    prompt — recognisable by the "Retrieved context" framing + the
    citation instructions."""
    prompt = _assemble_system_prompt(
        project_id="proj-x",
        context="[1] (source=A, score=0.812)\nHello world.",
        user_prompt=None,
        project_prompt=None,
        has_relevant_hits=True,
    )
    assert "Retrieved context" in prompt
    assert "cite which excerpt" in prompt
    assert "proj-x" in prompt


@pytest.mark.unit
def test_prompt_drops_retrieved_context_when_no_relevant_hits() -> None:
    """When no chunk cleared the relevance threshold, the prompt
    SHALL switch to the general-knowledge variant — no "Retrieved
    context" block at all, just an "answer from general knowledge"
    instruction. This is the fix for the small-model hallucination
    pattern where qwen2.5:3b confabulated bridges between an
    irrelevant context block and the user's question."""
    prompt = _assemble_system_prompt(
        project_id="proj-x",
        # `context` content is irrelevant when has_relevant_hits=False
        # — verify the function ignores it entirely.
        context="(no excerpts above the relevance threshold)",
        user_prompt=None,
        project_prompt=None,
        has_relevant_hits=False,
    )
    assert "Retrieved context" not in prompt
    assert "cite which excerpt" not in prompt
    assert "general knowledge" in prompt
    assert "proj-x" in prompt


@pytest.mark.unit
def test_prompt_prepends_user_and_project_behavioural_preambles() -> None:
    """User + project behavioural prompts SHALL be prepended in that
    EXACT order, ahead of the RAG section. Order matters : E-100-002
    has the user prompt outrank the project prompt, and both outrank
    the RAG instructions."""
    prompt = _assemble_system_prompt(
        project_id="proj-x",
        context="[1] (source=A, score=0.8)\nSome context.",
        user_prompt="Be concise.",
        project_prompt="Answer in formal French.",
        has_relevant_hits=True,
    )
    # The two preambles appear above the RAG section, in user-then-
    # project order.
    user_pos = prompt.index("Be concise.")
    project_pos = prompt.index("Answer in formal French.")
    rag_pos = prompt.index("Retrieved context")
    assert user_pos < project_pos < rag_pos


@pytest.mark.unit
def test_prompt_drops_empty_behavioural_preambles() -> None:
    """Empty / whitespace-only / None preambles SHALL be skipped so
    the LLM doesn't see dangling section headers."""
    prompt = _assemble_system_prompt(
        project_id="proj-x",
        context="[1] (source=A, score=0.8)\nSomething.",
        user_prompt="   ",
        project_prompt=None,
        has_relevant_hits=True,
    )
    assert "[User instructions]" not in prompt
    assert "[Project instructions]" not in prompt


@pytest.mark.unit
def test_prompt_omits_docgen_directive_by_default() -> None:
    """Non-DocGen conversations (no tool loop) SHALL NOT see the
    document-tool directive — it would confuse a plain RAG chat with
    instructions about tools it isn't offered."""
    prompt = _assemble_system_prompt(
        project_id="proj-x",
        context="[1] (source=A, score=0.8)\nSomething.",
        user_prompt=None,
        project_prompt=None,
        has_relevant_hits=True,
    )
    assert "Document tools — MANDATORY protocol" not in prompt


@pytest.mark.unit
def test_prompt_injects_docgen_directive_when_tools_active() -> None:
    """When the chat-direct DocGen tool loop is active the system
    prompt SHALL carry the mandatory tool-usage directive so the
    model persists via update_document instead of printing the
    edited content as plain text (Phase 2.C.3 defect fix). The
    directive SHALL sit AFTER the behavioural preambles and BEFORE
    the RAG context (operational mechanics, not behaviour)."""
    prompt = _assemble_system_prompt(
        project_id="proj-x",
        context="[1] (source=A, score=0.8)\nSome context.",
        user_prompt="Be concise.",
        project_prompt="Formal French.",
        has_relevant_hits=True,
        docgen_tools_active=True,
    )
    assert "Document tools — MANDATORY protocol" in prompt
    # The directive must explicitly forbid the observed failure modes
    # (2026-05-19 hardened version) : prose-instead-of-tool, the
    # fenced-content "as if that saved it" pattern, placeholder
    # values, and stopping after read_document. Assert the invariants
    # that express the requirement, not the exact prose (which was
    # intentionally rewritten — §10.4 test update for a contract/
    # content change).
    assert "ABSOLUTELY FORBIDDEN" in prompt
    assert "code block" in prompt
    assert "update_document" in prompt
    assert "read_document or update_document tool" in prompt
    directive_pos = prompt.index("Document tools — MANDATORY protocol")
    project_pos = prompt.index("Formal French.")
    rag_pos = prompt.index("Retrieved context")
    assert project_pos < directive_pos < rag_pos


# ---------------------------------------------------------------------------
# _tool_result_path — drives the SSE `done` payload `path` field that the
# Conversations inline strip uses to deep-link the Working area viewer
# (D-015 / Phase 2.C.3). Pure function, imported directly.


from ay_platform_core.c3_conversation.service import (  # noqa: E402
    _tool_result_path,
)


@pytest.mark.unit
def test_tool_result_path_create_reads_created_path() -> None:
    """create_document result nests the path under `created` — the
    helper SHALL surface it so the UI can open the new doc."""
    assert (
        _tool_result_path("create_document", {"created": {"path": "docs/a.md"}})
        == "docs/a.md"
    )


@pytest.mark.unit
def test_tool_result_path_update_reads_updated_path() -> None:
    """update_document nests the path under `updated`."""
    assert (
        _tool_result_path("update_document", {"updated": {"path": "docs/b.md"}})
        == "docs/b.md"
    )


@pytest.mark.unit
def test_tool_result_path_delete_reads_deleted_string() -> None:
    """delete_document returns the path as a bare `deleted` string
    (mirrors `_summarise_tool_result`'s `result.get('deleted')`)."""
    assert _tool_result_path("delete_document", {"deleted": "docs/c.md"}) == "docs/c.md"


@pytest.mark.unit
def test_tool_result_path_non_mutating_tool_returns_none() -> None:
    """Read / list tools have no affected path — no deep-link."""
    assert _tool_result_path("read_document", {"path": "docs/x.md"}) is None
    assert _tool_result_path("list_documents", {"documents": []}) is None


@pytest.mark.unit
def test_tool_result_path_error_result_returns_none() -> None:
    """An errored tool result SHALL NOT yield a path — the UI would
    otherwise render a deep-link to a document that was never written."""
    assert _tool_result_path("create_document", {"error": "boom"}) is None


@pytest.mark.unit
def test_tool_result_path_malformed_result_returns_none() -> None:
    """Missing / non-string path → None (UI omits the link rather
    than building a broken href)."""
    assert _tool_result_path("create_document", {"created": {}}) is None
    assert _tool_result_path("update_document", {}) is None
    assert _tool_result_path("delete_document", {"deleted": 123}) is None
