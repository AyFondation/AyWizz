# =============================================================================
# File: service.py
# Version: 14
# Path: ay_platform_core/src/ay_platform_core/c3_conversation/service.py
# Description: C3 Conversation Service facade.
#              Orchestrates CRUD and the SSE message-send flow.
#
#              v14 (2026-05-21): the tool_call `done` event also carries
#              the resulting document `version` (from C4's DocumentRef)
#              so the chat can render a versioned "Open in working area
#              (vN)" link below the response (#5 / R-200-147).
#
#              v13 (2026-05-21): the tool_call `done` inline event now
#              carries the (size-capped) call `arguments` so the UI
#              inline log can expand each tool call into its chain-of-
#              thought detail (#4). `_safe_tool_args` truncates large
#              string values (e.g. document `content`) to a preview so
#              the persisted events ledger never duplicates whole docs.
#
#              v12 (2026-05-21): generate one `response_turn_id` per AI
#              response (up-front, before the tool loop) and forward it
#              to every document tool call. C4 embeds it in the live-docs
#              commit message so the tree's per-file version batches by
#              response — N writes to a file in one turn = one version
#              bump (D-015 / R-200-147).
#
#              v11 (2026-05-19): hardened `_DOCGEN_TOOL_DIRECTIVE` —
#              step-numbered modify workflow + an explicit FORBIDDEN
#              list (no fenced-content "as if saved", no
#              "save the file"/"specify" asks, no [placeholder]
#              values, no stop-after-read). qwen2.5-coder:7b still
#              answered modify requests in prose under the softer v8
#              directive (no tool call at all).
#
#              v10 (2026-05-19): UNIFIED inline-event pipeline. The
#              separate `event: stage` / `event: tool_call` SSE
#              channels and the `collected_stages` / `collected_tool_
#              calls` lists collapse into one `event: inline` channel
#              and one `collected_events` audit ledger persisted as
#              `MessagePublic.events`. `_inline_sse` is the single
#              emitter ; `_legacy_stage_to_inline` projects pre-
#              unification persisted `stages` at read time (no data
#              migration). One formatter registry renders them UI-side.
#
#              v9 (2026-05-18): tool-loop observability — one INFO
#              line per round (parsed tool names) and a truncated
#              content preview when a round produced NO tool call
#              (the qwen 'claims success in prose without calling
#              update_document' failure mode, Phase 2.C.3 diagnosis).
#
#              v8 (2026-05-18): inject `_DOCGEN_TOOL_DIRECTIVE` into
#              the system prompt when the chat-direct DocGen tool
#              loop is active. Without it small models (qwen2.5:3b)
#              read a document then print the edited content as a
#              plain-text answer instead of calling update_document,
#              so the file was never persisted (Phase 2.C.3 defect).
#
#              v7 (2026-05-18): the `event: tool_call` done payload
#              now carries an optional `path` for mutating tools
#              (create / update / delete_document) so the UI can
#              deep-link the inline strip to the Working area viewer
#              (D-015 / Phase 2.C.3). `_tool_result_path` helper.
#
#              v6 (2026-05-16): chat-direct DocGen tool loop
#              (D-015 / Phase 2.C.2). When a `DocumentToolClient` is
#              wired, the RAG flow runs a bounded NON-streaming
#              tool-execution loop before the final answer : the LLM
#              is offered the document tools, each `tool_calls` round
#              is executed against C4 and fed back, and the resolved
#              final assistant text is emitted over the existing SSE
#              channel. New named SSE event `event: tool_call`.
#
#              v5: when retrieval surfaces no chunk above the
#              relevance threshold, the system prompt switches to the
#              `_GENERAL_SYSTEM_PROMPT` variant (no "Retrieved context"
#              block). Small models otherwise tried to reconcile their
#              answer with an empty/irrelevant context and hallucinated
#              bridges (e.g. "Paris as capital of the Hispanic kingdom"
#              for "capitale de l'Espagne").
#
#              v4: the pipeline timeline emitted as SSE `event: stage`
#              payloads is now also collected in-process and persisted
#              alongside the assistant message (`stages` field).
#              `_msg_doc_to_public` reads it back so a navigation /
#              refresh re-renders the same chip + collapsible panel
#              the operator saw live. Only `done` events are stored
#              (the `running` intermediate signals exist only for the
#              live UX).
#
#              v3: `send_message_stream` accepts optional `user_prompt`
#              and `project_prompt` (resolved by C2 and forwarded by the
#              UX). When present, they are prepended in order ahead of
#              the RAG retrieval section in the assembled system
#              message, so they outrank any RAG/context instructions.
#
#              v2 (Phase D of v1 plan): the message-send flow now does
#              RAG when the conversation has a project_id AND the
#              service was wired with a MemoryService + LLMGatewayClient.
#              Pipeline: persist user message → C7 retrieve top-K
#              chunks → build (system + context + history + user)
#              prompt → C8 streaming chat completion → SSE → persist
#              assistant reply. Wiring is opt-in: tests/components
#              that don't need RAG can pass `memory_service=None` and
#              `llm_client=None` and the original stub fallback runs.
# @relation R-100-003 R-100-074 D-008
# =============================================================================

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import UUID, uuid4

from fastapi import HTTPException, status

from ay_platform_core.c3_conversation.db.repository import ConversationRepository
from ay_platform_core.c3_conversation.document_tools import (
    DOC_TOOLS,
    DocumentToolClient,
    parse_tool_calls,
)
from ay_platform_core.c3_conversation.models import (
    ConversationCreate,
    ConversationInternal,
    ConversationPublic,
    ConversationUpdate,
    MessagePublic,
    MessageRole,
    PromptReference,
)
from ay_platform_core.c7_memory.models import IndexKind, RetrievalRequest
from ay_platform_core.c7_memory.remote import RemoteMemoryService
from ay_platform_core.c7_memory.service import MemoryService
from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.models import ChatCompletionRequest, ChatMessage, ChatRole

