# =============================================================================
# File: test_nats_publisher.py
# Version: 1
# Path: ay_platform_core/tests/unit/c4_orchestrator/test_nats_publisher.py
# Description: Unit tests for the NATS event publisher (R-200-070,
#              R-200-071). Use a fake nats client injected at construction
#              so the test exercises envelope serialisation, subject
#              prefix validation, and lifecycle (connect / publish /
#              aclose) without spinning a real server.
#              Integration against a real NATS server lives in
#              tests/integration/c4_orchestrator/test_nats_publisher_e2e.py
#              (requires testcontainers, skipped if nats-py absent).
#
# @relation validates:R-200-070
# @relation validates:R-200-071
# =============================================================================

from __future__ import annotations

import json
from typing import Any

import pytest

from ay_platform_core.c4_orchestrator.events.nats_publisher import NatsPublisher


class _FakeJetStream:
    """Captures publish calls without a real NATS server."""

    def __init__(self) -> None:
        self.published: list[tuple[str, bytes]] = []
        self.streams_added: list[dict[str, Any]] = []
        self.fail_on_publish = False

    async def add_stream(self, **kwargs: Any) -> None:
        self.streams_added.append(kwargs)

    async def publish(self, subject: str, payload: bytes) -> None:
        if self.fail_on_publish:
            raise RuntimeError("simulated JetStream publish failure")
        self.published.append((subject, payload))


@pytest.fixture
def publisher_with_fake_js() -> tuple[NatsPublisher, _FakeJetStream]:
    """Construct a NatsPublisher and inject a fake JetStream client
    so we can exercise `publish` without calling `connect()`."""
    pub = NatsPublisher(servers="nats://test:4222")
    js = _FakeJetStream()
    # Inject a structural fake for the real JetStreamContext (now type-strict
    # since the dep refresh shipped nats-py type info) — a test seam, not a
    # production assignment.
    pub._js = js  # type: ignore[assignment]
    return pub, js


class TestSubjectValidation:
    @pytest.mark.asyncio
    async def test_subject_must_start_with_orchestrator_prefix(
        self,
        publisher_with_fake_js: tuple[NatsPublisher, _FakeJetStream],
    ) -> None:
        pub, _ = publisher_with_fake_js
        with pytest.raises(ValueError, match=r"orchestrator\."):
            await pub.publish("c3.something.else", {"event_id": "x"})

    @pytest.mark.asyncio
    async def test_publish_before_connect_raises(self) -> None:
        pub = NatsPublisher(servers="nats://test:4222")
        with pytest.raises(RuntimeError, match="before connect"):
            await pub.publish("orchestrator.run-1.phase.started", {})


class TestEnvelopeSerialisation:
    @pytest.mark.asyncio
    async def test_envelope_payload_round_trip(
        self,
        publisher_with_fake_js: tuple[NatsPublisher, _FakeJetStream],
    ) -> None:
        pub, js = publisher_with_fake_js
        envelope = {
            "event_id": "evt-1",
            "event_type": "orchestrator.run-1.phase.started",
            "event_version": 1,
            "timestamp": "2026-05-20T10:00:00+00:00",
            "run_id": "run-1",
            "payload": {"phase": "brainstorm", "agent": "architect"},
        }
        await pub.publish("orchestrator.run-1.phase.started", envelope)
        assert len(js.published) == 1
        subject, raw = js.published[0]
        assert subject == "orchestrator.run-1.phase.started"
        assert json.loads(raw) == envelope

    @pytest.mark.asyncio
    async def test_publish_failure_collapses_to_runtimeerror(
        self,
        publisher_with_fake_js: tuple[NatsPublisher, _FakeJetStream],
    ) -> None:
        pub, js = publisher_with_fake_js
        js.fail_on_publish = True
        with pytest.raises(RuntimeError, match="JetStream publish"):
            await pub.publish("orchestrator.run-1.phase.started", {"x": 1})


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_aclose_without_connect_noop(self) -> None:
        pub = NatsPublisher(servers="nats://test:4222")
        await pub.aclose()  # SHOULD NOT raise even when never connected.


class TestImportSafety:
    def test_construct_without_nats_installed(self) -> None:
        """Module-level import is lazy ; construction without an open
        connection MAY succeed even when `nats-py` isn't installed.
        ImportError is raised by `connect()`, not by `__init__()`."""
        pub = NatsPublisher(servers="nats://test:4222")
        assert pub._servers == "nats://test:4222"
