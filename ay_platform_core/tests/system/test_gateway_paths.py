# =============================================================================
# File: test_gateway_paths.py
# Version: 1
# Path: ay_platform_core/tests/system/test_gateway_paths.py
# Description: System tests that run against the running docker-compose
#              stack through Traefik (http://localhost). These tests
#              validate that every public API surface is reachable and
#              behaves correctly through the ingress path — the same path
#              a production client would take.
#
#              Prerequisite: `ay_platform_core/scripts/e2e_stack.sh up`
#                            `ay_platform_core/scripts/e2e_stack.sh seed`
#              to bring the stack up and inject deterministic data.
# =============================================================================

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.system


# ---------------------------------------------------------------------------
# C2 — Auth (public, no forward-auth)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c2_auth_config_responds(gateway_client: httpx.AsyncClient) -> None:
    resp = await gateway_client.get("/auth/config")
    assert resp.status_code == 200
    assert "mode" in resp.json() or "auth_mode" in resp.json()


@pytest.mark.asyncio
async def test_c2_login_issues_token(gateway_client: httpx.AsyncClient) -> None:
    """Login with the bootstrap admin (alice / seed-password) returns a JWT.

    In `local` auth mode the password is verified against the argon2id
    hash stored at lifespan-bootstrap; the seed password is the one the
    `_ensure_local_admin` function used (env: `C2_LOCAL_ADMIN_PASSWORD`,
    default `seed-password` in `.env.test`).
    """
    resp = await gateway_client.post(
        "/auth/login",
        json={"username": "alice", "password": "seed-password"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("access_token")
    assert body.get("token_type", "bearer").lower() == "bearer"


@pytest.mark.asyncio
async def test_c2_verify_requires_bearer(
    gateway_client: httpx.AsyncClient,
) -> None:
    """/auth/verify without Authorization SHALL reject with 401."""
    resp = await gateway_client.get("/auth/verify")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Forward-auth enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c5_endpoint_rejects_anonymous_via_traefik(
    gateway_client: httpx.AsyncClient,
) -> None:
    """Without a bearer token, Traefik's forward-auth middleware must 401."""
    resp = await gateway_client.get(
        "/api/v1/projects/demo/requirements/documents"
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_c6_endpoint_rejects_anonymous_via_traefik(
    gateway_client: httpx.AsyncClient,
) -> None:
    resp = await gateway_client.get("/api/v1/validation/plugins")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_c9_endpoint_rejects_anonymous_via_traefik(
    gateway_client: httpx.AsyncClient,
) -> None:
    resp = await gateway_client.post(
        "/api/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# C5 Requirements — seeded data is reachable through Traefik
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c5_lists_seeded_document(
    gateway_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await gateway_client.get(
        "/api/v1/projects/demo/requirements/documents",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    slugs = [d["slug"] for d in resp.json().get("documents", [])]
    assert "900-SPEC-DEMO" in slugs, (
        "seeded document missing — did you run "
        "`ay_platform_core/scripts/e2e_stack.sh seed`?"
    )


@pytest.mark.asyncio
async def test_c5_gets_seeded_entity(
    gateway_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await gateway_client.get(
        "/api/v1/projects/demo/requirements/entities/R-900-001",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["entity_id"] == "R-900-001"
    assert body["status"] == "approved"


# ---------------------------------------------------------------------------
# C6 Validation — exposed through Traefik
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c6_plugins_endpoint(
    gateway_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await gateway_client.get(
        "/api/v1/validation/plugins", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    names = {p["name"] for p in resp.json()}
    assert "builtin-code" in names


@pytest.mark.asyncio
async def test_c6_trigger_stub_run_and_poll(
    gateway_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Triggers a pure-stub run (interface-signature-drift: always one info
    finding) and polls until it reaches `completed`. Exercises the async
    execution path inside a real C6 container + real ArangoDB + real MinIO
    snapshot write.
    """
    import asyncio  # noqa: PLC0415 — local only

    trigger = await gateway_client.post(
        "/api/v1/validation/runs",
        json={
            "domain": "code",
            "project_id": "demo",
            "check_ids": ["interface-signature-drift"],
        },
        headers=auth_headers,
    )
    assert trigger.status_code == 202, trigger.text
    run_id = trigger.json()["run_id"]

    for _ in range(60):
        detail = await gateway_client.get(
            f"/api/v1/validation/runs/{run_id}", headers=auth_headers
        )
        assert detail.status_code == 200
        if detail.json()["status"] == "completed":
            break
        await asyncio.sleep(0.5)
    else:
        pytest.fail(f"run {run_id} never completed")

    findings = await gateway_client.get(
        f"/api/v1/validation/runs/{run_id}/findings", headers=auth_headers
    )
    assert findings.status_code == 200
    check_ids = {f["check_id"] for f in findings.json()["items"]}
    assert "interface-signature-drift" in check_ids


# ---------------------------------------------------------------------------
# C7 Memory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c7_quota_endpoint(
    gateway_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    """C7's quota endpoint reports `bytes_used` / `bytes_limit` /
    `chunk_count` / `project_id`. Test asserts the schema on the wire,
    not just one possible alias — the prior `used_bytes` / `available_bytes`
    expectation drifted from the actual response."""
    resp = await gateway_client.get(
        "/api/v1/memory/projects/demo/quota", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "bytes_used" in body
    assert "bytes_limit" in body
    assert body["bytes_used"] >= 0
    assert body["bytes_limit"] > 0


# ---------------------------------------------------------------------------
# C9 MCP — JSON-RPC round trip through Traefik
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c9_mcp_initialize(
    gateway_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await gateway_client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("error") is None
    assert body["result"]["serverInfo"]["name"] == "ay-platform-core"


@pytest.mark.asyncio
async def test_c9_mcp_tools_list_includes_c5_and_c6(
    gateway_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await gateway_client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    names = {t["name"] for t in resp.json()["result"]["tools"]}
    # Coverage check against the ratified roster.
    assert {"c5_list_entities", "c5_get_entity", "c6_list_plugins"}.issubset(names)


@pytest.mark.asyncio
async def test_c9_mcp_c5_list_entities_roundtrip(
    gateway_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    """End-to-end: client → Traefik → C9 → C9 remote adapter → Traefik (?) →
    C5. v1 wiring: C9 calls C5 directly over the internal docker network
    (not through Traefik, by design — Traefik is the PUBLIC ingress).
    """
    import json  # noqa: PLC0415

    resp = await gateway_client.post(
        "/api/v1/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "c5_list_entities",
                "arguments": {"project_id": "demo"},
            },
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("error") is None, body
    assert body["result"]["isError"] is False
    content = json.loads(body["result"]["content"][0]["text"])
    entity_ids = {e["entity_id"] for e in content["entities"]}
    assert "R-900-001" in entity_ids, (
        "seeded entity missing from C5 via C9 — stack may not be seeded"
    )
