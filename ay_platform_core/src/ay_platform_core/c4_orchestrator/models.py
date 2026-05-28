# =============================================================================
# File: models.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/models.py
# Description: Pydantic v2 models for the C4 Orchestrator. Mirrors the
#              contract-critical entities E-200-001..008 from
#              200-SPEC-PIPELINE-AGENT.
#
#              v2 (2026-05-20) : adds Tranche B contracts — TraceEvent
#              (E-200-006), RunSteer (E-200-007), and the extended
#              RunPublic with the `trace` sliding window per R-200-201.
#              PromptReference (E-200-008) lives in C3 (chat surface).
#
# @relation implements:R-200-001
# @relation implements:R-200-002
# @relation implements:R-200-020
# @relation implements:R-200-022
# @relation implements:R-200-200
# @relation implements:R-200-201
# @relation implements:R-200-202
# @relation implements:E-200-001
# @relation implements:E-200-002
# @relation implements:E-200-003
# @relation implements:E-200-006
# @relation implements:E-200-007
# =============================================================================

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Enumerations — mirror 200-SPEC §2 glossary and §4.1 / §4.6
# ---------------------------------------------------------------------------


class Phase(StrEnum):
    """Five-phase pipeline per R-200-001."""

    BRAINSTORM = "brainstorm"
    SPEC = "spec"
    PLAN = "plan"
    GENERATE = "generate"
    REVIEW = "review"


class RunStatus(StrEnum):
    """Terminal run-level status per E-200-001."""

    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class EscalationStatus(StrEnum):
    """Four escalation statuses returned by every agent completion (R-200-022)."""

    DONE = "DONE"
    DONE_WITH_CONCERNS = "DONE_WITH_CONCERNS"
    NEEDS_CONTEXT = "NEEDS_CONTEXT"
    BLOCKED = "BLOCKED"


class AgentRole(StrEnum):
    """Agent roster per R-200-020. Mirrors C8 `catalog.AGENT_CATALOG` names."""

    ARCHITECT = "architect"
    PLANNER = "planner"
    IMPLEMENTER = "implementer"
    SPEC_REVIEWER = "spec-reviewer"
    QUALITY_REVIEWER = "quality-reviewer"
    SUB_AGENT = "sub-agent"


class Gate(StrEnum):
    """Three hard gates per R-200-010..013."""

    A_DESIGN_APPROVED = "A"
    B_VALIDATION_RED = "B"
    C_VALIDATION_FRESH_GREEN = "C"


class TraceEventKind(StrEnum):
    """Discriminator for the run-trace ledger (E-200-006, R-200-200).

    Future kinds SHALL extend this union, never reuse an existing value.
    """

    AGENT_DISPATCH = "agent-dispatch"
    GATE_EVAL = "gate-eval"
    FIX_ATTEMPT = "fix-attempt"
    PHASE_BOUNDARY = "phase-boundary"
    STEER_APPLIED = "steer-applied"


class TraceEvent(BaseModel):
    """One entry of the persisted run-trace ledger (E-200-006, R-200-200).

    Append-only, kind-discriminated. `payload` carries kind-specific
    extras and SHALL stay JSON-serialisable (Arango persistence).
    """

    model_config = ConfigDict(extra="forbid")

    kind: TraceEventKind
    ts: datetime
    phase: Phase
    label: str
    duration_ms: int | None = Field(default=None, ge=0)
    ok: bool | None = None
    payload: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Agent completion envelope (E-200-002)
# ---------------------------------------------------------------------------


class AgentConcern(BaseModel):
    """One non-blocking concern surfaced by an agent (R-200-041)."""

    model_config = ConfigDict(extra="forbid")

    severity: str  # low | medium | high
    message: str


class AgentBlocker(BaseModel):
    """Blocker payload emitted with EscalationStatus.BLOCKED."""

    model_config = ConfigDict(extra="forbid")

    reason: str
    suggested_action: str | None = None


class AgentNeedsContext(BaseModel):
    """Payload emitted with EscalationStatus.NEEDS_CONTEXT (R-200-040)."""

    model_config = ConfigDict(extra="forbid")

    queries: list[str] = Field(default_factory=list)


