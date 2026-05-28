# =============================================================================
# File: nats_publisher.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/events/nats_publisher.py
# Description: Real NATS event publisher for the C4 orchestrator (R-200-070,
#              R-200-071). Publishes the envelope to JetStream subjects of
#              the form `orchestrator.{run_id}.<object>.<action>` with
#              at-least-once delivery. Consumers idempotent on `event_id`
#              per spec § 4.8 rationale.
#
#              Connection lifecycle :
#                - `connect()` opens the NATS connection AND ensures the
#                  `orchestrator-events` JetStream stream exists (idempotent
#                  ; safe on every replica start).
#                - `publish()` publishes a single envelope as JSON ; failures
#                  surface as `RuntimeError`. The orchestrator catches +
#                  logs WARN to keep state-machine progression decoupled
#                  from NATS health (the trace ledger compensates per
#                  R-200-200).
#                - `aclose()` drains in-flight publishes + closes the
#                  connection (called from the FastAPI lifespan).
#
#              The `nats-py` import is LAZY : missing dependency raises a
#              clear ImportError at construction time only ; the module
#              loads cleanly even when nats-py isn't installed, so unit
#              tests of the orchestrator that use NullPublisher don't
#              pay the import cost.
#
# @relation implements:R-200-070
# @relation implements:R-200-071
# =============================================================================

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover — typing-only import
    from nats.aio.client import Client as NATSClient
    from nats.js.client import JetStreamContext

_log = logging.getLogger("c4_orchestrator.events.nats")

_STREAM_NAME = "orchestrator-events"
_SUBJECTS_PREFIX = "orchestrator."


class NatsPublisher:
    """JetStream-backed publisher for orchestrator events.

    Single instance per C4 replica (the FastAPI lifespan creates and
    closes it). Thread-safe under asyncio (the underlying nats client
    serialises publishes).
    """

    def __init__(self, *, servers: str | list[str], connect_timeout: float = 5.0) -> None:
        self._servers = servers
        self._connect_timeout = connect_timeout
        self._nc: NATSClient | None = None
        self._js: JetStreamContext | None = None

    async def connect(self) -> None:
        """Open the connection AND ensure the JetStream stream exists.
        Idempotent : the stream-add call surfaces "stream already exists"
        as a no-op."""
        try:
            import nats  # noqa: PLC0415 — lazy : optional dependency
            from nats.js.errors import BadRequestError  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "nats-py is required for NatsPublisher ; "
                "install ay_platform_core[all] or `pip install nats-py>=2.6`",
            ) from exc
        self._nc = await nats.connect(
            servers=self._servers,
            connect_timeout=self._connect_timeout,
            name="c4_orchestrator",
        )
        self._js = self._nc.jetstream()
        # Create the stream if it doesn't exist. JetStream raises a
        # BadRequestError when the stream already exists with a
        # compatible config — we catch + ignore that specific case.
        try:
            await self._js.add_stream(
                name=_STREAM_NAME,
                subjects=[f"{_SUBJECTS_PREFIX}>"],
                # at-least-once is the JetStream default ; we set retention
                # to limits-based with a generous max_age so consumers
                # that disconnect briefly can catch up. v1 defaults are
                # safe — operators tune via the admin surface.
                max_age=86_400 * 7,  # 7 days
            )
            _log.info("created JetStream stream %s", _STREAM_NAME)
        except BadRequestError as exc:
            # Stream already exists ; reuse as-is. A WARN-level log
            # would scare on every replica restart — keep at DEBUG.
            _log.debug("JetStream stream %s already exists: %s", _STREAM_NAME, exc)

    async def publish(self, subject: str, envelope: dict[str, Any]) -> None:
        """Publish one envelope to the given subject. Raises RuntimeError
        when the connection wasn't opened or the publish ack failed.
        The orchestrator catches + logs ; the trace ledger keeps the
        audit trail intact regardless."""
        if self._js is None:
            raise RuntimeError(
                "NatsPublisher.publish called before connect() — "
                "check the FastAPI lifespan wiring",
            )
        if not subject.startswith(_SUBJECTS_PREFIX):
            raise ValueError(
                f"subject {subject!r} must start with {_SUBJECTS_PREFIX!r} "
                "(R-200-070)",
            )
        payload = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
        try:
            await self._js.publish(subject, payload)
        except Exception as exc:  # nats raises various subclasses ; collapse to RuntimeError
            raise RuntimeError(f"JetStream publish to {subject!r} failed: {exc}") from exc

    async def aclose(self) -> None:
        """Drain pending publishes and close the connection."""
        if self._nc is not None:
            try:
                await self._nc.drain()
            except Exception as exc:  # drain failures are non-fatal at shutdown
                _log.warning("NATS drain failed during aclose: %s", exc)
            finally:
                self._nc = None
                self._js = None
