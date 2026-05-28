# =============================================================================
# File: test_real_chain.py
# Version: 1
# Path: ay_platform_core/tests/integration/c9_mcp/test_real_chain.py
# Description: Real-HTTP-chain integration tests for C9. Complements
#              test_mcp_flow.py (which wires C5/C6 as in-process Python
#              objects) by exercising the full path:
#                   C9 tool handler
#                   → RemoteRequirementsService / RemoteValidationService
#                   → httpx.ASGITransport
#                   → real C5 / C6 FastAPI router (auth middleware, Pydantic
#                     parsing, route dispatch)
#                   → real service
#                   → real ArangoDB + MinIO (testcontainers)
#
#              ASGITransport gives us genuine HTTP request/response
#              semantics without opening a socket — the same payloads that
#              would traverse Docker's network in production.
# =============================================================================

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from ay_platform_core.c5_requirements.models import DocumentCreate, DocumentReplace
from ay_platform_core.c5_requirements.router import router as c5_router
from ay_platform_core.c5_requirements.service import RequirementsService
from ay_platform_core.c6_validation.router import router as c6_router
from ay_platform_core.c6_validation.service import ValidationService
from ay_platform_core.c9_mcp.config import MCPConfig
from ay_platform_core.c9_mcp.remote import (
    RemoteRequirementsService,
    RemoteValidationService,
)
from ay_platform_core.c9_mcp.router import router as c9_router
from ay_platform_core.c9_mcp.server import MCPServer
from ay_platform_core.c9_mcp.tools.base import build_default_toolset

pytestmark = pytest.mark.integration


# Forward-auth headers as C1 (Traefik) would inject them after validating
# the bearer token with C2. In the real chain, every inter-component call
# carries them; here we set them as default headers on the ASGI-backed
# httpx clients so the downstream FastAPI routers see them.
_FWD_AUTH_HEADERS = {
    "X-User-Id": "alice",
    "X-User-Roles": "project_editor,project_owner,admin",
    "X-Tenant-Id": "t-demo",
}


_SEED_DOC = """---
document: 500-SPEC-CHAIN
version: 1
path: projects/demo/requirements/500-SPEC-CHAIN.md
language: en
status: draft
---

# Chain-test spec

#### R-500-100

```yaml
id: R-500-100
version: 1
status: approved
category: functional
```

The platform SHALL expose C5 reads through C9 via real HTTP.
"""


# ---------------------------------------------------------------------------
# ASGI clients — real FastAPI apps for C5 + C6, wrapped in httpx
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def c5_asgi_client(
    c9_c5_service: RequirementsService,
) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(c5_router)
    app.state.requirements_service = c9_c5_service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://c5",
        headers=_FWD_AUTH_HEADERS,
    ) as client:
        yield client


@pytest_asyncio.fixture
async def c6_asgi_client(
    c9_c6_service: ValidationService,
) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(c6_router)
    app.state.validation_service = c9_c6_service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://c6",
        headers=_FWD_AUTH_HEADERS,
    ) as client:
        yield client


@pytest_asyncio.fixture
async def c9_real_chain_app(
    c5_asgi_client: httpx.AsyncClient,
    c6_asgi_client: httpx.AsyncClient,
) -> FastAPI:
    """C9 app with tools pointed at real C5/C6 over ASGI HTTP."""
    remote_c5 = RemoteRequirementsService(
        base_url="http://c5", client=c5_asgi_client
    )
    remote_c6 = RemoteValidationService(
        base_url="http://c6", client=c6_asgi_client
    )
    tools = build_default_toolset(c5_service=remote_c5, c6_service=remote_c6)
    server = MCPServer(MCPConfig(), tools)
    app = FastAPI()
    app.include_router(c9_router)
    app.state.mcp_server = server
    return app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _call_tool(
    c9_client: httpx.AsyncClient,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    resp = await c9_client.post(
        "/api/v1/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def _success_content(body: dict[str, Any], tool_name: str) -> dict[str, Any]:
    assert body.get("error") is None, f"{tool_name}: transport error: {body}"
    result = body["result"]
    assert result["isError"] is False, (
        f"{tool_name}: isError=True — {result['content']}"
    )
    parsed: dict[str, Any] = json.loads(result["content"][0]["text"])
    return parsed


async def _seed_c5(c5: RequirementsService) -> None:
    await c5.create_document(
        "demo", "seeder", DocumentCreate(slug="500-SPEC-CHAIN")
    )
    await c5.replace_document(
        "demo",
        "500-SPEC-CHAIN",
        "seeder",
        DocumentReplace(content=_SEED_DOC),
        '"500-SPEC-CHAIN@v1"',
    )


# ---------------------------------------------------------------------------
# Tests — C5 reads through real HTTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_chain_c5_list_entities(
    c9_real_chain_app: FastAPI, c9_c5_service: RequirementsService
) -> None:
    """C9 → ASGI HTTP → real C5 router → real C5 service → real Arango.

    Validates the request actually traverses the FastAPI auth middleware,
    the Pydantic query validation, and the AQL query path — none of which
    are exercised by the in-process-object wiring in test_mcp_flow.py.
    """
    await _seed_c5(c9_c5_service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c9_real_chain_app),
        base_url="http://c9",
        headers=_FWD_AUTH_HEADERS,
    ) as c9_client:
        body = await _call_tool(
            c9_client, "c5_list_entities", {"project_id": "demo"}
        )
    content = _success_content(body, "c5_list_entities")
    entity_ids = [e["entity_id"] for e in content["entities"]]
    assert "R-500-100" in entity_ids


