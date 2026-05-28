# =============================================================================
# File: base.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/events/base.py
# Description: Protocol defining the NATS publisher contract (E-300-003).
#              Implementations SHALL preserve the canonical envelope so that
#              consumers idempotently de-dupe on `event_id`.
#
# @relation implements:E-300-003
# =============================================================================

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EventPublisher(Protocol):
    """Port for publishing C5 events.

    `subject` SHALL follow the pattern
    `requirements.<project-id>.<object>.<action>` per §6.3 of 300-SPEC.
    """

    async def publish(self, subject: str, envelope: dict[str, Any]) -> None: ...