_log = logging.getLogger("c3_conversation.tool_loop")
"""Observability for the chat-direct DocGen tool loop. INFO-level :
one line per round (parsed tool names) + a truncated preview of the
model's text when a round produced NO tool call (the canonical
'qwen answered in prose instead of calling update_document' failure
mode — Phase 2.C.3). Permanent observability, not throwaway debug."""


def _doc_to_public(doc: dict[str, Any]) -> ConversationPublic:
    return ConversationPublic(
        id=UUID(doc["id"]),
        owner_id=doc["owner_id"],
        project_id=doc.get("project_id"),
        title=doc["title"],
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
        message_count=doc.get("message_count", 0),
    )


def _doc_to_internal(doc: dict[str, Any]) -> ConversationInternal:
    return ConversationInternal(
        id=UUID(doc["id"]),
        owner_id=doc["owner_id"],
        project_id=doc.get("project_id"),
        title=doc["title"],
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
        message_count=doc.get("message_count", 0),
        deleted=doc.get("deleted", False),
    )


def _inline_sse(payload: dict[str, Any]) -> str:
    """Serialise one inline-activity event onto the single unified SSE
    channel (`event: inline`). Every kind (stage / tool_call /
    future) travels here ; the client dispatches on `payload['kind']`
    via its formatter registry. Replaces the former per-kind
    `event: stage` / `event: tool_call` channels."""
    return (
        "event: inline\n"
        f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
    )


def _legacy_stage_to_inline(stage: dict[str, Any]) -> dict[str, Any]:
    """Project a v3-era persisted `stages[]` entry into the unified
    `InlineEvent` shape (kind='stage'). Read-time shim so messages
    written before the unification still render — no data migration."""
    return {
        "kind": "stage",
        "label": stage.get("label", stage.get("name", "stage")),
        "status": stage.get("status", "done"),
        "name": stage.get("name"),
        "duration_ms": stage.get("duration_ms"),
        "stats": stage.get("stats"),
    }


def _assemble_chat_messages(
    *,
    system_content: str,
    history_msgs: list[ChatMessage],
    reference_blocks: list[str] | None,
    user_message: str,
) -> list[ChatMessage]:
    """Compose the final LLM message list.

    Order : (system) ; recent history ; optional <reference> system
    block per R-200-181 ; user message. Keeping this out of
    `_rag_stream._generate` keeps that closure under ruff's
    `PLR0915` ceiling AND makes the prompt assembly independently
    testable when the unit-test suite gets there.
    """
    out: list[ChatMessage] = [
        ChatMessage(role=ChatRole.SYSTEM, content=system_content),
        *history_msgs,
    ]
    if reference_blocks:
        ref_system = (
            "The operator has pinned the following references to this "
            "turn — treat them as authoritative context for the user's "
            "question :\n\n" + "\n\n".join(reference_blocks)
        )
        out.append(ChatMessage(role=ChatRole.SYSTEM, content=ref_system))
    out.append(ChatMessage(role=ChatRole.USER, content=user_message))
    return out


def _msg_doc_to_public(doc: dict[str, Any]) -> MessagePublic:
    # `events` is optional — absent on user messages. Pydantic
    # validates per-item when present ; a corrupt entry raises here
    # (caller surfaces a 500 ; better than silently dropping audit
    # data). Legacy docs predate `events` and only carry the v3
    # `stages` field — project them so old conversations still render.
    raw_events = doc.get("events")
    if not raw_events:
        legacy_stages = doc.get("stages")
        raw_events = (
            [_legacy_stage_to_inline(s) for s in legacy_stages]
            if legacy_stages
            else None
        )
    raw_refs = doc.get("references")
    return MessagePublic(
        id=UUID(doc["id"]),
        conversation_id=UUID(doc["conversation_id"]),
        role=MessageRole(doc["role"]),
        content=doc["content"],
        timestamp=doc["timestamp"],
        events=raw_events if raw_events else None,
        references=raw_refs if raw_refs else None,
    )


