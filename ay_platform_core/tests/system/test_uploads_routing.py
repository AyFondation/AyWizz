# =============================================================================
# File: test_uploads_routing.py
# Version: 1
# Path: ay_platform_core/tests/system/test_uploads_routing.py
# Description: System tests for C12 (n8n). Validates that Traefik actually
#              routes /uploads/* to the n8n container — we do NOT require a
#              seeded workflow to exist. The goal is to confirm the path is
#              plumbed: a non-existent webhook SHALL return an n8n error
#              body (JSON with `code`/`message`), not a Traefik 502.
#
#              R-100-080..087 cover the functional behaviour of the C12+C7
#              ingestion pipeline — those are exercised in future tests
#              once sample workflows are seeded into n8n (v1.5 follow-up).
# =============================================================================

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.system


@pytest.mark.asyncio
async def test_uploads_path_reaches_n8n(
    gateway_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Hitting a non-existent webhook path SHALL produce an n8n-typed error,
    not a Traefik-typed 5xx — proving the route traversal is correct.
    """
    resp = await gateway_client.post(
        "/uploads/does-not-exist",
        json={"probe": True},
        headers=auth_headers,
    )
    # n8n returns 404 (webhook not registered) with a JSON body. Traefik
    # would return 502/503 if the backend were unreachable.
    assert resp.status_code in (404, 405), (
        f"expected n8n 404/405 (webhook unknown), got {resp.status_code}: "
        f"{resp.text!r}"
    )
    # n8n error bodies are JSON with a `message` field.
    try:
        body = resp.json()
    except ValueError:
        pytest.fail(f"expected JSON error body from n8n, got: {resp.text!r}")
    assert isinstance(body, dict), f"n8n body not a dict: {body!r}"


@pytest.mark.asyncio
async def test_uploads_anonymous_is_401_via_traefik(
    gateway_client: httpx.AsyncClient,
) -> None:
    """forward-auth-c2 gates /uploads/* like any other /api/v1 surface."""
    resp = await gateway_client.post("/uploads/anything", json={})
    assert resp.status_code == 401, (
        f"expected forward-auth to 401 on /uploads without bearer, got "
        f"{resp.status_code}: {resp.text!r}"
    )