@pytest.mark.asyncio
async def test_real_chain_c5_get_entity(
    c9_real_chain_app: FastAPI, c9_c5_service: RequirementsService
) -> None:
    await _seed_c5(c9_c5_service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c9_real_chain_app),
        base_url="http://c9",
        headers=_FWD_AUTH_HEADERS,
    ) as c9_client:
        body = await _call_tool(
            c9_client,
            "c5_get_entity",
            {"project_id": "demo", "entity_id": "R-500-100"},
        )
    content = _success_content(body, "c5_get_entity")
    assert content["entity_id"] == "R-500-100"
    assert content["status"] == "approved"


@pytest.mark.asyncio
async def test_real_chain_c5_unknown_entity_surfaces_as_error(
    c9_real_chain_app: FastAPI, c9_c5_service: RequirementsService
) -> None:
    """Error propagation through the real HTTP chain: C5 returns 404,
    RemoteRequirementsService raises HTTPException(404), C9 translates to
    isError=true envelope with the status embedded in the message."""
    await _seed_c5(c9_c5_service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c9_real_chain_app),
        base_url="http://c9",
        headers=_FWD_AUTH_HEADERS,
    ) as c9_client:
        body = await _call_tool(
            c9_client,
            "c5_get_entity",
            {"project_id": "demo", "entity_id": "R-500-999"},
        )
    assert body.get("error") is None
    assert body["result"]["isError"] is True
    assert "404" in body["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_real_chain_c5_list_documents(
    c9_real_chain_app: FastAPI, c9_c5_service: RequirementsService
) -> None:
    await _seed_c5(c9_c5_service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c9_real_chain_app),
        base_url="http://c9",
        headers=_FWD_AUTH_HEADERS,
    ) as c9_client:
        body = await _call_tool(
            c9_client, "c5_list_documents", {"project_id": "demo"}
        )
    content = _success_content(body, "c5_list_documents")
    slugs = [d["slug"] for d in content["documents"]]
    assert "500-SPEC-CHAIN" in slugs


@pytest.mark.asyncio
async def test_real_chain_c5_list_relations_empty(
    c9_real_chain_app: FastAPI, c9_c5_service: RequirementsService
) -> None:
    """Seeded entity has no outgoing relations — the list SHALL be
    returned as an empty array, not a 404."""
    await _seed_c5(c9_c5_service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c9_real_chain_app),
        base_url="http://c9",
        headers=_FWD_AUTH_HEADERS,
    ) as c9_client:
        body = await _call_tool(
            c9_client,
            "c5_list_relations",
            {"project_id": "demo", "source_id": "R-500-100"},
        )
    content = _success_content(body, "c5_list_relations")
    assert content["relations"] == []


# ---------------------------------------------------------------------------
# Tests — C6 reads + trigger through real HTTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_chain_c6_list_plugins(c9_real_chain_app: FastAPI) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c9_real_chain_app),
        base_url="http://c9",
        headers=_FWD_AUTH_HEADERS,
    ) as c9_client:
        body = await _call_tool(c9_client, "c6_list_plugins", {})
    content = _success_content(body, "c6_list_plugins")
    names = {p["name"] for p in content["plugins"]}
    assert "builtin-code" in names
    assert "code" in content["domains"]


@pytest.mark.asyncio
async def test_real_chain_c6_trigger_and_list_findings(
    c9_real_chain_app: FastAPI, c9_c6_service: ValidationService
) -> None:
    """End-to-end: C9 tool call triggers a C6 run over HTTP, polls until
    completion (poll is against the same HTTP layer), then reads findings.

    Exercises: MCP JSON-RPC → ASGI HTTP → C6 router → C6 service → Arango
    for both POST (trigger) and GET (findings) legs.
    """
    import asyncio  # noqa: PLC0415 — local only

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c9_real_chain_app),
        base_url="http://c9",
        headers=_FWD_AUTH_HEADERS,
    ) as c9_client:
        trigger_body = await _call_tool(
            c9_client,
            "c6_trigger_validation",
            {
                "domain": "code",
                "project_id": "demo",
                "check_ids": ["interface-signature-drift"],
            },
        )
        trigger_content = _success_content(trigger_body, "c6_trigger_validation")
        run_id = trigger_content["run_id"]

        # The trigger launches async work inside the C6 service; poll its
        # state through the real C6 service facade (the real-chain
        # equivalent of observing a background task).
        completed = False
        for _ in range(60):
            run = await c9_c6_service.get_run(run_id)
            if run.status.value == "completed":
                completed = True
                break
            await asyncio.sleep(0.05)
        assert completed, f"run {run_id} never completed"

        findings_body = await _call_tool(
            c9_client, "c6_list_findings", {"run_id": run_id}
        )
    content = _success_content(findings_body, "c6_list_findings")
    check_ids = {f["check_id"] for f in content["items"]}
    assert "interface-signature-drift" in check_ids


@pytest.mark.asyncio
async def test_real_chain_c6_unknown_run_surfaces_as_error(
    c9_real_chain_app: FastAPI,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c9_real_chain_app),
        base_url="http://c9",
        headers=_FWD_AUTH_HEADERS,
    ) as c9_client:
        body = await _call_tool(
            c9_client, "c6_list_findings", {"run_id": "nonexistent-run-xyz"}
        )
    assert body.get("error") is None
    assert body["result"]["isError"] is True
    assert "404" in body["result"]["content"][0]["text"]