class ConversationService:
    def __init__(
        self,
        repo: ConversationRepository,
        *,
        memory_service: MemoryService | RemoteMemoryService | None = None,
        llm_client: LLMGatewayClient | None = None,
        document_tools: DocumentToolClient | None = None,
        rag_top_k: int = 5,
        rag_history_turns: int = 6,
        max_tool_rounds: int = 6,
    ) -> None:
        self._repo = repo
        # Chat-direct DocGen tool client (D-015). None → tool loop
        # disabled, plain RAG streaming chat (legacy v5 behaviour).
        self._doc_tools = document_tools
        self._max_tool_rounds = max_tool_rounds
        # Phase D wiring — optional. When BOTH are provided AND the
        # conversation has a project_id, `send_message_stream` runs the
        # RAG pipeline; otherwise it falls back to the static stub.
        # `MemoryService` is the in-process variant (test stack);
        # `RemoteMemoryService` is the HTTP variant (K8s production).
        # Both expose the same retrieve()/ingest_conversation_turn()
        # surface — ConversationService is agnostic to the choice.
        self._memory = memory_service
        self._llm = llm_client
        self._rag_top_k = rag_top_k
        self._rag_history_turns = rag_history_turns

    # ------------------------------------------------------------------
    # Access guard
    # ------------------------------------------------------------------

    async def _require_access(
        self, conversation_id: UUID, user_id: str
    ) -> dict[str, Any]:
        doc = await self._repo.get_conversation(conversation_id)
        if doc is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )
        if doc["owner_id"] != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return doc

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def list_conversations(self, user_id: str) -> list[ConversationPublic]:
        docs = await self._repo.list_conversations(user_id)
        return [_doc_to_public(d) for d in docs]

    async def create_conversation(
        self, user_id: str, payload: ConversationCreate
    ) -> ConversationPublic:
        doc = await self._repo.create_conversation(
            owner_id=user_id,
            title=payload.title,
            project_id=payload.project_id,
        )
        return _doc_to_public(doc)

    async def get_conversation(
        self, conversation_id: UUID, user_id: str
    ) -> ConversationPublic:
        doc = await self._require_access(conversation_id, user_id)
        return _doc_to_public(doc)

    async def update_conversation(
        self, conversation_id: UUID, user_id: str, payload: ConversationUpdate
    ) -> ConversationPublic:
        await self._require_access(conversation_id, user_id)
        updates: dict[str, Any] = {}
        if payload.title is not None:
            updates["title"] = payload.title
        if payload.project_id is not None:
            updates["project_id"] = payload.project_id
        doc = await self._repo.update_conversation(conversation_id, updates)
        if doc is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )
        return _doc_to_public(doc)

    async def delete_conversation(
        self, conversation_id: UUID, user_id: str
    ) -> None:
        await self._require_access(conversation_id, user_id)
        await self._repo.soft_delete_conversation(conversation_id)

    async def list_messages(
        self, conversation_id: UUID, user_id: str
    ) -> list[MessagePublic]:
        await self._require_access(conversation_id, user_id)
        docs = await self._repo.list_messages(conversation_id)
        return [_msg_doc_to_public(d) for d in docs]

    # ------------------------------------------------------------------
    # Message send → SSE stream (C4 stub)
    # ------------------------------------------------------------------

    async def send_message_stream(
        self,
        conversation_id: UUID,
        user_id: str,
        content: str,
        *,
        tenant_id: str | None = None,
        user_roles: str = "project_editor",
        user_prompt: str | None = None,
        project_prompt: str | None = None,
        references: list[PromptReference] | None = None,
    ) -> AsyncIterator[str]:
        """Persist the user message and yield SSE chunks for the
        assistant reply.

        Two paths:

        - **RAG path** (Phase D): when this service has been wired with
          BOTH a `MemoryService` and an `LLMGatewayClient`, AND the
          conversation has a `project_id`, AND a `tenant_id` was
          propagated from the request, retrieve the top-K relevant
          chunks from C7, build a (system + context + history + user)
          prompt, and stream the LLM response from C8.
        - **Stub path** (legacy): if any wire is missing, fall back to
          the deterministic placeholder reply that earlier versions
          shipped — preserves backward compat with tests that don't
          care about the LLM.
        """
        conv = await self._require_access(conversation_id, user_id)

        # Tranche B (R-200-180..184) — resolve prompt-attached references
        # NOW, BEFORE persisting the user message, so an RBAC 403 or a
        # 413 token-cap reject prevents the message landing in history
        # without its references. Atomic-at-the-request-level.
        project_id_local = conv.get("project_id")
        resolved_ref_blocks: list[str] = []
        if references:
            if self._doc_tools is None or not project_id_local or not tenant_id:
                # Per R-200-183 atomicity: rather than silently drop,
                # 503 makes the wiring gap explicit.
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=(
                        "references require live-docs wiring (project + "
                        "tenant + DocumentToolClient) — not configured"
                    ),
                )
            resolved_ref_blocks = await self._resolve_references(
                references=references,
                project_id=project_id_local,
                tenant_id=tenant_id,
                user_id=user_id,
                user_roles=user_roles,
            )

        # Persist user message FIRST so a downstream LLM failure still
        # leaves the user's input in the history.
        await self._repo.append_message(
            conversation_id=conversation_id,
            role=MessageRole.USER,
            content=content,
            references=(
                [r.model_dump() for r in references] if references else None
            ),
        )

        project_id = conv.get("project_id")
        if (
            self._memory is not None
            and self._llm is not None
            and project_id
            and tenant_id
        ):
            return self._rag_stream(
                conversation_id=conversation_id,
                project_id=project_id,
                tenant_id=tenant_id,
                user_id=user_id,
                user_roles=user_roles,
                user_message=content,
                user_prompt=user_prompt,
                project_prompt=project_prompt,
                reference_blocks=resolved_ref_blocks,
            )
        return self._stub_stream(conversation_id)

    # ------------------------------------------------------------------
    # Prompt-attached references resolver (R-200-180..184)
    # ------------------------------------------------------------------

    _PROMPT_REF_TOKEN_CAP: ClassVar[int] = 32_000
    """Approximate token cap per R-200-181 — 4 chars/token heuristic in
    v1. Switches to a real C8 tokenizer when one becomes available
    (Q-500-005)."""

    async def _resolve_references(
        self,
        *,
        references: list[PromptReference],
        project_id: str,
        tenant_id: str,
        user_id: str,
        user_roles: str,
    ) -> list[str]:
        """Fetch each reference's content from C4, slice excerpts by
        line range, accumulate <reference> blocks under the 32K-token
        approximate cap. Raises HTTPException 403 on a reference whose
        resolution fails RBAC (atomic per R-200-183) and 413 when the
        combined content would exceed the cap (R-200-181)."""
        assert self._doc_tools is not None  # narrowed by caller
        blocks: list[str] = []
        total_chars = 0
        char_cap = self._PROMPT_REF_TOKEN_CAP * 4
        for idx, ref in enumerate(references):
            if ref.source != "live-docs":
                # Q-200-019 : `source` references are deferred (run_id
                # ambiguity). Reject explicitly rather than silently drop.
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"reference[{idx}] source 'source' not supported in v1 "
                        "(Q-200-019)"
                    ),
                )
            sc, body = await self._doc_tools.read_document_content(
                project_id=project_id,
                path=ref.path,
                user_id=user_id,
                tenant_id=tenant_id,
                user_roles=user_roles,
            )
            if sc == 403:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        f"reference[{idx}] {ref.path!r} resolution forbidden "
                        "(RBAC denied access to underlying resource)"
                    ),
                )
            if sc == 404:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"reference[{idx}] {ref.path!r} not found in live-docs"
                    ),
                )
            if sc != 200:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=(
                        f"reference[{idx}] resolution failed: C4 HTTP {sc}"
                    ),
                )
            content = body
            range_attr = ""
            if ref.kind == "excerpt" and ref.range is not None:
                lines = content.splitlines()
                start_idx = max(0, ref.range.start_line - 1)
                end_idx = min(len(lines), ref.range.end_line)
                content = "\n".join(lines[start_idx:end_idx])
                range_attr = (
                    f' lines="{ref.range.start_line}-{ref.range.end_line}"'
                )
            block = (
                f'<reference path="{ref.path}" kind="{ref.kind}"'
                f'{range_attr}>\n{content}\n</reference>'
            )
            total_chars += len(block)
            if total_chars > char_cap:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=(
                        "combined reference content exceeds the 32K-token "
                        "approximate cap (R-200-181) — drop or shorten "
                        f"references[{idx}:] and retry"
                    ),
                )
            blocks.append(block)
        return blocks

    def _stub_stream(self, conversation_id: UUID) -> AsyncIterator[str]:
        stub_reply = (
            "RAG / LLM not wired in this deployment. "
            "Your message has been saved; configure C7 + C8 to enable "
            "automatic responses."
        )

        async def _generate() -> AsyncIterator[str]:
            for word in stub_reply.split():
                yield f"data: {word} \n\n"
                await asyncio.sleep(0)
            yield "data: [DONE]\n\n"
            await self._repo.append_message(
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content=stub_reply,
            )

        return _generate()

    def _rag_stream(
        self,
        *,
        conversation_id: UUID,
        project_id: str,
        tenant_id: str,
        user_id: str,
        user_roles: str,
        user_message: str,
        user_prompt: str | None,
        project_prompt: str | None,
        reference_blocks: list[str] | None = None,
    ) -> AsyncIterator[str]:
        async def _generate() -> AsyncIterator[str]:
            # The SSE protocol mixes two event kinds :
            #   - default `message` events (no `event:` line) carry the
            #     streamed LLM tokens — legacy clients keep working
            #     unchanged because that's exactly the v1 contract.
            #   - named `event: inline` events carry JSON describing
            #     ANY in-turn activity, discriminated by `kind`
            #     ('stage' = pipeline progress, 'tool_call' = DocGen
            #     tool, future kinds). One unified channel ; the client
            #     dispatches via its formatter registry. Old clients
            #     ignore them (per SSE spec: unknown event types fall
            #     through to the default `message` listener which our
            #     token parser ignores because the data is JSON).
            t_start = time.perf_counter()

            # Unified inline-activity ledger. Every `done` event (any
            # kind) is appended here and persisted with the assistant
            # message so the UX re-renders the exact same inline log
            # on navigation / reload, and so the list is the queryable
            # audit trail of the turn. `running` events are live-only
            # progress signals, never stored.
            collected_events: list[dict[str, Any]] = []

            def _emit(
                *,
                name: str,
                status: str,
                label: str,
                duration_ms: int | None = None,
                stats: dict[str, Any] | None = None,
            ) -> str:
                # Pipeline-stage events on the unified channel
                # (kind='stage'). Thin wrapper kept so the ~5 stage
                # call sites stay readable.
                payload: dict[str, Any] = {
                    "kind": "stage",
                    "name": name,
                    "status": status,
                    "label": label,
                }
                if duration_ms is not None:
                    payload["duration_ms"] = duration_ms
                if stats is not None:
                    payload["stats"] = stats
                if status == "done":
                    collected_events.append(payload)
                return _inline_sse(payload)

            # 1. Retrieval phase.
            assert self._memory is not None  # narrowed in caller
            assert self._llm is not None
            t_retrieve = time.perf_counter()
            yield _emit(
                name="retrieve",
                status="running",
                label="Searching project sources",
            )
            retrieval = await self._memory.retrieve(
                RetrievalRequest(
                    project_id=project_id,
                    query=user_message,
                    indexes=[
                        IndexKind.EXTERNAL_SOURCES,
                        IndexKind.CONVERSATIONS,
                    ],
                    top_k=self._rag_top_k,
                ),
                tenant_id=tenant_id,
                user_id=user_id,
                user_roles=user_roles,
            )
            retrieve_ms = int((time.perf_counter() - t_retrieve) * 1000)
            hits_total, hits_relevant, top_score = _hit_stats(retrieval.hits)
            context_block = _format_retrieved_chunks(retrieval.hits)
            yield _emit(
                name="retrieve",
                status="done",
                label=(
                    f"{hits_relevant}/{hits_total} relevant chunk"
                    f"{'s' if hits_relevant != 1 else ''}"
                ),
                duration_ms=retrieve_ms,
                stats={
                    "hits_total": hits_total,
                    "hits_relevant": hits_relevant,
                    "top_score": top_score,
                },
            )

            # 2. Build prompt: (optional user_prompt + project_prompt
            #    preamble) + RAG system instructions + recent history
            #    + user.
            history_msgs = await self._recent_history_messages(
                conversation_id, exclude_id=None,
            )
            system_content = _assemble_system_prompt(
                project_id=project_id,
                context=context_block,
                user_prompt=user_prompt,
                project_prompt=project_prompt,
                # No relevant hits → switch to the general-knowledge
                # variant which drops the "Retrieved context" framing
                # entirely. Small models confabulate when given an
                # empty/irrelevant context block.
                has_relevant_hits=hits_relevant > 0,
                # DocGen mode → inject the mandatory tool-usage
                # directive so the model persists via update_document
                # instead of printing the edited content as text.
                docgen_tools_active=self._doc_tools is not None,
            )
            messages = _assemble_chat_messages(
                system_content=system_content,
                history_msgs=history_msgs,
                reference_blocks=reference_blocks,
                user_message=user_message,
            )

            # 3. Generation phase.
            t_generate = time.perf_counter()
            yield _emit(
                name="generate",
                status="running",
                label="Asking the language model",
            )
            collected_tokens: list[str] = []
            # One id per AI response, generated up-front (the tool loop
            # runs before the assistant message is persisted). C4 embeds
            # it in each live-docs commit so the tree's per-file version
            # batches by response — every write this turn shares the id,
            # so N writes to a file collapse to one version bump.
            response_turn_id = str(uuid4())

            if self._doc_tools is not None:
                # 3a. Chat-direct DocGen tool loop (D-015 / Phase 2.C.2).
                #     Bounded NON-streaming rounds : offer the document
                #     tools, execute each `tool_calls` against C4, feed
                #     results back, loop until the model answers in
                #     plain text. The final text is emitted in one SSE
                #     `data:` chunk (tool turns take seconds anyway ;
                #     streaming the final answer would need a second
                #     LLM call — deferred).
                async for sse in self._run_tool_loop(
                    messages=messages,
                    conversation_id=conversation_id,
                    project_id=project_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    user_roles=user_roles,
                    collected_tokens=collected_tokens,
                    collected_events=collected_events,
                    turn_id=response_turn_id,
                ):
                    yield sse
            else:
                # 3b. Legacy streaming RAG path (no tools). Each chunk
                #     is an OpenAI SSE event ; we extract
                #     `choices[0].delta.content` and re-emit as a plain
                #     SSE message event.
                request = ChatCompletionRequest(messages=messages, stream=True)
                async with self._llm.chat_completion_stream(
                    request,
                    agent_name="c3-rag",
                    session_id=str(conversation_id),
                    tenant_id=tenant_id,
                    project_id=project_id,
                ) as chunks:
                    async for chunk in chunks:
                        delta = _extract_delta_content(chunk)
                        if delta:
                            collected_tokens.append(delta)
                            # SSE escape: replace any embedded newlines
                            # so they don't break the SSE framing.
                            safe = delta.replace("\n", "\\n")
                            yield f"data: {safe}\n\n"

            generate_ms = int((time.perf_counter() - t_generate) * 1000)
            total_tokens = sum(len(t) for t in collected_tokens)  # char count
            yield _emit(
                name="generate",
                status="done",
                label=f"{total_tokens} chars generated",
                duration_ms=generate_ms,
                stats={"chars": total_tokens, "deltas": len(collected_tokens)},
            )

            total_ms = int((time.perf_counter() - t_start) * 1000)
            yield _emit(
                name="done",
                status="done",
                label="Reply ready",
                duration_ms=total_ms,
            )
            yield "data: [DONE]\n\n"

            # 4. Persist the assistant reply along with the pipeline
            #    timeline so a navigation / refresh re-renders the same
            #    chip + collapsible panel the operator saw live.
            full_reply = "".join(collected_tokens).strip()
            if full_reply:
                assistant_msg = await self._repo.append_message(
                    conversation_id=conversation_id,
                    role=MessageRole.ASSISTANT,
                    content=full_reply,
                    events=collected_events or None,
                )
                # 5. Phase E — feed the turn back into C7 so follow-up
                #    questions can retrieve it. Best-effort: a memory
                #    failure here SHALL NOT break the user-facing reply
                #    (the SSE stream has already emitted [DONE]).
                turn_id = (
                    str(assistant_msg["id"])
                    if isinstance(assistant_msg, dict) and "id" in assistant_msg
                    else f"t-{datetime.now(UTC).isoformat()}"
                )
                # Conversation memory loop is opportunistic; quota /
                # embedder hiccups SHALL NOT propagate to the caller
                # whose SSE stream has already closed with [DONE].
                with contextlib.suppress(Exception):
                    await self._memory.ingest_conversation_turn(
                        tenant_id=tenant_id,
                        project_id=project_id,
                        conversation_id=str(conversation_id),
                        turn_id=turn_id,
                        user_message=user_message,
                        assistant_reply=full_reply,
                        actor_id=str(conversation_id),
                        # Remote variant raises NotImplementedError here
                        # in v1; the suppress() catches it so chat
                        # streaming is unaffected.
                        user_id=user_id,
                        user_roles=user_roles,
                    )

        return _generate()

    async def _run_tool_loop(
        self,
        *,
        messages: list[ChatMessage],
        conversation_id: UUID,
        project_id: str,
        tenant_id: str,
        user_id: str,
        user_roles: str,
        collected_tokens: list[str],
        collected_events: list[dict[str, Any]],
        turn_id: str | None = None,
    ) -> AsyncIterator[str]:
        """Bounded non-streaming tool-execution loop (D-015). Yields
        SSE strings : one unified `event: inline` (kind='tool_call')
        per executed tool (running + done) plus one final `data:`
        chunk with the model's plain-text answer. Appends the answer
        to `collected_tokens` and each terminal tool event to
        `collected_events` (the shared audit ledger persisted with the
        assistant message). Mutates `messages` in place across rounds
        (assistant tool_call turn + tool result turns), mirroring the
        OpenAI function-calling protocol."""
        assert self._llm is not None
        assert self._doc_tools is not None

        for round_idx in range(self._max_tool_rounds):
            request = ChatCompletionRequest(
                messages=messages,
                stream=False,
                tools=DOC_TOOLS,
            )
            try:
                resp = await self._llm.chat_completion(
                    request,
                    agent_name="c3-docgen",
                    session_id=str(conversation_id),
                    tenant_id=tenant_id,
                    project_id=project_id,
                )
            except Exception as exc:  # transport / gateway failure
                msg = f"(LLM gateway error during tool loop: {exc})"
                collected_tokens.append(msg)
                yield f"data: {msg}\\n\n"
                return

            choice = resp.choices[0] if resp.choices else None
            message = choice.message if choice is not None else None
            tool_calls = parse_tool_calls(message) if message is not None else []

            raw_content = ""
            if message is not None and isinstance(message.content, str):
                raw_content = message.content
            if tool_calls:
                _log.info(
                    "tool_loop round=%d conv=%s parsed=%d tools=%s",
                    round_idx + 1,
                    conversation_id,
                    len(tool_calls),
                    [tc.get("name") for tc in tool_calls],
                )
            else:
                # No tool call this round → the model answered in prose.
                # Log a preview : this is exactly where the qwen
                # 'claims success without calling update_document'
                # failure surfaces, so the raw text is the evidence.
                _log.info(
                    "tool_loop round=%d conv=%s NO_TOOL_CALL "
                    "content_preview=%r",
                    round_idx + 1,
                    conversation_id,
                    raw_content[:600],
                )

            if not tool_calls:
                # Final answer — the model chose to respond in text.
                content = raw_content.strip() or "(the assistant returned no text)"
                collected_tokens.append(content)
                safe = content.replace("\n", "\\n")
                yield f"data: {safe}\n\n"
                return

            # Preserve the raw tool_calls block so the loop-back
            # assistant message is protocol-faithful (some providers
            # reject a tool result whose call wasn't echoed back).
            raw_calls = (getattr(message, "model_extra", {}) or {}).get(
                "tool_calls", [],
            )
            # `ChatMessage` uses extra='allow' so `tool_calls` /
            # `tool_call_id` / `name` are valid at runtime ; build via
            # model_validate so the type checker accepts the extra keys.
            assistant_content = (
                message.content
                if (message is not None and isinstance(message.content, str))
                else ""
            )
            messages.append(
                ChatMessage.model_validate(
                    {
                        "role": ChatRole.ASSISTANT,
                        "content": assistant_content,
                        "tool_calls": raw_calls,
                    },
                ),
            )

            for tc in tool_calls:
                tool_name = tc["name"]
                yield _inline_sse(
                    {
                        "kind": "tool_call",
                        "status": "running",
                        "name": tool_name,
                        "label": tool_name,
                        "round": round_idx + 1,
                    },
                )
                result = await self._doc_tools.execute(
                    name=tool_name,
                    arguments=tc["arguments"],
                    project_id=project_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    user_roles=user_roles,
                    turn_id=turn_id,
                )
                ok = "error" not in result
                summary = _summarise_tool_result(tool_name, result)
                # Surface the affected document path on mutating tools
                # so the UI can deep-link the inline log to the Working
                # area viewer (D-015 / Phase 2.C.3).
                doc_path = _tool_result_path(tool_name, result)
                doc_version = _tool_result_version(tool_name, result)
                done_event: dict[str, Any] = {
                    "kind": "tool_call",
                    "status": "done",
                    "name": tool_name,
                    "label": summary,
                    "ok": ok,
                    "round": round_idx + 1,
                    "summary": summary,
                    # The (size-capped) call arguments so the UI inline
                    # log can expand each tool call into its chain-of-
                    # thought detail (R-500-014 ; #4). Large string
                    # values like `content` are truncated to a preview
                    # so the audit ledger doesn't duplicate whole docs.
                    "arguments": _safe_tool_args(tc["arguments"]),
                }
                if doc_path is not None:
                    done_event["path"] = doc_path
                # Resulting per-file version (R-200-147) so the chat can
                # render a versioned "Open in working area (vN)" link
                # below the response (#5).
                if doc_version is not None:
                    done_event["version"] = doc_version
                yield _inline_sse(done_event)
                # Persist the terminal event into the shared audit
                # ledger (done-only policy, same as stages).
                collected_events.append(done_event)
                messages.append(
                    ChatMessage.model_validate(
                        {
                            "role": ChatRole.TOOL,
                            "content": json.dumps(
                                result, separators=(",", ":"),
                            ),
                            "tool_call_id": tc["id"],
                            "name": tc["name"],
                        },
                    ),
                )

        # Round budget exhausted without a final text answer.
        msg = (
            "(stopped after the tool-call budget — ask me to continue "
            "or rephrase)"
        )
        collected_tokens.append(msg)
        yield f"data: {msg}\n\n"

    async def _recent_history_messages(
        self,
        conversation_id: UUID,
        *,
        exclude_id: UUID | None,
    ) -> list[ChatMessage]:
        """Fetch the last N messages and convert to OpenAI-style chat
        messages for the LLM prompt. The just-persisted user message
        is omitted (we add it explicitly in the prompt builder)."""
        rows = await self._repo.list_messages(conversation_id)
        # `rows` are persisted in chronological order (oldest first);
        # take the LAST `rag_history_turns * 2` items so we keep both
        # sides of recent exchanges.
        recent = rows[-(self._rag_history_turns * 2 + 1) :-1]  # drop the just-saved user message
        out: list[ChatMessage] = []
        for row in recent:
            role = MessageRole(row["role"])
            chat_role = ChatRole.USER if role == MessageRole.USER else ChatRole.ASSISTANT
            out.append(ChatMessage(role=chat_role, content=row["content"]))
        return out

    # ------------------------------------------------------------------
    # Expert mode events (NATS stub — R-100-074)
    # ------------------------------------------------------------------

    @staticmethod
    async def expert_mode_stream() -> AsyncIterator[str]:
        """SSE stream for pipeline telemetry events.

        Returns a single 'unavailable' event per R-100-074: when NATS is
        not yet deployed, expert mode is declared unavailable rather than
        silently dropping events.
        """
        yield 'data: {"type":"unavailable","reason":"pipeline telemetry not yet implemented"}\n\n'


