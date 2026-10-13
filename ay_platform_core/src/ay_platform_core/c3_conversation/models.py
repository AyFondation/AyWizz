# =============================================================================
# File: models.py
# Version: 5
# Path: ay_platform_core/src/ay_platform_core/c3_conversation/models.py
# Description: Pydantic v2 data contracts for C3 Conversation Service.
#              ConversationPublic and MessagePublic are registered as
#              platform contracts (consumed by C1, C4, future UI).
#
#              v4: UNIFIED inline-event pipeline. `StageRecord` and
#              the short-lived `ToolCallRecord` are collapsed into a
#              single `InlineEvent` (discriminated by `kind`) and a
#              single `MessagePublic.events` list. One channel for
#              every kind of in-turn activity (pipeline stages,
#              DocGen tool calls, future kinds) : the service emits
#              them through one collector, the UX renders them through
#              one formatter registry, and the persisted list is one
#              queryable audit ledger. Legacy messages persisted with
#              the v3 `stages` field are projected into `events`
#              (kind="stage") at read time — no data migration. Only
#              terminal (`done`) events are persisted.
#
#              v3: `StageRecord` + `MessagePublic.stages` persist the
#              pipeline timeline (retrieve / generate / done phases
#              with durations + stats) alongside the assistant message.
#              The UX re-renders the same chip + collapsible panel on
#              navigation / reload, where previously the timeline was
#              transient (client-only `liveStages` state).
#
#              v2: `MessageRequest` carries optional `user_prompt` and
#              `project_prompt` fields. The UX fetches these from C2
#              at session start (user prefs) / project navigation
#              (project settings) and forwards them per-message so C3
#              can prepend both ahead of the RAG context block.
# @relation R-100-003 D-008
# =============================================================================

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


# ---------------------------------------------------------------------------
# Public contracts (exposed via contract registry)
# ---------------------------------------------------------------------------


class ConversationPublic(BaseModel):
    """Public view of a conversation — no internal metadata."""

    id: UUID = Field(description="Stable identifier of this conversation.")
    owner_id: str = Field(
        description=(
            "The user who owns it. A conversation is PRIVATE to its owner: "
            "there is no sharing parameter, and another member of the same "
            "project cannot read it."
        )
    )
    project_id: str | None = Field(
        default=None,
        description=(
            "The project this conversation retrieves from. Null means a "
            "project-less chat — the LLM answers with no RAG context, "
            "because retrieval is scoped to a project and there is nothing "
            "to scope it to."
        ),
    )
    title: str = Field(
        description=(
            "Display title. Defaults to a placeholder on creation and is "
            "replaced once the first exchange gives it a subject."
        )
    )
    created_at: datetime = Field(description="When it was opened (UTC).")
    updated_at: datetime = Field(
        description="When a message was last appended (UTC) — the sort key a listing uses."
    )
    message_count: int = Field(
        default=0, description="Messages in it, user and assistant turns together."
    )


class InlineEvent(BaseModel):
    """One inline-activity event — the unified record for every kind
    of in-turn activity surfaced to the operator (pipeline stages,
    chat-direct DocGen tool calls, and future kinds).

    Single channel by design : the service emits all of them through
    one collector, the SSE carries them on one `event: inline`
    channel, the UX renders them through one formatter registry keyed
    on `kind`, and the persisted list is one queryable audit ledger.
    Adding a new kind = adding a formatter, no new field plumbing.

    Only terminal (`done`) events are persisted — `running` events
    are live-only progress signals (same policy the former
    StageRecord applied to pipeline phases). `kind`-specific fields
    are optional ; a formatter reads the subset relevant to its
    kind."""

    kind: str = Field(
        description="Discriminator : 'stage' (pipeline phase), "
        "'tool_call' (DocGen tool), 'reasoning' (model extended-thinking, "
        "R-200-207), extensible.",
    )
    label: str = Field(description="Human-readable one-line summary.")
    text: str | None = Field(
        default=None,
        description="Free text payload — for 'reasoning' events, a chunk of the "
        "model's extended-thinking (verbose mode only).",
    )
    status: Literal["running", "done"] = "done"
    name: str | None = Field(
        default=None,
        description="Machine id : stage name or tool name.",
    )
    ok: bool | None = Field(
        default=None,
        description="Outcome flag for tool_call events ; None for stages.",
    )
    round: int | None = Field(
        default=None,
        description="Tool-loop round index for tool_call events.",
    )
    duration_ms: int | None = Field(
        default=None,
        description="Elapsed time for stage events.",
    )
    stats: dict[str, Any] | None = Field(
        default=None,
        description="Free-form metrics for stage events.",
    )
    summary: str | None = Field(
        default=None,
        description="Result summary for tool_call events.",
    )
    path: str | None = Field(
        default=None,
        description="Affected document path for mutating DocGen tools "
        "(create / update / delete_document) ; None otherwise.",
    )


class PromptReferenceRange(BaseModel):
    """Inclusive 1-indexed line range for an `excerpt`-kind reference
    (E-200-008). End line is inclusive ; `start_line <= end_line`."""

    model_config = ConfigDict(extra="forbid")

    start_line: int = Field(
        ge=1, description="First line of the excerpt, 1-indexed and INCLUSIVE."
    )
    end_line: int = Field(
        ge=1,
        description=(
            "Last line, 1-indexed and INCLUSIVE — so a single-line excerpt "
            "has `start_line == end_line`, not `end_line = start + 1`."
        ),
    )


