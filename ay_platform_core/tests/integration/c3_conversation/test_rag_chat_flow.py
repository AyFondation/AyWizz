# =============================================================================
# File: test_rag_chat_flow.py
# Version: 1
# Path: ay_platform_core/tests/integration/c3_conversation/test_rag_chat_flow.py
# Description: Phase D integration tests — chat-with-RAG end-to-end.
#
#              Wires a full C3 + C7 + scripted-C8 stack against real
#              ArangoDB. Pre-indexes a small corpus in C7, then sends a
#              user message via POST /conversations/{id}/messages and
#              asserts:
#                1. The SSE stream completes with [DONE].
#                2. The retrieved chunk content is present in the
#                   prompt that the scripted LLM received (proves RAG
#                   augment ran).
#                3. The assistant reply is persisted in C3 history.
#                4. The fallback stub path runs when memory + llm are
#                   absent OR when the conversation has no project_id.
#
# @relation validates:R-100-003
# =============================================================================

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI, Header, HTTPException, Request

from ay_platform_core.c3_conversation.db.repository import ConversationRepository
from ay_platform_core.c3_conversation.models import (
    ConversationCreate,
    MessageRole,
)
from ay_platform_core.c3_conversation.router import router as c3_router
from ay_platform_core.c3_conversation.service import ConversationService
from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.db.repository import MemoryRepository
from ay_platform_core.c7_memory.embedding.deterministic import (
    DeterministicHashEmbedder,
)
from ay_platform_core.c7_memory.models import SourceIngestRequest
from ay_platform_core.c7_memory.service import MemoryService
from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.config import ClientSettings
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]


# ---------------------------------------------------------------------------
# Scripted LLM — captures every prompt + emits a canned streamed reply.
# ---------------------------------------------------------------------------


class _ScriptedLLM:
    """Inspectable LLM stub: stores every prompt seen and yields a
    fixed token sequence as the streamed completion."""

    def __init__(self, reply_tokens: list[str]) -> None:
        self.reply_tokens = reply_tokens
        self.prompts_seen: list[list[dict[str, Any]]] = []

    def latest_prompt_text(self) -> str:
        if not self.prompts_seen:
            return ""
        return "\n".join(m["content"] for m in self.prompts_seen[-1])


def _build_mock_llm_app(scripted: _ScriptedLLM) -> FastAPI:
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def completions(
        request: Request,
        x_agent_name: str | None = Header(default=None),
        x_session_id: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> Any:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="bearer required")
        if not x_agent_name or not x_session_id:
            raise HTTPException(status_code=400, detail="missing tags")
        body = await request.json()
        scripted.prompts_seen.append(body["messages"])

        if not body.get("stream"):
            raise HTTPException(status_code=400, detail="non-stream not used in RAG flow")

        async def _stream() -> AsyncIterator[bytes]:
            for tok in scripted.reply_tokens:
                event = {
                    "choices": [{"index": 0, "delta": {"content": tok}}],
                }
                yield f"data: {json.dumps(event)}\n\n".encode()
            yield b"data: [DONE]\n\n"

        from fastapi.responses import StreamingResponse  # noqa: PLC0415

        return StreamingResponse(_stream(), media_type="text/event-stream")

    return app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def rag_stack(
    arango_container: ArangoEndpoint,
) -> AsyncIterator[dict[str, Any]]:
    db_name = f"c3_rag_{uuid.uuid4().hex[:8]}"
    sys_db = ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password,
    )
    sys_db.create_database(db_name)

    arango = ArangoClient(hosts=arango_container.url)
    db = arango.db(db_name, username="root", password=arango_container.password)

    # C3
    c3_repo = ConversationRepository(db)
    c3_repo._ensure_collections_sync()

    # C7 (deterministic embedder for speed; chunk content is the data
    # we want to assert on, embedding quality doesn't matter here).
    c7_repo = MemoryRepository(db)
    c7_repo._ensure_collections_sync()
    c7_embedder = DeterministicHashEmbedder(dimension=64)
    c7_service = MemoryService(
        config=MemoryConfig(
            embedding_adapter="deterministic-hash",
            embedding_dimension=c7_embedder.dimension,
            chunk_token_size=64,
            chunk_overlap=8,
            default_quota_bytes=1024 * 1024 * 1024,
            retrieval_scan_cap=1000,
        ),
        repo=c7_repo,
        embedder=c7_embedder,
    )

    # Scripted C8
    scripted = _ScriptedLLM(reply_tokens=[
        "The ", "Voyager ", "1 ", "spacecraft ", "was ", "launched ",
        "in ", "1977.",
    ])
    mock_llm_app = _build_mock_llm_app(scripted)
    llm_transport = httpx.ASGITransport(app=mock_llm_app)
    llm_http = httpx.AsyncClient(transport=llm_transport, base_url="http://mock/v1")
    llm_client = LLMGatewayClient(
        ClientSettings(gateway_url="http://mock/v1"),
        bearer_token="rag-test-token",
        http_client=llm_http,
    )

    c3_service = ConversationService(
        c3_repo, memory_service=c7_service, llm_client=llm_client,
    )
    c3_app = FastAPI()
    c3_app.include_router(c3_router)
    c3_app.state.conversation_service = c3_service

    try:
        yield {
            "c3_app": c3_app,
            "c3_service": c3_service,
            "c3_repo": c3_repo,
            "c7_service": c7_service,
            "scripted": scripted,
            "llm_http": llm_http,
        }
    finally:
        await llm_http.aclose()
        cleanup_arango_database(arango_container, db_name)