def get_service(repo: ConversationRepository) -> ConversationService:
    return ConversationService(repo)


# ---------------------------------------------------------------------------
# Phase D helpers — RAG prompt assembly + LLM stream parsing
# ---------------------------------------------------------------------------


_RAG_SYSTEM_PROMPT = (
    "You are a helpful assistant for project {project_id}.\n\n"
    "You draw on two sources of knowledge :\n"
    "1. The retrieved excerpts from the project's source documents "
    "(shown below) — AUTHORITATIVE for project-specific questions ; "
    "cite which excerpt ([1], [2]…) you used.\n"
    "2. Your general world knowledge — use freely for general questions "
    "(geography, common facts, language, code patterns, etc.) when the "
    "excerpts don't cover the topic.\n\n"
    "When answering :\n"
    "- For project-specific questions, prefer the retrieved excerpts and "
    "cite them.\n"
    "- For general / out-of-corpus questions, answer directly from your "
    "training — don't refuse just because the excerpts don't cover it.\n"
    "- If the answer mixes both, combine them naturally and indicate "
    "which part came from the project sources.\n"
    "- Respond in the same language as the user's question.\n\n"
    "Retrieved context :\n{context}"
)


_GENERAL_SYSTEM_PROMPT = (
    "You are a helpful assistant for project {project_id}.\n\n"
    "The project's source documents do not cover this question, so "
    "answer from your general knowledge alone. Be precise and concise. "
    "If you don't know the answer, say so plainly — do NOT invent.\n"
    "Respond in the same language as the user's question."
)
"""Fallback system prompt used when retrieval surfaced no chunks above
`_RAG_MIN_SCORE`. Drops the "Retrieved context" framing entirely —
small models (qwen2.5:3b on CPU) otherwise try to reconcile their
answer with the empty/irrelevant context block and produce
hallucinated bridges ("Paris as capital of the Hispanic kingdom…"
for "capitale de l'Espagne" being the canonical observed failure
mode). Keeping the user / project behavioural preambles on top of
this prompt is still correct — they aren't tied to retrieval."""


