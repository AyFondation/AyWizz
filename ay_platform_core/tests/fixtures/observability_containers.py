# =============================================================================
# File: observability_containers.py
# Version: 1
# Path: ay_platform_core/tests/fixtures/observability_containers.py
# Description: Testcontainers fixtures for the observability adapters
#              (R-100-124): a single-node Grafana Loki and a
#              single-node Elasticsearch. Session-scoped to amortise
#              startup cost; tests use unique trace_ids / indices to
#              avoid cross-pollination.
# =============================================================================

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, cast

import httpx
import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

# Pinned tags. Loki 3.x uses the same query API shape as 2.x; ES 8.x is the
# current LTS line.
LOKI_IMAGE = "grafana/loki:3.3.2"
ELASTICSEARCH_IMAGE = "docker.elastic.co/elasticsearch/elasticsearch:8.16.1"


@dataclass(frozen=True)
class LokiEndpoint:
    base_url: str


@dataclass(frozen=True)
class ElasticsearchEndpoint:
    base_url: str


def _wait_for_status(url: str, *, expected: int = 200, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    last_err: str | None = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=2.0)
            if resp.status_code == expected:
                return
            last_err = f"HTTP {resp.status_code}: {resp.text[:120]}"
        except httpx.RequestError as exc:
            last_err = f"{type(exc).__name__}: {exc}"
        time.sleep(1.0)
    raise RuntimeError(f"{url} not ready after {timeout_s}s: {last_err}")


# ---------------------------------------------------------------------------
# Loki
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def loki_container() -> Iterator[LokiEndpoint]:
    """Single-node Loki backed by filesystem storage (default config).

    Loki's stock `local-config.yaml` is sufficient for adapter tests:
    push via `/loki/api/v1/push`, query via `/loki/api/v1/query_range`.
    Newly-pushed lines become queryable within a couple of seconds —
    tests SHOULD poll rather than assume immediate visibility.
    """
    container = DockerContainer(LOKI_IMAGE)
    container.with_exposed_ports(3100)
    with container as started:
        host = cast(Any, started).get_container_host_ip()
        port = int(cast(Any, started).get_exposed_port(3100))
        base_url = f"http://{host}:{port}"
        # Loki exposes /ready once internal subsystems are running.
        _wait_for_status(f"{base_url}/ready", timeout_s=90.0)
        yield LokiEndpoint(base_url=base_url)


# ---------------------------------------------------------------------------
# Elasticsearch
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def elasticsearch_container() -> Iterator[ElasticsearchEndpoint]:
    """Single-node Elasticsearch with security disabled.

    Disabling xpack.security keeps the test surface narrow (no TLS,
    no token plumbing). Production deployments enable it; the adapter's
    Basic-Auth plumbing is unit-tested separately.
    """
    container = (
        DockerContainer(ELASTICSEARCH_IMAGE)
        .with_env("discovery.type", "single-node")
        .with_env("xpack.security.enabled", "false")
        .with_env("xpack.security.http.ssl.enabled", "false")
        .with_env("ES_JAVA_OPTS", "-Xms512m -Xmx512m")
        .with_exposed_ports(9200)
        .waiting_for(LogMessageWaitStrategy("started").with_startup_timeout(180))
    )
    with container as started:
        host = cast(Any, started).get_container_host_ip()
        port = int(cast(Any, started).get_exposed_port(9200))
        base_url = f"http://{host}:{port}"
        # After the "started" log line, poll cluster health until yellow.
        _wait_for_status(
            f"{base_url}/_cluster/health?wait_for_status=yellow&timeout=30s",
            timeout_s=120.0,
        )
        yield ElasticsearchEndpoint(base_url=base_url)
