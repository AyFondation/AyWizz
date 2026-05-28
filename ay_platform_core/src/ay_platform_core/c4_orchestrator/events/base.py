# =============================================================================
# File: base.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/events/base.py
# Description: Event publisher protocol for C4. Shares the envelope shape
#              defined by E-300-003 (reused by E-200-004). Subjects follow
#              `orchestrator.{run_id}.<object>.<action>` per R-200-070.
#
# @relation implements:R-200-070
# @relation implements:E-200-004
# =============================================================================

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class OrchestratorEventPublisher(Protocol):
    async def publish(self, subject: str, envelope: dict[str, Any]) -> None: ...