_DOCGEN_TOOL_DIRECTIVE = (
    "[Document tools — MANDATORY protocol]\n"
    "You operate in document-generation mode. You have function tools : "
    "list_documents, read_document, create_document, update_document, "
    "delete_document. You MUST act by CALLING these tools. A chat reply "
    "is NEVER a substitute for a tool call.\n"
    "\n"
    "To CREATE a document : call create_document with the full content.\n"
    "\n"
    "To MODIFY / add a section to / rewrite an EXISTING document, do "
    "EXACTLY this, as tool calls, without talking to the user in "
    "between :\n"
    "  1. call read_document(path) to get the current content ;\n"
    "  2. compute the new full file content yourself (apply the "
    "requested change to what you read) ;\n"
    "  3. call update_document(path, content=<the COMPLETE new file>) "
    "— update_document replaces the whole file, so pass everything, "
    "not just the added part ;\n"
    "  4. only AFTER the tool call returns, reply with ONE short "
    "confirmation sentence.\n"
    "\n"
    "ABSOLUTELY FORBIDDEN (these lose the user's work — never do them) :\n"
    "- Replying with the document content in a ``` code block ``` as "
    "if that saved it. It does NOT. Only update_document/"
    "create_document persist.\n"
    "- Asking the user to 'save the file', to 'specify' a value, or "
    "to confirm, when you can perform the action yourself. Do it.\n"
    "- Using placeholders like [insert_date_here] : if a value is "
    "needed (e.g. a date) and the user didn't give one, choose a "
    "sensible concrete value yourself and proceed with the tool call.\n"
    "- Stopping after read_document without the follow-up "
    "update_document call.\n"
    "If the user's message asks to change a document, your FIRST "
    "action this turn MUST be a read_document or update_document tool "
    "call — not prose."
)
"""Operational directive injected ONLY when the chat-direct DocGen
tool loop is active (`self._doc_tools is not None`). Without (and
even with an earlier, softer version of) it, local models
(qwen2.5:3b, qwen2.5-coder:7b) answer a modify request in prose —
emitting the edited content in a fenced block and asking the user to
'save the file' / 'specify the date' — instead of calling
update_document, so nothing is persisted (observed Phase 2.C.3, then
again 2026-05-19 with the 7b). This hardened, step-numbered,
explicitly-forbidden-patterns version is the mitigation ; it raises
compliance but a weak model can still ignore it — not a guarantee."""