@pytest.fixture(scope="function")
def stub_only_app(arango_container: ArangoEndpoint) -> Iterator[FastAPI]:
    """C3 wired WITHOUT memory + LLM, to exercise the stub fallback."""
    db_name = f"c3_stub_{uuid.uuid4().hex[:8]}"
    sys_db = ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password,
    )
    sys_db.create_database(db_name)
    db = ArangoClient(hosts=arango_container.url).db(
        db_name, username="root", password=arango_container.password,
    )
    repo = ConversationRepository(db)
    repo._ensure_collections_sync()
    service = ConversationService(repo)  # no memory, no llm
    app = FastAPI()
    app.include_router(c3_router)
    app.state.conversation_service = service
    try:
        yield app
    finally:
        cleanup_arango_database(arango_container, db_name)


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://e2e-rag",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_rag_flow_round_trip(rag_stack: dict[str, Any]) -> None:
    """End-to-end: pre-seed C7 with a source about Voyager 1, create a
    conversation in the project, send a message asking about it, assert
    the SSE stream contains the LLM reply AND the retrieval context made
    it to the LLM prompt."""
    c3_app: FastAPI = rag_stack["c3_app"]
    c7_service: MemoryService = rag_stack["c7_service"]
    c3_service: ConversationService = rag_stack["c3_service"]
    scripted: _ScriptedLLM = rag_stack["scripted"]

    tenant_id = "tenant-rag"
    project_id = "project-rag"
    user_id = "u-rag"

    # 1. Pre-seed C7 with a source containing the answer.
    await c7_service.ingest_source(
        SourceIngestRequest(
            source_id="src-voyager",
            project_id=project_id,
            mime_type="text/plain",
            content=(
                "The Voyager 1 spacecraft was launched on September 5, 1977 by NASA. "
                "It is the most distant human-made object from Earth."
            ),
            size_bytes=128,
            uploaded_by=user_id,
        ),
        tenant_id=tenant_id,
    )

    # 2. Create a conversation tied to the project.
    conv = await c3_service.create_conversation(
        user_id=user_id,
        payload=ConversationCreate(title="Voyager Q&A", project_id=project_id),
    )

    # 3. POST a message; consume the SSE stream.
    headers = {"X-User-Id": user_id, "X-Tenant-Id": tenant_id}
    async with _client(c3_app) as c, c.stream(
        "POST",
        f"/api/v1/conversations/{conv.id}/messages",
        headers=headers,
        json={"content": "When was Voyager 1 launched?"},
    ) as response:
        assert response.status_code == 200
        # Tokens (default `message` events with `data:` lines) end up
        # in `chunks` ; unified `event: inline` lines are tracked
        # separately. Was: a dedicated `event: stage` channel ; now
        # one `event: inline` channel carries every kind (the unified-
        # pipeline contract, 2026-05-19).
        chunks: list[str] = []
        inline_event_count = 0
        current_event_type = "message"
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                current_event_type = line[len("event: "):].strip()
            elif line.startswith("data: "):
                if current_event_type == "inline":
                    inline_event_count += 1
                else:
                    chunks.append(line[len("data: "):])
            elif line == "":
                # Blank line resets the event type per SSE spec.
                current_event_type = "message"

    # 4. Assert SSE framing — at least the [DONE] sentinel is present.
    assert "[DONE]" in chunks, f"no [DONE] sentinel in stream: {chunks}"
    # And that the unified `event: inline` channel emitted pipeline
    # activity (regression : the live inline log depends on it).
    assert inline_event_count >= 2, (
        f"expected ≥2 inline SSE events, got {inline_event_count}"
    )

    # 5. Reconstruct the assistant reply from the streamed tokens.
    reply_tokens = [c for c in chunks if c != "[DONE]"]
    full_reply = "".join(reply_tokens).strip()
    assert "Voyager" in full_reply
    assert "1977" in full_reply

    # 6. The scripted LLM saw a prompt; it MUST contain the retrieved
    #    chunk content (proves RAG augment fired).
    assert scripted.prompts_seen, "scripted LLM was never called"
    last_prompt = scripted.latest_prompt_text()
    assert "Voyager 1" in last_prompt, (
        "retrieved chunk did not make it to the LLM prompt"
    )
    assert "September 5, 1977" in last_prompt, (
        "retrieved chunk text not propagated"
    )

    # 7. Assistant reply persisted.
    messages = await c3_service.list_messages(conv.id, user_id)
    assert len(messages) == 2
    assert messages[0].role == MessageRole.USER
    assert messages[1].role == MessageRole.ASSISTANT
    assert "Voyager" in messages[1].content

    # 8. Inline-activity ledger persisted alongside the assistant
    #    message (regression for the chat-UX work : without this,
    #    navigating away and back loses the inline log). Pipeline
    #    phases now live in the UNIFIED `events` list as kind='stage'
    #    entries (was the dedicated `stages` field). At least
    #    `retrieve`, `generate`, `done` SHALL be present, each a
    #    `status='done'` row with a non-null `duration_ms`.
    assert messages[1].events is not None, "events were not persisted"
    stage_events = [e for e in messages[1].events if e.kind == "stage"]
    stage_names = [e.name for e in stage_events]
    assert "retrieve" in stage_names
    assert "generate" in stage_names
    assert "done" in stage_names
    for e in stage_events:
        assert e.status == "done", f"non-done stage persisted : {e}"
        assert e.duration_ms is not None, f"missing duration on {e.name}"
    # The user message has no inline events — they only attach to
    # assistant turns (server enforces by only persisting on the
    # assistant append_message call).
    assert messages[0].events is None


