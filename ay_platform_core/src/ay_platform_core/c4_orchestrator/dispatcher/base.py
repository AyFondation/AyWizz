# =============================================================================
# File: base.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/dispatcher/base.py
# Description: Agent dispatcher protocol. Implementations invoke an agent
#              for a given phase and return its `AgentCompletion` envelope.
#              Two concrete implementations planned:
#                - InProcessDispatcher (v1): runs the agent in the same
#                  Python process, issues one LLM call via the C8 client,
#                  parses the result into the envelope.
#                - K8sPodDispatcher (v2, R-200-030): spawns an ephemeral
#                  pod with an isolated context bundle. Deferred.
#
# @relation implements:R-200-030
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ay_platform_core.c4_orchestrator.models import (
    AgentCompletion,
    AgentRole,
    Phase,
)


@dataclass(frozen=True, slots=True)
class DispatchRequest:
    """Envelope passed to a dispatcher for one agent invocation."""

    run_id: str
    phase: Phase
    agent: AgentRole
    session_id: str
    tenant_id: str
    user_id: str
    project_id: str
    prompt: str
    context_bundle: dict[str, Any]


@runtime_checkable
class AgentDispatcher(Protocol):
    """Protocol every dispatcher implementation satisfies."""

    async def dispatch(self, request: DispatchRequest) -> AgentCompletion: ...
