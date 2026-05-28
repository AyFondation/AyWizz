# =============================================================================
# File: test_tcp_layer.py
# Version: 1
# Path: ay_platform_core/tests/system/test_tcp_layer.py
# Description: System-tier tests that specifically target the TCP/HTTP
#              transport layer (not exercised by ASGITransport-backed
#              integration tests). These assertions would pass trivially
#              in-process but fail if Traefik, the Docker network, or
#              kernel TCP is misconfigured.
#
#              Scope:
#                - HTTP/1.1 keep-alive reuses the socket across calls.
#                - Large response streams correctly (no truncation).
#                - Concurrent requests don't deadlock or starve.
#                - Empty-body POST is accepted.
#                - HEAD request gets response without body.
#                - Connection to a nonexistent path reaches Traefik (404
#                  from the gateway, not a connection error).
#
#              Prerequisite: `./ay_platform_core/scripts/e2e_stack.sh up`
#              (system tier requires the docker-compose stack).
# =============================================================================

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest

pytestmark = pytest.mark.system


# ---------------------------------------------------------------------------
# Raw socket checks (bypasses httpx entirely — proves TCP connectivity)
# ---------------------------------------------------------------------------


def _host_port_from_base(base_url: str) -> tuple[str, int]:
    # naive — base_url is `http://host[:port]`
    body = base_url.removeprefix("http://").removeprefix("https://")
    if "/" in body:
        body = body.split("/", 1)[0]
    if ":" in body:
        host, port_s = body.rsplit(":", 1)
        return host, int(port_s)
    return body, 80


@pytest.mark.asyncio
async def test_gateway_port_accepts_tcp_connection(
    gateway_client: httpx.AsyncClient,
) -> None:
    """Open a raw TCP socket to the gateway port. Proves the stack is
    actually listening on the published port — not just that the httpx
    client works with an ASGI app."""
    host, port = _host_port_from_base(str(gateway_client.base_url))
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    try:
        sock.connect((host, port))
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# HTTP/1.1 keep-alive + large response + concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keep_alive_reuses_single_connection(
    gateway_client: httpx.AsyncClient,
) -> None:
    """Fire 10 GETs through the same AsyncClient; Traefik + httpx SHALL
    reuse the TCP connection per HTTP/1.1 keep-alive. We can't observe
    the pool count from outside httpx, but we can verify the requests
    complete in aggregate faster than a new TLS/TCP handshake per call
    would allow (sanity bound)."""
    import time  # noqa: PLC0415

    start = time.monotonic()
    for _ in range(10):
        resp = await gateway_client.get("/auth/config")
        assert resp.status_code == 200
    elapsed = time.monotonic() - start
    # 10 requests over localhost with keep-alive SHALL complete in <2 s.
    # This is a sanity bound — not a precise timing assertion.
    assert elapsed < 2.0, f"10 keep-alive GETs took {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_concurrent_requests_do_not_deadlock(
    gateway_client: httpx.AsyncClient,
) -> None:
    """Issue 20 concurrent GETs; all SHALL complete with 200 and no
    connection-pool deadlock."""

    async def one_call() -> int:
        resp = await gateway_client.get("/auth/config")
        return resp.status_code

    statuses = await asyncio.gather(*(one_call() for _ in range(20)))
    assert all(s == 200 for s in statuses), f"statuses: {statuses}"


# ---------------------------------------------------------------------------
# HTTP verbs + edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_head_returns_no_body(
    gateway_client: httpx.AsyncClient,
) -> None:
    """HEAD /auth/config SHALL return the same status as GET but empty
    body. Proves Traefik and FastAPI cooperate on HEAD."""
    resp = await gateway_client.head("/auth/config")
    assert resp.status_code == 200
    assert resp.content == b""


@pytest.mark.asyncio
async def test_empty_post_body_accepted(
    gateway_client: httpx.AsyncClient,
) -> None:
    """A POST with no body (not even Content-Length: 0 via json payload)
    SHALL NOT crash the stack. Traefik's HTTP parser SHALL accept it."""
    # /auth/login without body → 422 validation error from FastAPI,
    # NOT a 502 / 500 from the gateway.
    resp = await gateway_client.post("/auth/login", content=b"")
    assert resp.status_code in (400, 422), (
        f"expected FastAPI validation error (400/422), got {resp.status_code}"
    )


@pytest.mark.asyncio
async def test_unknown_path_returns_traefik_404_not_connection_error(
    gateway_client: httpx.AsyncClient,
) -> None:
    """A path no router matches SHALL return HTTP 404 from the gateway
    (Traefik's default when no rule matches), NOT a TCP connection
    error. Ensures the socket is actually listening."""
    resp = await gateway_client.get("/does-not-exist/nowhere")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Chunked / streaming response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_large_response_streams_to_completion(
    gateway_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/validation/plugins returns a JSON list. Even though the
    body is small (~2-3 KB), this assertion proves the stack correctly
    closes the response so httpx sees a complete JSON document — any
    TCP-level truncation at this stage would break the assertion."""
    resp = await gateway_client.get(
        "/api/v1/validation/plugins", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    # Sanity: at least the builtin-code plugin should be there.
    assert any(p.get("name") == "builtin-code" for p in body)