async def test_stub_fallback_when_llm_not_wired(
    stub_only_app: FastAPI,
) -> None:
    """When the service has no memory + LLM wired, sending a message
    SHALL stream the static stub reply and persist it."""
    user_id = "u-stub"
    tenant_id = "tenant-stub"

    # Create a conversation through the API (no project_id needed).
    headers = {"X-User-Id": user_id, "X-Tenant-Id": tenant_id}
    async with _client(stub_only_app) as c:
        create = await c.post(
            "/api/v1/conversations",
            headers=headers,
            json={"title": "stub-test"},
        )
        assert create.status_code == 201
        conv_id = create.json()["conversation"]["id"]

        async with c.stream(
            "POST",
            f"/api/v1/conversations/{conv_id}/messages",
            headers=headers,
            json={"content": "hello"},
        ) as response:
            assert response.status_code == 200
            full = []
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    full.append(line[len("data: "):])

    assert "[DONE]" in full
    joined = " ".join(full)
    assert "RAG" in joined or "not wired" in joined or "Your message" in joined


async def test_rag_skipped_when_conversation_has_no_project(
    rag_stack: dict[str, Any],
) -> None:
    """A conversation without a project_id SHALL fall back to the stub
    even though memory + llm are wired — RAG requires a project scope."""
    c3_app: FastAPI = rag_stack["c3_app"]
    c3_service: ConversationService = rag_stack["c3_service"]
    scripted: _ScriptedLLM = rag_stack["scripted"]
    user_id = "u-noproj"
    tenant_id = "tenant-noproj"

    conv = await c3_service.create_conversation(
        user_id=user_id,
        payload=ConversationCreate(title="No project"),  # no project_id
    )

    headers = {"X-User-Id": user_id, "X-Tenant-Id": tenant_id}
    prompts_before = len(scripted.prompts_seen)
    async with _client(c3_app) as c, c.stream(
        "POST",
        f"/api/v1/conversations/{conv.id}/messages",
        headers=headers,
        json={"content": "hello"},
    ) as response:
        assert response.status_code == 200
        async for _line in response.aiter_lines():
            pass

    # The LLM SHALL NOT have been called — the stub path skipped it.
    assert len(scripted.prompts_seen) == prompts_before, (
        "RAG fired despite missing project_id"
    )


# ---------------------------------------------------------------------------
# Phase E — Conversation memory loop
# ---------------------------------------------------------------------------


