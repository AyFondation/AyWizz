# =============================================================================
# File: test_mcp_flow.py
# Version: 1
# Path: ay_platform_core/tests/integration/c9_mcp/test_mcp_flow.py
# Description: End-to-end tests for the C9 MCP server against REAL C5 + C6
#              services (ArangoDB + MinIO testcontainers). Exercises the
#              full MCP protocol flow: initialize → tools/list → tools/call
#              round-trip for both C5 and C6 adapters.
# =============================================================================

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from ay_platform_core.c5_requirements.service import RequirementsService
from ay_platform_core.c6_validation.service import ValidationService

pytestmark = pytest.mark.integration


_HEADERS = {
    "X-User-Id": "alice",
    "X-User-Roles": "project_editor,project_owner",
}


_SEED_DOC = """---
document: 500-SPEC-DEMO
version: 1
path: projects/demo/requirements/500-SPEC-DEMO.md
language: en
status: draft
---

# Demo spec

#### R-500-001

```yaml
id: R-500-001
version: 1
status: approved
category: functional
```

The system SHALL validate inputs before processing.
"""


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def _rpc(
    client: httpx.AsyncClient,
    method: str,
    *,
    params: dict[str, Any] | None = None,
    req_id: int = 1,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
    }
    if params is not None:
        payload["params"] = params
    resp = await client.post("/api/v1/mcp", json=payload, headers=_HEADERS)
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def _call_tool(
    client: httpx.AsyncClient,
    name: str,
    arguments: dict[str, Any],
    req_id: int = 1,
) -> dict[str, Any]:
    body = await _rpc(
        client,
        "tools/call",
        params={"name": name, "arguments": arguments},
        req_id=req_id,
    )
    return body


async def _seed_c5(c5: RequirementsService) -> None:
    """Create one document with one approved entity in the `demo` project."""
    from ay_platform_core.c5_requirements.models import (  # noqa: PLC0415
        DocumentCreate,
        DocumentReplace,
    )

    await c5.create_document(
        "demo", "seeder", DocumentCreate(slug="500-SPEC-DEMO")
    )
    await c5.replace_document(
        "demo",
        "500-SPEC-DEMO",
        "seeder",
        DocumentReplace(content=_SEED_DOC),
        '"500-SPEC-DEMO@v1"',
    )


# ---------------------------------------------------------------------------
# MCP handshake
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_handshake(c9_app: FastAPI) -> None:
    async with _client(c9_app) as client:
        body = await _rpc(client, "initialize")
    assert body.get("error") is None
    result = body["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["serverInfo"]["name"] == "ay-platform-core"


@pytest.mark.asyncio
async def test_tools_list_advertises_full_roster(c9_app: FastAPI) -> None:
    async with _client(c9_app) as client:
        body = await _rpc(client, "tools/list")
    names = {t["name"] for t in body["result"]["tools"]}
    assert {
        "c5_list_entities",
        "c5_get_entity",
        "c5_list_documents",
        "c5_get_document",
        "c5_list_relations",
        "c6_list_plugins",
        "c6_trigger_validation",
        "c6_list_findings",
    }.issubset(names)


# ---------------------------------------------------------------------------
# C5 tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c5_list_entities_round_trip(
    c9_app: FastAPI, c9_c5_service: RequirementsService
) -> None:
    await _seed_c5(c9_c5_service)
    async with _client(c9_app) as client:
        body = await _call_tool(
            client, "c5_list_entities", {"project_id": "demo"}
        )
    assert body.get("error") is None
    content = json.loads(body["result"]["content"][0]["text"])
    entity_ids = [e["entity_id"] for e in content["entities"]]
    assert "R-500-001" in entity_ids


@pytest.mark.asyncio
async def test_c5_get_entity_round_trip(
    c9_app: FastAPI, c9_c5_service: RequirementsService
) -> None:
    await _seed_c5(c9_c5_service)
    async with _client(c9_app) as client:
        body = await _call_tool(
            client,
            "c5_get_entity",
            {"project_id": "demo", "entity_id": "R-500-001"},
        )
    entity = json.loads(body["result"]["content"][0]["text"])
    assert entity["entity_id"] == "R-500-001"
    assert entity["status"] == "approved"


@pytest.mark.asyncio
async def test_c5_list_documents_round_trip(
    c9_app: FastAPI, c9_c5_service: RequirementsService
) -> None:
    await _seed_c5(c9_c5_service)
    async with _client(c9_app) as client:
        body = await _call_tool(
            client, "c5_list_documents", {"project_id": "demo"}
        )
    content = json.loads(body["result"]["content"][0]["text"])
    slugs = [d["slug"] for d in content["documents"]]
    assert "500-SPEC-DEMO" in slugs