def _assemble_system_prompt(
    *,
    project_id: str,
    context: str,
    user_prompt: str | None,
    project_prompt: str | None,
    has_relevant_hits: bool,
    docgen_tools_active: bool = False,
) -> str:
    """Compose the LLM system message in the user-mandated order :
    user behavioural prompt → project behavioural prompt → (DocGen
    tool directive when the tool loop is active) → RAG instructions +
    context (or a general-knowledge variant when no retrieved chunk
    cleared the relevance threshold). Empty / None preambles are
    silently skipped so the LLM sees a clean prompt without empty
    section headers."""
    sections: list[str] = []
    user_clean = (user_prompt or "").strip()
    if user_clean:
        sections.append(f"[User instructions]\n{user_clean}")
    project_clean = (project_prompt or "").strip()
    if project_clean:
        sections.append(f"[Project instructions]\n{project_clean}")
    if docgen_tools_active:
        sections.append(_DOCGEN_TOOL_DIRECTIVE)
    if has_relevant_hits:
        sections.append(
            _RAG_SYSTEM_PROMPT.format(project_id=project_id, context=context),
        )
    else:
        sections.append(_GENERAL_SYSTEM_PROMPT.format(project_id=project_id))
    return "\n\n".join(sections)


_RAG_MIN_SCORE = 0.4
"""Minimum cosine similarity for a retrieved chunk to be included in
the LLM context. Below this threshold the chunk is statistically
unrelated to the user's query — keeping it in the prompt confuses
small models (they latch onto the irrelevant excerpts instead of
falling back to general knowledge). The 0.4 threshold is empirical
for `all-minilm` embeddings ; tune per embedding adapter when
swapping models."""


