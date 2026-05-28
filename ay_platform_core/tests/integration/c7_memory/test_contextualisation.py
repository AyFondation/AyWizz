# =============================================================================
# File: test_contextualisation.py
# Version: 1
# Path: ay_platform_core/tests/integration/c7_memory/test_contextualisation.py
# Description: V2 #3-A.c integration tests — cumulative chunk contextualisation
#              at ingestion (R-400-203). Real ArangoDB + a scripted C8 LLM
#              (httpx ASGI) returning a fixed context :
#                - with contextualisation ON + an LLM, each persisted chunk
#                  carries the generated `context` (and `content` stays raw) ;
#                - with it OFF, or without an LLM, `context` is "".
#
# @relation validates:R-400-203
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI, Header, HTTPException, Request

from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.db.repository import MemoryRepository
from ay_platform_core.c7_memory.embedding.deterministic import (
    DeterministicHashEmbedder,
)
from ay_platform_core.c7_memory.models import IndexKind, SourceIngestRequest
from ay_platform_core.c7_memory.service import MemoryService
from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.config import ClientSettings
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]

_CONTEXT_REPLY = "This chunk situates the gateway within the platform."
_SOURCE_TEXT = (
    "The platform routes every model call through the C8 gateway. "
    "The gateway enforces budgets and per-agent routing. "
    "Downstream components never call providers directly."
)


def _mock_llm_app() -> FastAPI:
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def completions(
        request: Request,
        x_agent_name: str | None = Header(default=None),
        x_session_id: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> Any:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="bearer required")
        await request.json()
        return {
            "id": "ctx-1",
            "object": "chat.completion",
            "created": 1_700_000_000,
            "model": "mock",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": _CONTEXT_REPLY},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }

    return app


async def _make_db(arango_container: ArangoEndpoint) -> tuple[Any, str]:
    db_name = f"c7_ctx_{uuid.uuid4().hex[:8]}"
    ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password,
    ).create_database(db_name)
    db = ArangoClient(hosts=arango_container.url).db(
        db_name, username="root", password=arango_container.password,
    )
    return db, db_name


@pytest_asyncio.fixture(scope="function")
async def ctx_stack(
    arango_container: ArangoEndpoint,
) -> AsyncIterator[dict[str, Any]]:
    db, db_name = await _make_db(arango_container)
    repo = MemoryRepository(db)
    repo._ensure_collections_sync()
    embedder = DeterministicHashEmbedder(dimension=64)
    llm_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_mock_llm_app()), base_url="http://mock/v1",
    )
    llm_client = LLMGatewayClient(
        ClientSettings(gateway_url="http://mock/v1"),
        bearer_token="ctx-test-token",
        http_client=llm_http,
    )
    service = MemoryService(
        config=MemoryConfig(
            embedding_adapter="deterministic-hash",
            embedding_dimension=embedder.dimension,
            chunk_token_size=16,
            chunk_overlap=2,
            default_quota_bytes=1024 * 1024 * 1024,
            retrieval_scan_cap=1000,
        ),
        repo=repo,
        embedder=embedder,
        llm_client=llm_client,
    )
    try:
        yield {"repo": repo, "service": service, "embedder": embedder}
    finally:
        await llm_http.aclose()
        cleanup_arango_database(arango_container, db_name)


async def _ingest(service: MemoryService, *, source_id: str) -> None:
    await service.ingest_source(
        SourceIngestRequest(
            source_id=source_id,
            project_id="project-ctx",
            mime_type="text/plain",
            content=_SOURCE_TEXT,
            size_bytes=len(_SOURCE_TEXT.encode("utf-8")),
            uploaded_by="alice",
        ),
        tenant_id="tenant-ctx",
    )


async def _chunks(repo: MemoryRepository, embedder: DeterministicHashEmbedder,
                  source_id: str) -> list[dict[str, Any]]:
    rows = await repo.scan_chunks(
        tenant_id="tenant-ctx",
        project_id="project-ctx",
        indexes=[IndexKind.EXTERNAL_SOURCES.value],
        model_id=embedder.model_id,
        include_deprecated=False,
        include_history=False,
        scan_cap=1000,
    )
    return [r for r in rows if r.get("source_id") == source_id]


async def test_contextualisation_stores_context_on_each_chunk(
    ctx_stack: dict[str, Any],
) -> None:
    service: MemoryService = ctx_stack["service"]
    repo: MemoryRepository = ctx_stack["repo"]
    embedder: DeterministicHashEmbedder = ctx_stack["embedder"]

    await _ingest(service, source_id="src-ctx")
    rows = await _chunks(repo, embedder, "src-ctx")

    assert len(rows) >= 2  # the source split into multiple chunks
    assert all(r["context"] == _CONTEXT_REPLY for r in rows)
    # `content` stays the RAW chunk (not the contextualised text) — context
    # is a separate field ; the embedding is the only thing contextualised.
    assert all(_CONTEXT_REPLY not in r["content"] for r in rows)


async def test_contextualisation_disabled_yields_empty_context(
    arango_container: ArangoEndpoint,
) -> None:
    db, db_name = await _make_db(arango_container)
    try:
        repo = MemoryRepository(db)
        repo._ensure_collections_sync()
        embedder = DeterministicHashEmbedder(dimension=64)
        llm_http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_mock_llm_app()),
            base_url="http://mock/v1",
        )
        llm_client = LLMGatewayClient(
            ClientSettings(gateway_url="http://mock/v1"),
            bearer_token="t",
            http_client=llm_http,
        )
        service = MemoryService(
            config=MemoryConfig(
                embedding_adapter="deterministic-hash",
                embedding_dimension=embedder.dimension,
                chunk_token_size=16,
                chunk_overlap=2,
                default_quota_bytes=1024 * 1024 * 1024,
                contextualisation_enabled=False,
            ),
            repo=repo,
            embedder=embedder,
            llm_client=llm_client,
        )
        await _ingest(service, source_id="src-off")
        rows = await _chunks(repo, embedder, "src-off")
        assert rows
        assert all(r["context"] == "" for r in rows)
        await llm_http.aclose()
    finally:
        cleanup_arango_database(arango_container, db_name)


async def test_no_llm_yields_empty_context(
    arango_container: ArangoEndpoint,
) -> None:
    db, db_name = await _make_db(arango_container)
    try:
        repo = MemoryRepository(db)
        repo._ensure_collections_sync()
        embedder = DeterministicHashEmbedder(dimension=64)
        service = MemoryService(
            config=MemoryConfig(
                embedding_adapter="deterministic-hash",
                embedding_dimension=embedder.dimension,
                chunk_token_size=16,
                chunk_overlap=2,
                default_quota_bytes=1024 * 1024 * 1024,
            ),
            repo=repo,
            embedder=embedder,
            # No llm_client — contextualisation no-ops gracefully.
        )
        await _ingest(service, source_id="src-nollm")
        rows = await _chunks(repo, embedder, "src-nollm")
        assert rows
        assert all(r["context"] == "" for r in rows)
    finally:
        cleanup_arango_database(arango_container, db_name)