@pytest.mark.asyncio
async def test_c5_get_document_round_trip(
    c9_app: FastAPI, c9_c5_service: RequirementsService
) -> None:
    await _seed_c5(c9_c5_service)
    async with _client(c9_app) as client:
        body = await _call_tool(
            client,
            "c5_get_document",
            {"project_id": "demo", "slug": "500-SPEC-DEMO"},
        )
    document = json.loads(body["result"]["content"][0]["text"])
    assert document["slug"] == "500-SPEC-DEMO"
    assert document["body"] is not None


@pytest.mark.asyncio
async def test_c5_get_entity_404_surfaces_as_is_error(
    c9_app: FastAPI, c9_c5_service: RequirementsService
) -> None:
    async with _client(c9_app) as client:
        body = await _call_tool(
            client,
            "c5_get_entity",
            {"project_id": "demo", "entity_id": "R-500-999"},
        )
    # C5 raises HTTPException 404; MCP translates to isError=true.
    assert body.get("error") is None
    assert body["result"]["isError"] is True
    assert "HTTP 404" in body["result"]["content"][0]["text"]


# ---------------------------------------------------------------------------
# C6 tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c6_list_plugins_round_trip(c9_app: FastAPI) -> None:
    async with _client(c9_app) as client:
        body = await _call_tool(client, "c6_list_plugins", {})
    content = json.loads(body["result"]["content"][0]["text"])
    names = {p["name"] for p in content["plugins"]}
    assert "builtin-code" in names
    assert "code" in content["domains"]


@pytest.mark.asyncio
async def test_c6_trigger_and_list_findings_round_trip(
    c9_app: FastAPI, c9_c6_service: ValidationService
) -> None:
    async with _client(c9_app) as client:
        # Trigger a run of a stub check so we get a deterministic info finding.
        trigger_body = await _call_tool(
            client,
            "c6_trigger_validation",
            {
                "domain": "code",
                "project_id": "demo",
                "check_ids": ["interface-signature-drift"],
            },
        )
        assert trigger_body["result"]["isError"] is False
        trigger_content = json.loads(trigger_body["result"]["content"][0]["text"])
        run_id = trigger_content["run_id"]

        # Poll for completion (the run executes asynchronously).
        completed = False
        for _ in range(40):
            run = await c9_c6_service.get_run(run_id)
            if run.status.value == "completed":
                completed = True
                break
            await asyncio.sleep(0.05)
        assert completed, "run never completed"

        findings_body = await _call_tool(
            client,
            "c6_list_findings",
            {"run_id": run_id},
            req_id=2,
        )
    content = json.loads(findings_body["result"]["content"][0]["text"])
    check_ids = {f["check_id"] for f in content["items"]}
    assert "interface-signature-drift" in check_ids


@pytest.mark.asyncio
async def test_c6_trigger_unknown_domain_surfaces_is_error(
    c9_app: FastAPI,
) -> None:
    async with _client(c9_app) as client:
        body = await _call_tool(
            client,
            "c6_trigger_validation",
            {"domain": "presentation", "project_id": "demo"},
        )
    assert body["result"]["isError"] is True
    assert "HTTP 404" in body["result"]["content"][0]["text"]


# ---------------------------------------------------------------------------
# HTTP transport concerns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_requires_auth_header(c9_app: FastAPI) -> None:
    async with _client(c9_app) as client:
        resp = await client.post(
            "/api/v1/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_tools_endpoint_surfaces_full_roster(c9_app: FastAPI) -> None:
    async with _client(c9_app) as client:
        resp = await client.get("/api/v1/mcp/tools", headers=_HEADERS)
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()}
    assert len(names) == 8


@pytest.mark.asyncio
async def test_health_endpoint(c9_app: FastAPI) -> None:
    async with _client(c9_app) as client:
        resp = await client.get("/api/v1/mcp/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_raw_parse_error_returns_json_rpc_error(c9_app: FastAPI) -> None:
    async with _client(c9_app) as client:
        resp = await client.post(
            "/api/v1/mcp",
            content=b"not json",
            headers={**_HEADERS, "Content-Type": "application/json"},
        )
    assert resp.status_code == 200  # transport-level OK, JSON-RPC error inside
    body = resp.json()
    assert body.get("error") is not None
    assert body["error"]["code"] == -32700  # ERROR_PARSE