async def test_conversation_memory_loop_indexes_turns_in_c7(
    rag_stack: dict[str, Any],
) -> None:
    """Phase E: each user/assistant turn SHALL be indexed in C7's
    `CONVERSATIONS` index. After one turn we expect chunks with
    index='conversations' visible via direct repo scan."""
    c3_app: FastAPI = rag_stack["c3_app"]
    c3_service: ConversationService = rag_stack["c3_service"]
    c7_service: MemoryService = rag_stack["c7_service"]
    user_id = "u-memloop"
    tenant_id = "tenant-memloop"
    project_id = "project-memloop"

    conv = await c3_service.create_conversation(
        user_id=user_id,
        payload=ConversationCreate(title="memory-loop", project_id=project_id),
    )
    headers = {"X-User-Id": user_id, "X-Tenant-Id": tenant_id}
    async with _client(c3_app) as c, c.stream(
        "POST",
        f"/api/v1/conversations/{conv.id}/messages",
        headers=headers,
        json={"content": "Tell me about Voyager 1."},
    ) as response:
        assert response.status_code == 200
        async for _line in response.aiter_lines():
            pass

    # Direct AQL scan of memory_chunks for index=conversations.
    repo = c7_service._repo
    cursor = repo._db.aql.execute(
        "FOR c IN memory_chunks "
        "FILTER c.tenant_id == @tid AND c.project_id == @pid "
        "AND c.index == 'conversations' "
        "RETURN c",
        bind_vars={"tid": tenant_id, "pid": project_id},
    )
    rows = list(cursor)
    assert len(rows) > 0, (
        "no chunks indexed under CONVERSATIONS — memory loop didn't fire"
    )
    joined = " ".join(r["content"] for r in rows)
    # The user message + assistant reply (canned: "...Voyager 1...1977")
    # SHALL be present in the indexed turn.
    assert "Voyager" in joined
    assert "1977" in joined  # from the scripted assistant reply


async def test_followup_retrieves_prior_turn_context(
    rag_stack: dict[str, Any],
) -> None:
    """Multi-turn flow: turn 1 establishes a fact, turn 2's retrieval
    SHALL include the prior turn's chunk (proving the memory loop's
    second-order effect — the conversation answers itself)."""
    c3_app: FastAPI = rag_stack["c3_app"]
    c3_service: ConversationService = rag_stack["c3_service"]
    scripted: _ScriptedLLM = rag_stack["scripted"]
    user_id = "u-multiturn"
    tenant_id = "tenant-multiturn"
    project_id = "project-multiturn"

    # Re-script the LLM so turn 1 gives a distinctive reply that turn 2
    # can match against. Reset prompts_seen for clean assertion.
    scripted.reply_tokens = [
        "Marrakesh ", "is ", "a ", "Moroccan ", "city ",
        "famous ", "for ", "its ", "medina.",
    ]
    scripted.prompts_seen.clear()

    conv = await c3_service.create_conversation(
        user_id=user_id,
        payload=ConversationCreate(title="multi-turn", project_id=project_id),
    )
    headers = {"X-User-Id": user_id, "X-Tenant-Id": tenant_id}

    # ---- Turn 1 ----------------------------------------------------------
    async with _client(c3_app) as c, c.stream(
        "POST",
        f"/api/v1/conversations/{conv.id}/messages",
        headers=headers,
        json={"content": "Tell me about Marrakesh."},
    ) as response:
        async for _line in response.aiter_lines():
            pass

    # ---- Turn 2 ----------------------------------------------------------
    # Re-script: turn 2's reply doesn't matter for the assertion (we
    # check the PROMPT). Reset prompts_seen so we can target turn 2's.
    turn1_prompt_count = len(scripted.prompts_seen)
    scripted.reply_tokens = ["The ", "medina ", "of ", "Marrakesh."]

    async with _client(c3_app) as c, c.stream(
        "POST",
        f"/api/v1/conversations/{conv.id}/messages",
        headers=headers,
        json={"content": "What is Marrakesh famous for?"},
    ) as response:
        async for _line in response.aiter_lines():
            pass

    # The second prompt MUST exist and contain the assistant text from
    # turn 1 (which got indexed in CONVERSATIONS and retrieved here).
    assert len(scripted.prompts_seen) > turn1_prompt_count
    turn2_prompt_text = "\n".join(
        m["content"] for m in scripted.prompts_seen[turn1_prompt_count]
    )
    assert "medina" in turn2_prompt_text, (
        "turn 1 assistant reply (which mentioned 'medina') was not "
        "retrieved when turn 2 asked a follow-up question. The "
        "conversation memory loop did not propagate. Prompt was: "
        f"{turn2_prompt_text!r}"
    )
