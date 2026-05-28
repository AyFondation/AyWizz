# =============================================================================
# File: null_publisher.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/events/null_publisher.py
# Description: No-op publisher used until NATS is deployed. Captures events
#              in an in-memory buffer so that tests can assert on the
#              sequence of transitions without wiring a broker. Null object
#              for the event-publisher port; to be replaced by a real NATS
#              adapter once the broker is deployed (D-008).
# @relation ignore-module
# =============================================================================

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger("platform.c4.orchestrator.events")


class NullPublisher:
    """Drops events into a structured log + an in-memory buffer."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, subject: str, envelope: dict[str, Any]) -> None:
        self.published.append((subject, envelope))
        _logger.info(
            "c4.event",
            extra={"subject": subject, "event_id": envelope.get("event_id")},
        )

    def clear(self) -> None:
        self.published.clear()