def _format_retrieved_chunks(hits: list[Any]) -> str:
    """Render a list of `RetrievalHit` into a numbered context block.
    Filters out hits below `_RAG_MIN_SCORE` — the LLM should fall back
    to general knowledge when nothing relevant is found rather than
    hallucinating against weak hits.

    Empty list (after filtering) → a placeholder so the prompt's
    'answer from general knowledge' branch fires."""
    if not hits:
        return "(no excerpts retrieved from the project's sources)"
    relevant = [h for h in hits if getattr(h, "score", 0.0) >= _RAG_MIN_SCORE]
    if not relevant:
        return (
            "(no excerpts above the relevance threshold — the retrieved "
            "chunks scored below "
            f"{_RAG_MIN_SCORE} cosine similarity, treat as no project context)"
        )
    lines: list[str] = []
    for i, hit in enumerate(relevant, start=1):
        # Hits are RetrievalHit Pydantic instances; access by attribute.
        snippet = getattr(hit, "content", "") or ""
        # Trim long chunks so the prompt stays within model context.
        snippet = snippet.strip()
        if len(snippet) > 800:
            snippet = snippet[:800] + "…"
        source = getattr(hit, "source_id", None) or "unknown"
        score = getattr(hit, "score", 0.0)
        lines.append(f"[{i}] (source={source}, score={score:.3f})\n{snippet}")
    return "\n\n".join(lines)


