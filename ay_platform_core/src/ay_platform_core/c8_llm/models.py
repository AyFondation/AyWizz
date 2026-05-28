# =============================================================================
# File: models.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c8_llm/models.py
# Description: Pydantic v2 models for C8's public surface. These are the
#              wire-level payloads exchanged between internal components and
#              the LiteLLM proxy, plus the cost/admin response shapes.
#              C8 exposes an OpenAI-compatible API (R-800-010); the request
#              and response models mirror the OpenAI schema subset the
#              platform depends on, not the full specification.
#
#              v2 (2026-05-19): `ChatMessage.content` is now Optional
#              (`| None = None`). Per the OpenAI spec a tool-call
#              assistant message has `content: null`. Spec-compliant
#              hosted providers (OpenRouter) return that ; the
#              previously-required field raised a ChatCompletionResponse
#              validation error before `tool_calls` could be read,
#              breaking the entire DocGen tool loop on the hosted-LLM
#              switch. Ollama always sent a string so this was latent.
#
# @relation implements:R-800-010
# @relation implements:R-800-013
# @relation implements:R-800-014
# @relation implements:R-800-070
# @relation implements:R-800-073
# =============================================================================

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Chat completions (OpenAI-compatible subset)
# ---------------------------------------------------------------------------


class ChatRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ChatMessage(BaseModel):
    """Single message in a chat completion exchange."""

    model_config = ConfigDict(extra="allow")  # `name`, `tool_call_id`, etc. are optional

    role: ChatRole
    # OpenAI spec : `content` is null on an assistant message that
    # carries `tool_calls` (the model chose a tool over prose). A
    # spec-compliant provider (OpenRouter/OpenAI) returns content=null
    # there ; Ollama always sent a string so this never surfaced
    # until the 2026-05-19 hosted-provider switch. Optional + default
    # None ; downstream already guards with isinstance(content, str).
    content: str | list[dict[str, Any]] | None = None


class ChatCompletionRequest(BaseModel):
    """Request body for POST /v1/chat/completions.

    Only the fields the platform consciously depends on are declared; any
    additional OpenAI-standard field is accepted (extra='allow') and passed
    through to the provider verbatim.
    """

    model_config = ConfigDict(extra="allow")

    model: str | None = None
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    tools: list[dict[str, Any]] | None = None
    response_format: dict[str, Any] | None = None


class UsageInfo(BaseModel):
    """Token usage counters returned by the provider."""

    model_config = ConfigDict(extra="allow")

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached_tokens: int = 0


class ChatCompletionChoice(BaseModel):
    model_config = ConfigDict(extra="allow")

    index: int
    message: ChatMessage
    finish_reason: str | None = None


class ChatCompletionResponse(BaseModel):
    """Non-streaming response to /v1/chat/completions."""

    model_config = ConfigDict(extra="allow")

    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: UsageInfo
    # C8's `_provider_extensions` envelope per R-800-015 — caller opts in.
    provider_extensions: dict[str, Any] | None = Field(
        default=None, alias="_provider_extensions"
    )


# ---------------------------------------------------------------------------
# Request-envelope metadata (what C8 reads from headers + merges into call logs)
# ---------------------------------------------------------------------------


class CallTags(BaseModel):
    """Tag set propagated on every LLM call — matches `llm_calls.tags`
    (E-800-002) exactly. Used by the cost-tracker callback and by cost
    aggregation endpoints."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    project_id: str | None = None
    user_id: str | None = None
    session_id: str
    agent_name: str
    phase: str | None = None
    sub_agent_id: str | None = None


class CallRecord(BaseModel):
    """A single row of `llm_calls` (E-800-002). Public because admin UIs and
    the eval harness consume this schema to drive dashboards."""

    model_config = ConfigDict(extra="forbid")

    call_id: str
    timestamp_start: datetime
    timestamp_end: datetime
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    cost_usd: float
    latency_ms: int
    status: Literal["success", "failure", "timeout", "rate_limited", "budget_exceeded"]
    error_code: str | None = None
    error_message: str | None = None
    tags: CallTags
    request_fingerprint: str
    archive_path: str | None = None
    trace_id: str | None = None


# ---------------------------------------------------------------------------
# Cost forwarder → receiver wire payload (R-800-070)
# ---------------------------------------------------------------------------


class CostCallEnvelope(BaseModel):
    """Compact payload the LiteLLM cost forwarder POSTs to the C8 cost
    receiver (`POST /internal/llm-calls`). The forwarder pre-computes
    the cache fingerprint and sends only what the receiver needs to
    build a `CallRecord` — NO message bodies, so cost tracking never
    duplicates document content through the internal hop."""

    model_config = ConfigDict(extra="allow")

    status: Literal["success", "failure"] = "success"
    model: str
    # {prompt_tokens, completion_tokens, cached_tokens}
    usage: dict[str, int] = Field(default_factory=dict)
    # Forward-auth / attribution headers (X-Agent-Name, X-Tenant-Id, …).
    headers: dict[str, str] = Field(default_factory=dict)
    fingerprint: str = ""
    start_time: datetime
    end_time: datetime
    error_code: str | None = None
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Admin — cost and budget responses
# ---------------------------------------------------------------------------


class CostBreakdown(BaseModel):
    """Per-dimension cost roll-up returned by /admin/v1/costs/*."""

    model_config = ConfigDict(extra="forbid")

    dimension: str  # e.g. "agent_name", "session_id", "project_id"
    value: str
    total_cost_usd: float
    call_count: int
    input_tokens: int
    output_tokens: int


class CostSummary(BaseModel):
    """Aggregated cost summary (R-800-073)."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    project_id: str | None = None
    period_start: datetime
    period_end: datetime
    total_cost_usd: float
    call_count: int
    by_agent: list[CostBreakdown] = Field(default_factory=list)
    by_model: list[CostBreakdown] = Field(default_factory=list)


class BudgetStatus(BaseModel):
    """Snapshot of a budget window (R-800-063)."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    project_id: str | None = None
    window_start: datetime
    window_end: datetime
    hard_cap_usd: float
    soft_cap_usd: float
    consumed_usd: float
    remaining_usd: float
    status: Literal["ok", "soft_cap_reached", "hard_cap_exceeded"]
