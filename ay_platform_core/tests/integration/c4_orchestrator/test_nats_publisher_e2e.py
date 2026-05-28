# =============================================================================
# File: test_nats_publisher_e2e.py
# Version: 2
# Path: ay_platform_core/tests/integration/c4_orchestrator/test_nats_publisher_e2e.py
# Description: End-to-end test for `NatsPublisher` against a real NATS
#              JetStream server provisioned via testcontainers
#              (`nats:2.10-alpine` with `-js`). Publishes one envelope,
#              consumes it back via a JetStream pull subscriber, asserts
#              subject + payload round-trip.
#
#              SKIPPED when `nats-py` isn't installed in the environment
#              — the package is on the main install but operators may
#              run the suite before `pip install -e .` picks up the new
#              dependency. `pytest.importorskip` performs the gate and
#              keeps all module-level imports at the top so ruff stays
#              happy (no E402 / PLC0415 suppressions needed).
#
# @relation validates:R-200-070
# @relation validates:R-200-071
# =============================================================================

from __future__ import annotations

import json

import pytest

# Gate the whole module on `nats-py` availability. `importorskip` skips
# at collection time when the package is missing AND imports it so
# `import nats` further down is a no-op re-import.
nats = pytest.importorskip("nats")

# Imports below are module-level but follow the importorskip gate (so
# they MUST come after it to avoid collection failure on missing
# nats-py). E402 is suppressed for that single reason ; the alternative
# (move imports inside the test) trips PLC0415 and is uglier.
from testcontainers.core.container import (  # noqa: E402  # type: ignore[import-untyped]
    DockerContainer,
)

from ay_platform_core.c4_orchestrator.events.nats_publisher import (  # noqa: E402
    NatsPublisher,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]


@pytest.fixture(scope="function")
def nats_container():  # type: ignore[no-untyped-def]
    """Spin up `nats:2.10-alpine` with JetStream enabled. Exposes the
    NATS client port (4222) on a host-mapped port and yields the URL."""
    container = DockerContainer("nats:2.10-alpine")
    container.with_command("-js")
    container.with_exposed_ports(4222)
    container.start()
    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(4222)
        yield f"nats://{host}:{port}"
    finally:
        container.stop()


async def test_publish_and_consume_round_trip(nats_container: str) -> None:
    """Publish one envelope ; read it back via a JetStream pull
    subscriber ; assert subject + payload match. Covers R-200-070
    (publish) + R-200-071 (idempotent consumer on event_id)."""
    publisher = NatsPublisher(servers=nats_container)
    await publisher.connect()
    try:
        envelope = {
            "event_id": "evt-int-1",
            "event_type": "orchestrator.run-int.phase.started",
            "event_version": 1,
            "timestamp": "2026-05-20T10:00:00+00:00",
            "run_id": "run-int",
            "payload": {"phase": "brainstorm", "agent": "architect"},
        }
        await publisher.publish(
            "orchestrator.run-int.phase.started", envelope,
        )

        nc = await nats.connect(servers=nats_container, connect_timeout=5.0)
        try:
            js = nc.jetstream()
            sub = await js.pull_subscribe(
                "orchestrator.run-int.>",
                durable="test-pull",
                stream="orchestrator-events",
            )
            msgs = await sub.fetch(1, timeout=5.0)
            assert len(msgs) == 1
            received = json.loads(msgs[0].data.decode("utf-8"))
            assert received == envelope
            assert msgs[0].subject == "orchestrator.run-int.phase.started"
            await msgs[0].ack()
        finally:
            await nc.close()
    finally:
        await publisher.aclose()