class AgentCompletion(BaseModel):
    """Return envelope of an agent invocation (E-200-002)."""

    model_config = ConfigDict(extra="forbid")

    agent: AgentRole
    run_id: str
    phase: Phase
    status: EscalationStatus
    output: dict[str, Any] = Field(default_factory=dict)
    concerns: list[AgentConcern] = Field(default_factory=list)
    needs_context: AgentNeedsContext | None = None
    blocker: AgentBlocker | None = None
    duration_ms: int = Field(ge=0, default=0)
    llm_call_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Gate evaluation result (used internally; not a router response)
# ---------------------------------------------------------------------------


class GateResult(BaseModel):
    """Structured outcome of a hard-gate evaluation."""

    model_config = ConfigDict(extra="forbid")

    gate: Gate
    passed: bool
    artifact_id: str | None = None
    reason: str | None = None
    evidence_timestamp: datetime | None = None


# ---------------------------------------------------------------------------
# Run public view (E-200-001 projection)
# ---------------------------------------------------------------------------


class RunPublic(BaseModel):
    """Run state exposed through the REST API. Internal bookkeeping
    (fix_attempts map, enrichment_rounds, events_emitted, etc.) is NOT
    surfaced here — admins query the full record via a dedicated endpoint
    when needed."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    project_id: str
    session_id: str
    tenant_id: str
    user_id: str
    domain: str
    current_phase: Phase
    status: RunStatus
    started_at: datetime
    completed_at: datetime | None = None
    concerns: list[AgentConcern] = Field(default_factory=list)
    minio_root: str
    # Operator-readable explanation when `status == "blocked"`. Set by
    # `OrchestratorService._block_run` ; surfaced in the UX Pipeline
    # page so the operator can see why automatic retries gave up
    # (parse failure / unknown status / three-fix-rule / gate guard /
    # admin abort). None on running and completed runs.
    block_reason: str | None = None
    # Sliding-window of the 200 most recent TraceEvents on the run
    # (R-200-201). Older events are read back via the paginated
    # `GET /runs/{id}/trace?before=<ts>` endpoint. Empty on legacy
    # runs created before Tranche B.
    trace: list[TraceEvent] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Request/response models for the REST surface (§6.1 of 200-SPEC)
# ---------------------------------------------------------------------------


class RunCreate(BaseModel):
    """POST /api/v1/orchestrator/runs body."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    session_id: str
    initial_prompt: str = Field(min_length=1)
    domain: str = "code"


class RunFeedback(BaseModel):
    """POST /runs/{run_id}/feedback body — user input at interactive phases.

    `approved=True` on the `plan` phase is how invisible-mode UIs pass
    Gate A per Q-200-004 resolution.
    """

    model_config = ConfigDict(extra="forbid")

    phase: Phase
    user_feedback: str | None = None
    approved: bool | None = None


class RunResumeStrategy(StrEnum):
    RETRY = "retry"
    SKIP_PHASE = "skip-phase"
    ABORT = "abort"


class RunResume(BaseModel):
    """POST /runs/{run_id}/resume — admin override after BLOCKED halt."""

    model_config = ConfigDict(extra="forbid")

    strategy: RunResumeStrategy


class RunSteer(BaseModel):
    """POST /runs/{run_id}/steer body (E-200-007, R-200-202).

    A single operator hint, capped at 4 KB. Multiple steers are issued
    one POST at a time ; batching is not supported (per spec).
    """

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4096)


# ---------------------------------------------------------------------------
# Domain descriptor (E-200-003) — loaded at startup per R-200-062
# ---------------------------------------------------------------------------


class GateCheck(BaseModel):
    """One gate check entry in a domain descriptor."""

    model_config = ConfigDict(extra="forbid")

    check: str  # named check identifier
    implementation: str  # "module.path:callable" — loaded on descriptor import


class AgentOverride(BaseModel):
    """Per-domain override on an agent role's LLM features."""

    model_config = ConfigDict(extra="allow")

    llm_features_additional: list[str] = Field(default_factory=list)


class DomainDescriptor(BaseModel):
    """YAML descriptor per domain plug-in (E-200-003)."""

    model_config = ConfigDict(extra="forbid")

    domain: str
    artifact_mime_types: list[str] = Field(min_length=1)
    validation_artifact_type: str
    gate_b: GateCheck
    gate_c: GateCheck
    agents: dict[str, AgentOverride] = Field(default_factory=dict)
