# =============================================================================
# File: test_c7_livedocs_client.py
# Version: 2
# Path: ay_platform_core/tests/unit/c4_orchestrator/test_c7_livedocs_client.py
# Description: Unit tests for the C4 -> C7 live-docs index client (D-021 /
#              R-400-232): index/remove hit the right C7 endpoints with the
#              system forward-auth headers + payload, are best-effort (a
#              transport error or 4xx never raises — a doc save must not fail on
#              a RAG-index hiccup), and are a no-op when unconfigured. v2 adds
#              the `kg_indexed` READ (R-200-173): bool on 200, None on
#              error/4xx/disabled so the meta endpoint stays null-tolerant.
#
# @relation validates:R-400-232
# @relation validates:R-200-173
# =============================================================================

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from ay_platform_core.c4_orchestrator.c7_livedocs_client import C7LiveDocsClient

pytestmark = pytest.mark.unit


def _mock(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_index_posts_expected_url_headers_payload() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["roles"] = request.headers.get("X-User-Roles")
        seen["tenant"] = request.headers.get("X-Tenant-Id")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"path": "a.md", "doc_type": "prose",
                                         "chunk_count": 1, "model_id": "m",
                                         "reduced_fidelity": False})

    c = C7LiveDocsClient(base_url="http://c7:8000", client=_mock(handler))
    await c.index(tenant_id="t1", project_id="p1", path="docs/a.md", content="hi")
    assert seen["method"] == "PUT"
    assert seen["url"] == "http://c7:8000/api/v1/memory/projects/p1/live-docs/index"
    # System identity asserts a CONTENT role, never platform_manager.
    assert seen["roles"] == "project_editor"
    assert seen["tenant"] == "t1"
    assert seen["body"] == {"path": "docs/a.md", "content": "hi", "uploaded_by": "system"}
    await c.aclose()


async def test_remove_deletes_catch_all_path() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        return httpx.Response(204)

    c = C7LiveDocsClient(base_url="http://c7:8000", client=_mock(handler))
    await c.remove(tenant_id="t1", project_id="p1", path="docs/a.md")
    assert seen["method"] == "DELETE"
    assert seen["url"] == (
        "http://c7:8000/api/v1/memory/projects/p1/live-docs/index/docs/a.md"
    )


async def test_best_effort_swallows_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("c7 down")

    c = C7LiveDocsClient(base_url="http://c7:8000", client=_mock(handler))
    # Must NOT raise — a doc save cannot fail because the index is unreachable.
    await c.index(tenant_id="t1", project_id="p1", path="a.md", content="x")
    await c.remove(tenant_id="t1", project_id="p1", path="a.md")


async def test_best_effort_swallows_4xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "bad"})

    c = C7LiveDocsClient(base_url="http://c7:8000", client=_mock(handler))
    await c.index(tenant_id="t1", project_id="p1", path="a.md", content="x")


async def test_kg_indexed_reads_bool_on_200() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["roles"] = request.headers.get("X-User-Roles")
        return httpx.Response(200, json={"path": "src/a.py", "kg_indexed": True})

    c = C7LiveDocsClient(base_url="http://c7:8000", client=_mock(handler))
    got = await c.kg_indexed(tenant_id="t1", project_id="p1", path="src/a.py")
    assert got is True
    assert seen["method"] == "GET"
    assert seen["url"] == (
        "http://c7:8000/api/v1/memory/projects/p1/live-docs/kg-indexed"
        "?path=src%2Fa.py"
    )
    assert seen["roles"] == "project_editor"
    await c.aclose()


async def test_kg_indexed_none_on_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("c7 down")

    c = C7LiveDocsClient(base_url="http://c7:8000", client=_mock(handler))
    # Unreachable → None (meta endpoint leaves kg_indexed null, never fails).
    assert await c.kg_indexed(tenant_id="t1", project_id="p1", path="a.py") is None


async def test_kg_indexed_none_on_4xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "no"})

    c = C7LiveDocsClient(base_url="http://c7:8000", client=_mock(handler))
    assert await c.kg_indexed(tenant_id="t1", project_id="p1", path="a.py") is None


async def test_kg_indexed_none_when_unconfigured() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        return httpx.Response(200, json={"kg_indexed": True})

    c = C7LiveDocsClient(base_url="", client=_mock(handler))
    assert await c.kg_indexed(tenant_id="t1", project_id="p1", path="a.py") is None


async def test_noop_when_unconfigured() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        nonlocal called
        called = True
        return httpx.Response(200)

    # Empty base_url → disabled; the injected client is never touched.
    c = C7LiveDocsClient(base_url="", client=_mock(handler))
    await c.index(tenant_id="t1", project_id="p1", path="a.md", content="x")
    await c.remove(tenant_id="t1", project_id="p1", path="a.md")
    assert called is False
