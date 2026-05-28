# =============================================================================
# File: null_publisher.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/events/null_publisher.py
# Description: No-op / logging publisher used until NATS is deployed.
#              Captures published events in memory for test assertions and
#              emits a structured log line so that dev-time traffic is
#              observable. Swap for a NATS JetStream adapter when infra
#              lands (S-2 in the C5 plan). Null object for the
#              event-publisher port (D-008).
# @relation ignore-module
# =============================================================================

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger("platform.c5.requirements.events")


class NullPublisher:
    """Drops events into a structured log and an in-memory buffer.

    The buffer is public so that tests can inspect the event stream without
    wiring a real broker. Not suitable for production (not thread-safe, no
    retention).
    """

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, subject: str, envelope: dict[str, Any]) -> None:
        self.published.append((subject, envelope))
        _logger.info(
            "c5.event",
            extra={"subject": subject, "event_id": envelope.get("event_id")},
        )

    def clear(self) -> None:
        self.published.clear()
