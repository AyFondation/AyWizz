# =============================================================================
# File: models.py
# Version: 4
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

    id: UUID
    owner_id: str
    project_id: str | None = None
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


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
        "'tool_call' (DocGen tool), extensible.",
    )
    label: str = Field(description="Human-readable one-line summary.")
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

    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class PromptReference(BaseModel):
    """One file/excerpt the operator pinned to their prompt
    (E-200-008 / R-200-180..184). `kind=file` references the whole
    file ; `kind=excerpt` carries a line range. `source=live-docs`
    targets the project live-docs artifact run ; `source=source` is
    deferred to v2 (Q-200-019 — run_id ambiguity)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["file", "excerpt"]
    source: Literal["live-docs", "source"]
    path: str = Field(min_length=1, max_length=512)
    range: PromptReferenceRange | None = None


class MessagePublic(BaseModel):
    """Public view of a single message."""

    id: UUID
    conversation_id: UUID
    role: MessageRole
    content: str
    timestamp: datetime
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
    title: str = Field(default="New Conversation", max_length=255)
    project_id: str | None = None


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    project_id: str | None = None


class MessageRequest(BaseModel):
    """User-sent message + optional behavioural prompts forwarded by
    the UX. `user_prompt` and `project_prompt` are the EFFECTIVE
    values resolved by C2 (override OR default) and are prepended in
    that order to the system prompt the LLM receives, BEFORE the RAG
    context block. Either field is optional ; missing/empty values
    are silently dropped from the assembled prompt."""

    content: str = Field(min_length=1)
    user_prompt: str | None = Field(default=None, max_length=4000)
    project_prompt: str | None = Field(default=None, max_length=4000)
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


class ConversationListResponse(BaseModel):
    conversations: list[ConversationPublic]


class ConversationResponse(BaseModel):
    conversation: ConversationPublic


class MessageListResponse(BaseModel):
    messages: list[MessagePublic]


class MessageResponse(BaseModel):
    message: MessagePublic