def _hit_stats(hits: list[Any]) -> tuple[int, int, float]:
    """Summarise a retrieval-hit list for the stage event. Returns
    `(total, above_threshold, top_score)`. `top_score` is rounded to
    3 decimals so the JSON payload stays small."""
    total = len(hits)
    above = sum(1 for h in hits if getattr(h, "score", 0.0) >= _RAG_MIN_SCORE)
    top = max((getattr(h, "score", 0.0) for h in hits), default=0.0)
    return total, above, round(float(top), 3)


def _summarise_tool_result(tool: str, result: dict[str, Any]) -> str:
    """One-line human summary of a tool result for the unified
    `event: inline` (kind='tool_call') done payload — it becomes the
    event `label`/`summary` the UX formatter renders. Kept terse —
    the full result is fed to the model, not the user."""
    if "error" in result:
        return f"error: {result['error']}"
    summaries: dict[str, str] = {
        "list_documents": f"{len(result.get('documents', []))} document(s)",
        "read_document": (
            f"read {result.get('path', '?')} "
            f"({len(result.get('content', ''))} chars)"
        ),
        "create_document": f"created {result.get('created', {}).get('path', '?')}",
        "update_document": f"updated {result.get('updated', {}).get('path', '?')}",
        "delete_document": f"deleted {result.get('deleted', '?')}",
    }
    return summaries.get(tool, "ok")


def _safe_tool_args(
    arguments: dict[str, Any], *, max_len: int = 280,
) -> dict[str, Any]:
    """Size-capped copy of a tool call's arguments for the inline-log
    chain-of-thought detail (#4). Long string values (notably the
    `content` of create/update_document, which is the whole document)
    are truncated to a preview + a `(N chars)` note so the persisted
    `events` ledger never duplicates a full document body. Non-string
    values pass through unchanged (they are small : paths, flags)."""
    out: dict[str, Any] = {}
    for key, value in arguments.items():
        if isinstance(value, str) and len(value) > max_len:
            out[key] = f"{value[:max_len]}… ({len(value)} chars)"
        else:
            out[key] = value
    return out


def _tool_result_path(tool: str, result: dict[str, Any]) -> str | None:
    """Extract the affected document path from a mutating tool result,
    for the SSE `done` payload `path` field. Returns ``None`` for
    non-mutating tools or on a malformed result so the UI simply
    omits the deep-link rather than rendering a broken one."""
    if "error" in result:
        return None
    if tool == "create_document":
        path = result.get("created", {}).get("path")
    elif tool == "update_document":
        path = result.get("updated", {}).get("path")
    elif tool == "delete_document":
        path = result.get("deleted")
    else:
        return None
    return path if isinstance(path, str) and path else None


def _tool_result_version(tool: str, result: dict[str, Any]) -> int | None:
    """Extract the resulting per-file version from a create/update
    document tool result (the C4 `DocumentRef.version`, R-200-147) for
    the SSE `done` payload `version` field. None for non-mutating tools,
    errors, or when C4 could not compute it (Gitea unavailable)."""
    if "error" in result:
        return None
    if tool == "create_document":
        version = result.get("created", {}).get("version")
    elif tool == "update_document":
        version = result.get("updated", {}).get("version")
    else:
        return None
    return version if isinstance(version, int) else None


def _extract_delta_content(chunk: dict[str, Any]) -> str:
    """Pull the streamed token text out of an OpenAI-shaped chunk.

    Defensive: chunk may lack `choices` (final usage event), or the
    delta may be empty (role-only first event). Returns "" in those
    cases so the caller can simply concatenate.
    """
    choices = chunk.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    return content if isinstance(content, str) else ""