class PromptReference(BaseModel):
    """One file/excerpt the operator pinned to their prompt
    (E-200-008 / R-200-180..184). `kind=file` references the whole
    file ; `kind=excerpt` carries a line range. `source=live-docs`
    targets the project live-docs artifact run ; `source=source` is
    deferred to v2 (Q-200-019 — run_id ambiguity)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["file", "excerpt"] = Field(
        description=(
            "`file` attaches the whole document; `excerpt` attaches only "
            "`range`. The distinction is a cost decision as much as a "
            "precision one — a whole file spends context the answer may not "
            "need."
        )
    )
    source: Literal["live-docs", "source"] = Field(
        description=(
            "Which tree `path` is resolved against: `live-docs` for the "
            "project's authored documents, `source` for its generated code. "
            "The same path can exist in both."
        )
    )
    path: str = Field(
        min_length=1,
        max_length=512,
        description="Path within that tree, relative to the project root.",
    )
    range: PromptReferenceRange | None = Field(
        default=None,
        description="Required for `excerpt`, ignored for `file`.",
    )


class MessagePublic(BaseModel):
    """Public view of a single message."""

    id: UUID = Field(description="Stable identifier of this message.")
    conversation_id: UUID = Field(description="The conversation it belongs to.")
    role: MessageRole = Field(
        description="Who produced it — the user, or the assistant."
    )
    content: str = Field(
        description=(
            "The text. For an assistant turn this is the final answer, "
            "already assembled from the token deltas the SSE stream emitted."
        )
    )
    timestamp: datetime = Field(description="When it was recorded (UTC).")
    events: list[InlineEvent] | None = Field(
        default=None,
        description="Unified inline-activity ledger captured during "
        "the SSE stream (assistant messages only) : pipeline stages, "
        "DocGen tool calls, future kinds — one audit list. None for "
        "user messages. Legacy messages persisted with the v3 "
        "`stages` field are projected into this list (kind='stage') "
        "at read time, so no data migration is required.",
    )
    references: list[PromptReference] | None = Field(
        default=None,
        description="Operator-attached prompt references (R-200-182). "
        "Metadata only — `path` + `kind` + `range` — NEVER the resolved "
        "content (storage discipline). None on assistant messages and "
        "on legacy user messages persisted before Tranche B.",
    )


# ---------------------------------------------------------------------------
# Internal model (includes ArangoDB _key, soft-delete flag)
# ---------------------------------------------------------------------------


class ConversationInternal(BaseModel):
    """Full conversation record as stored in ArangoDB."""

    id: UUID
    owner_id: str
    project_id: str | None = None
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    deleted: bool = False


# ---------------------------------------------------------------------------
# Request / response shapes
# ---------------------------------------------------------------------------


class ConversationCreate(BaseModel):
    title: str = Field(
        default="New Conversation",
        max_length=255,
        description="Display title. The default is a placeholder meant to be replaced.",
    )
    project_id: str | None = Field(
        default=None,
        description=(
            "The project to retrieve from. Omit for a chat with no RAG "
            "context. The caller must hold a role on it."
        ),
    )


class ConversationUpdate(BaseModel):
    title: str | None = Field(
        default=None, max_length=255, description="New title. Omit to leave it."
    )
    project_id: str | None = Field(
        default=None,
        description=(
            "Re-point the conversation at another project. Omit to leave it. "
            "Past turns are NOT re-retrieved — only subsequent ones use the "
            "new scope."
        ),
    )


class MessageRequest(BaseModel):
    """User-sent message + optional behavioural prompts forwarded by
    the UX. `user_prompt` and `project_prompt` are the EFFECTIVE
    values resolved by C2 (override OR default) and are prepended in
    that order to the system prompt the LLM receives, BEFORE the RAG
    context block. Either field is optional ; missing/empty values
    are silently dropped from the assembled prompt."""

    content: str = Field(min_length=1, description="The user's message.")
    user_prompt: str | None = Field(
        default=None,
        max_length=4000,
        description=(
            "The caller's own system-prompt addendum, forwarded from their "
            "stored preference. Sent per message rather than read "
            "server-side so a client can override it for one turn."
        ),
    )
    project_prompt: str | None = Field(
        default=None,
        max_length=4000,
        description=(
            "The project's system-prompt addendum, same mechanism. Both are "
            "prepended AHEAD of the retrieved context block, so they frame "
            "the answer rather than compete with the evidence."
        ),
    )
    # Tranche B (R-200-180) — up to 10 file/excerpt references the
    # operator pinned to this turn. Server-resolved and inlined as a
    # separate system block before the user message. Empty list and
    # None are equivalent (no references attached).
    references: list[PromptReference] | None = Field(
        default=None,
        max_length=10,
        description="Up to 10 prompt-attached references "
        "(R-200-180). 32K-token cap on combined resolved content "
        "(R-200-181) — over-cap requests SHALL return 413.",
    )
    reasoning_verbose: bool = Field(
        default=False,
        description="Verbose reasoning toggle (R-200-207) — when true the model's "
        "extended thinking is requested and streamed as `reasoning` inline "
        "events. Opt-in (extra thinking tokens billed).",
    )


class ConversationListResponse(BaseModel):
    conversations: list[ConversationPublic] = Field(
        description="The caller's own conversations, most recently updated first."
    )


class ConversationResponse(BaseModel):
    conversation: ConversationPublic = Field(
        description=(
            "Wrapped rather than returned bare so the envelope can gain a\n"
            "field without breaking every client — the same reason\n"
            "`DomainList` wraps a list of strings."
        )
    )


class MessageListResponse(BaseModel):
    messages: list[MessagePublic] = Field(
        description="The conversation's turns in chronological order, oldest first."
    )


class MessageResponse(BaseModel):
    message: MessagePublic = Field(description="The recorded message.")
