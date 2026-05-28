# =============================================================================
# File: test_mcp_tool_flows.py
# Version: 1
# Path: ay_platform_core/tests/system/test_mcp_tool_flows.py
# Description: System-tier coverage of the 8 MCP tools through Traefik. Each
#              test hits `POST /api/v1/mcp` with a realistic tools/call
#              payload against the real C9 container (which in turn calls
#              C5/C6 over the internal docker network). Confirms the
#              round-trip path is intact for every tool in the v1 roster.
#
#              Prerequisite: `./ay_platform_core/scripts/e2e_stack.sh up`
#              then `… seed` so the `demo` project carries R-900-001 +
#              900-SPEC-DEMO.
# =============================================================================

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.system


async def _call(
    client: httpx.AsyncClient,
    auth: dict[str, str],
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Helper: issue a tools/call through Traefik and return the envelope."""
    resp = await client.post(
        "/api/v1/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def _assert_success(body: dict[str, Any], tool_name: str) -> dict[str, Any]:
    """Validate an MCP envelope is a successful tool result and return the
    parsed content body.
    """
    assert body.get("error") is None, f"{tool_name} transport error: {body}"
    result = body["result"]
    assert result["isError"] is False, (
        f"{tool_name} returned isError=True: {result['content']}"
    )
    parsed: dict[str, Any] = json.loads(result["content"][0]["text"])
    return parsed


def _assert_is_error(body: dict[str, Any], tool_name: str) -> str:
    """Validate an MCP envelope reports a domain-side error and return the
    error text.
    """
    assert body.get("error") is None, (
        f"{tool_name} wrapped a domain error as transport error: {body}"
    )
    result = body["result"]
    assert result["isError"] is True, (
        f"{tool_name} expected isError=True, got result: {result!r}"
    )
    text: str = result["content"][0]["text"]
    return text


# ---------------------------------------------------------------------------
# C5 tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_c5_list_entities_happy_path(
    gateway_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    body = await _call(
        gateway_client, auth_headers, "c5_list_entities", {"project_id": "demo"}
    )
    content = _assert_success(body, "c5_list_entities")
    entity_ids = [e["entity_id"] for e in content["entities"]]
    assert "R-900-001" in entity_ids


@pytest.mark.asyncio
async def test_tool_c5_list_entities_invalid_status_is_domain_error(
    gateway_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    body = await _call(
        gateway_client,
        auth_headers,
        "c5_list_entities",
        {"project_id": "demo", "status": "hallucinated"},
    )
    text = _assert_is_error(body, "c5_list_entities")
    assert "status" in text.lower()


@pytest.mark.asyncio
async def test_tool_c5_get_entity_happy_path(
    gateway_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    body = await _call(
        gateway_client,
        auth_headers,
        "c5_get_entity",
        {"project_id": "demo", "entity_id": "R-900-001"},
    )
    content = _assert_success(body, "c5_get_entity")
    assert content["entity_id"] == "R-900-001"
    assert content["status"] == "approved"


@pytest.mark.asyncio
async def test_tool_c5_get_entity_404_surfaces_as_error(
    gateway_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    body = await _call(
        gateway_client,
        auth_headers,
        "c5_get_entity",
        {"project_id": "demo", "entity_id": "R-900-999"},
    )
    text = _assert_is_error(body, "c5_get_entity")
    assert "404" in text


@pytest.mark.asyncio
async def test_tool_c5_list_documents_happy_path(
    gateway_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    body = await _call(
        gateway_client,
        auth_headers,
        "c5_list_documents",
        {"project_id": "demo"},
    )
    content = _assert_success(body, "c5_list_documents")
    slugs = [d["slug"] for d in content["documents"]]
    assert "900-SPEC-DEMO" in slugs


@pytest.mark.asyncio
async def test_tool_c5_get_document_happy_path(
    gateway_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    body = await _call(
        gateway_client,
        auth_headers,
        "c5_get_document",
        {"project_id": "demo", "slug": "900-SPEC-DEMO"},
    )
    content = _assert_success(body, "c5_get_document")
    assert content["slug"] == "900-SPEC-DEMO"
    assert content["body"] is not None


@pytest.mark.asyncio
async def test_tool_c5_list_relations_empty_is_success(
    gateway_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Seeded entity has no relations — the tool SHALL return an empty list,
    not an error."""
    body = await _call(
        gateway_client,
        auth_headers,
        "c5_list_relations",
        {"project_id": "demo", "source_id": "R-900-001"},
    )
    content = _assert_success(body, "c5_list_relations")
    assert content["relations"] == []


# ---------------------------------------------------------------------------
# C6 tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_c6_list_plugins_happy_path(
    gateway_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    body = await _call(gateway_client, auth_headers, "c6_list_plugins", {})
    content = _assert_success(body, "c6_list_plugins")
    names = {p["name"] for p in content["plugins"]}
    assert "builtin-code" in names
    assert "code" in content["domains"]


@pytest.mark.asyncio
async def test_tool_c6_trigger_and_findings_roundtrip(
    gateway_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    """c6_trigger_validation + c6_list_findings used together: the trigger
    returns a run_id, the caller polls until completion, then lists the
    findings. Exercises both tools in a realistic sequence.
    """
    trigger_body = await _call(
        gateway_client,
        auth_headers,
        "c6_trigger_validation",
        {
            "domain": "code",
            "project_id": "demo",
            "check_ids": ["interface-signature-drift"],
        },
    )
    trigger_content = _assert_success(trigger_body, "c6_trigger_validation")
    run_id = trigger_content["run_id"]
    assert trigger_content["status"] in {"pending", "running", "completed"}

    # Poll the underlying C6 run via the REST API until completion.
    completed = False
    for _ in range(60):
        detail = await gateway_client.get(
            f"/api/v1/validation/runs/{run_id}", headers=auth_headers
        )
        assert detail.status_code == 200
        if detail.json()["status"] == "completed":
            completed = True
            break
        await asyncio.sleep(0.5)
    assert completed, f"run {run_id} never completed"

    findings_body = await _call(
        gateway_client, auth_headers, "c6_list_findings", {"run_id": run_id}
    )
    content = _assert_success(findings_body, "c6_list_findings")
    check_ids = {f["check_id"] for f in content["items"]}
    assert "interface-signature-drift" in check_ids


@pytest.mark.asyncio
async def test_tool_c6_trigger_unknown_domain_surfaces_as_error(
    gateway_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    body = await _call(
        gateway_client,
        auth_headers,
        "c6_trigger_validation",
        {"domain": "presentation", "project_id": "demo"},
    )
    text = _assert_is_error(body, "c6_trigger_validation")
    assert "404" in text or "domain" in text.lower()


@pytest.mark.asyncio
async def test_tool_c6_list_findings_bad_run_id_surfaces_as_error(
    gateway_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    body = await _call(
        gateway_client,
        auth_headers,
        "c6_list_findings",
        {"run_id": "nonexistent-run-42"},
    )
    text = _assert_is_error(body, "c6_list_findings")
    assert "404" in text or "not found" in text.lower()


# ---------------------------------------------------------------------------
# Cross-cutting: unknown tool surfaces as JSON-RPC-level error (not isError)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_unknown_is_transport_error(
    gateway_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    body = await _call(gateway_client, auth_headers, "ghost_tool", {})
    assert body.get("error") is not None, (
        "unknown tool SHALL surface as a transport-level JSON-RPC error, "
        "not an isError result envelope"
    )
    # ERROR_TOOL_NOT_FOUND is the C9 application code
    assert body["error"]["code"] == -32001


@pytest.mark.asyncio
async def test_tool_missing_required_arg_surfaces_as_error(
    gateway_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Required-field validation is a domain-side concern: the tool raises
    ToolDispatchError, which C9 translates to isError=true."""
    body = await _call(gateway_client, auth_headers, "c5_get_entity", {})
    text = _assert_is_error(body, "c5_get_entity")
    assert "project_id" in text
