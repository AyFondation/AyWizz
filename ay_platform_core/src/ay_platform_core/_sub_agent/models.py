# =============================================================================
# File: models.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/_sub_agent/models.py
# Description: Wire models for the sub-agent context bundle (R-200-033).
#              The manifest.json that lives at the bundle prefix root
#              (`c4-dispatch/<run_id>/<sub_agent_id>/manifest.json`) is
#              a `TaskEnvelope` — auditable, replayable.
#
# @relation implements:R-200-033
# =============================================================================

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ay_platform_core.c4_orchestrator.models import AgentRole, Phase


class ContextBundleEntry(BaseModel):
    """One referenced file under the bundle's `context/` directory.
    The sub-agent reads each entry's bytes from MinIO before composing
    the LLM prompt. `purpose` is operator-friendly metadata included in
    the manifest for replay audit (R-200-033 explicit-context rule)."""

    model_config = ConfigDict(extra="forbid")

    relative_path: str = Field(min_length=1, max_length=512)
    purpose: str = Field(default="", max_length=256)
    content_type: str = Field(default="text/plain", max_length=128)


class TaskEnvelope(BaseModel):
    """`manifest.json` of a sub-agent context bundle (E-200-003-adjacent).

    Sub-agents are stateless workers — every input they need to do their
    job sits in the envelope OR is fetched lazily from `context/` via
    `context_entries[*].relative_path`. The envelope is auditable on
    its own without re-reading MinIO."""

    model_config = ConfigDict(extra="forbid")

    # ---- Identity ---------------------------------------------------------
    run_id: str = Field(min_length=1, max_length=64)
    sub_agent_id: str = Field(min_length=1, max_length=64)
    project_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)

    # ---- Task ------------------------------------------------------------
    phase: Phase
    agent: AgentRole
    user_prompt: str = Field(min_length=1)
    # Free-form bundle the orchestrator wants to pass through to the
    # LLM prompt (e.g. concerns_so_far, domain hint, prior outputs).
    # The sub-agent JSON-dumps it into the user prompt verbatim — same
    # shape as the in-process dispatcher consumes.
    context_bundle: dict[str, Any] = Field(default_factory=dict)
    # Optional file references the sub-agent SHALL load from MinIO
    # before composing the prompt. Empty list = no file context.
    context_entries: list[ContextBundleEntry] = Field(default_factory=list)


class SubAgentRunReport(BaseModel):
    """Top-level wrapper of the completion.json file the sub-agent
    writes back to `<bundle_prefix>output/completion.json`. The
    orchestrator reads this file once the pod terminates to decide
    the run's next step."""

    model_config = ConfigDict(extra="forbid")

    # The orchestrator's `AgentCompletion` envelope — already validated
    # by its own model when the sub-agent serialises it.
    completion: dict[str, Any]
    # `started_at_iso` / `finished_at_iso` add cheap wall-clock
    # observability without the orchestrator having to query K8s for
    # pod timestamps. Sub-agent-side clock — trust at audit level.
    started_at_iso: str
    finished_at_iso: str
    # The raw LLM response id (when surfaced by C8). Useful for replay
    # / debugging without re-running the full pipeline.
    llm_call_id: str | None = None
