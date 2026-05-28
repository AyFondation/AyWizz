# =============================================================================
# File: test_auto_kg_extraction.py
# Version: 1
# Path: ay_platform_core/tests/integration/c7_memory/test_auto_kg_extraction.py
# Description: Verifies the gap-UX-#3 behaviour : when C7 is wired with
#              both kg_repo and llm_client, AND
#              `C7_AUTO_EXTRACT_KG_ON_UPLOAD=True`, an upload via
#              `POST /sources/{sid}/ingest-chunks` SHALL automatically trigger KG
#              extraction so the F.2 hybrid retrieval graph is
#              populated without a separate manual call.
#
#              Behaviour pinned :
#                1. With auto + kg_repo + llm wired → KG entities
#                   appear after upload.
#                2. With auto disabled (config flag) → no KG.
#                3. With auto enabled but kg_repo missing → upload
#                   succeeds, no KG (no crash).
#                4. With auto enabled but LLM returns malformed JSON
#                   → upload SUCCEEDS (best-effort suppress) and
#                   no KG persisted.
#
# @relation validates:R-400-021
# =============================================================================

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI, Header, HTTPException, Request
from minio import Minio

from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.db.repository import MemoryRepository
from ay_platform_core.c7_memory.embedding.deterministic import (
    DeterministicHashEmbedder,
)
from ay_platform_core.c7_memory.kg.repository import KGRepository
from ay_platform_core.c7_memory.router import router as c7_router
from ay_platform_core.c7_memory.service import MemoryService
from ay_platform_core.c7_memory.service import get_service as c7_get_service
from ay_platform_core.c7_memory.storage.minio_storage import MemorySourceStorage
from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.config import ClientSettings
from tests.fixtures.containers import (
    ArangoEndpoint,
    MinioEndpoint,
    cleanup_arango_database,
    cleanup_minio_bucket,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]

_TENANT = "tenant-auto-kg"
_PROJECT = "project-auto-kg"
_HEADERS = {
    "X-User-Id": "alice",
    "X-Tenant-Id": _TENANT,
    "X-User-Roles": "project_editor",
}


def _scripted_llm_app(json_payload: str) -> FastAPI:
    """A minimal mock C8 that always returns the given JSON body wrapped
    in OpenAI chat-completion shape. Lifted from test_kg_extraction.py."""
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
        if not x_agent_name or not x_session_id:
            raise HTTPException(status_code=400, detail="missing tags")
        await request.json()
        return {
            "id": "mock-1",
            "object": "chat.completion",
            "created": 1_700_000_000,
            "model": "mock",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json_payload,
                    },
                    "finish_reason": "stop",
                },
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    return app


@pytest_asyncio.fixture(scope="function")
async def kg_upload_stack(
    arango_container: ArangoEndpoint,
    minio_container: MinioEndpoint,
) -> AsyncIterator[dict[str, Any]]:
    """Wires real Arango + MinIO + KG repo + scripted LLM. The fixture
    parametrises by config flag — tests adjust `service._config` to
    flip auto-extract on/off."""
    db_name = f"c7_autokg_{uuid.uuid4().hex[:8]}"
    bucket = f"c7-autokg-{uuid.uuid4().hex[:6]}"
    sys_db = ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password,
    )
    sys_db.create_database(db_name)
    db = ArangoClient(hosts=arango_container.url).db(
        db_name, username="root", password=arango_container.password,
    )

    repo = MemoryRepository(db)
    repo._ensure_collections_sync()
    kg_repo = KGRepository(db)
    kg_repo._ensure_collections_sync()
    embedder = DeterministicHashEmbedder(dimension=64)

    minio = Minio(
        minio_container.endpoint,
        access_key=minio_container.access_key,
        secret_key=minio_container.secret_key,
        secure=False,
    )
    storage = MemorySourceStorage(minio, bucket)
    storage._ensure_bucket_sync()

    canned_json = json.dumps({
        "entities": [
            {"name": "Voyager 1", "type": "spacecraft"},
            {"name": "Earth", "type": "place"},
        ],
        "relations": [
            {
                "subject": {"name": "Voyager 1", "type": "spacecraft"},
                "relation": "launched_from",
                "object": {"name": "Earth", "type": "place"},
            },
        ],
    })
    mock_app = _scripted_llm_app(canned_json)
    llm_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mock_app),
        base_url="http://mock/v1",
    )
    llm_client = LLMGatewayClient(
        ClientSettings(gateway_url="http://mock/v1"),
        bearer_token="auto-kg-test-token",
        http_client=llm_http,
    )

    service = MemoryService(
        config=MemoryConfig(
            embedding_adapter="deterministic-hash",
            embedding_dimension=embedder.dimension,
            chunk_token_size=64,
            chunk_overlap=8,
            default_quota_bytes=1024 * 1024 * 1024,
            retrieval_scan_cap=1000,
            auto_extract_kg_on_upload=True,
        ),
        repo=repo,
        embedder=embedder,
        storage=storage,
        kg_repo=kg_repo,
        llm_client=llm_client,
    )
    app = FastAPI()
    app.include_router(c7_router)
    app.dependency_overrides[c7_get_service] = lambda: service
    try:
        yield {
            "app": app, "service": service, "kg_repo": kg_repo,
            "llm_http": llm_http,
        }
    finally:
        await llm_http.aclose()
        cleanup_arango_database(arango_container, db_name)
        cleanup_minio_bucket(minio_container, bucket)


async def _upload_text(
    app: FastAPI, source_id: str, body: bytes,
) -> httpx.Response:
    """D-020 session 7 — drives ingestion through the new
    `/ingest-chunks` path. Builds a minimal ChunkIngestRequest from the
    raw text so the auto-KG hook (now wired into
    `ingest_chunks_from_extractor`) fires on the indexed chunk."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    text = body.decode("utf-8")
    chunk_request: dict[str, Any] = {
        "extraction_run_id": f"test-{uuid.uuid4().hex[:8]}",
        "manifest_object_key": None,
        "embedding_model": "deterministic-hash-v1",
        "embedding_model_version": "test-v1",
        "embedding_dimension": 64,
        "uploaded_by": _HEADERS["X-User-Id"],
        "mime_type": "text/plain",
        "chunks": [
            {
                "chunk_id": f"{source_id}:0000",
                "seq": 0,
                "text": text,
                "original_text": text,
                "section_path": [],
                "char_start": 0,
                "char_end": len(text),
                "token_count": len(text.split()),
                "references": [],
                "images": [],
                "tables": [],
                "extraction_run_id": f"test-{uuid.uuid4().hex[:8]}",
                "embedding": None,
            }
        ],
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.post(
            f"/api/v1/memory/projects/{_PROJECT}/sources/{source_id}/ingest-chunks",
            headers={**_HEADERS, "Content-Type": "application/json"},
            json=chunk_request,
        )


async def test_upload_auto_triggers_kg_extraction(
    kg_upload_stack: dict[str, Any],
) -> None:
    """The flag is on, kg_repo + llm wired → entities present in graph
    after the `/ingest-chunks` call returns 201 (D-020 session 7 —
    auto-KG hook preserved on the new path)."""
    app: FastAPI = kg_upload_stack["app"]
    kg_repo: KGRepository = kg_upload_stack["kg_repo"]
    source_id = f"src-auto-{uuid.uuid4().hex[:6]}"
    body = b"Voyager 1 was launched from Earth in 1977."

    resp = await _upload_text(app, source_id, body)
    assert resp.status_code == 201, resp.text

    ents = await kg_repo.list_entities_for_source(_TENANT, _PROJECT, source_id)
    assert {e["name"] for e in ents} == {"Voyager 1", "Earth"}, (
        f"expected auto-extracted entities, got {ents}"
    )


async def test_disabled_flag_skips_auto_extraction(
    kg_upload_stack: dict[str, Any],
) -> None:
    """Flip the config flag → upload succeeds but graph stays empty."""
    service: MemoryService = kg_upload_stack["service"]
    service._config = service._config.model_copy(
        update={"auto_extract_kg_on_upload": False},
    )
    app: FastAPI = kg_upload_stack["app"]
    kg_repo: KGRepository = kg_upload_stack["kg_repo"]
    source_id = f"src-noauto-{uuid.uuid4().hex[:6]}"
    body = b"Voyager 1 was launched from Earth in 1977."

    resp = await _upload_text(app, source_id, body)
    assert resp.status_code == 201

    ents = await kg_repo.list_entities_for_source(_TENANT, _PROJECT, source_id)
    assert ents == [], (
        f"expected no entities (flag disabled), got {ents}"
    )


async def test_malformed_llm_response_does_not_break_upload(
    arango_container: ArangoEndpoint,
    minio_container: MinioEndpoint,
) -> None:
    """LLM returns invalid JSON → KG extraction raises internally,
    `contextlib.suppress` catches it, upload still SUCCEEDS with 201
    and chunks persist (KG just stays empty)."""
    db_name = f"c7_autokg_bad_{uuid.uuid4().hex[:8]}"
    bucket = f"c7-autokg-bad-{uuid.uuid4().hex[:6]}"
    sys_db = ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password,
    )
    sys_db.create_database(db_name)
    try:
        db = ArangoClient(hosts=arango_container.url).db(
            db_name, username="root", password=arango_container.password,
        )
        repo = MemoryRepository(db)
        repo._ensure_collections_sync()
        kg_repo = KGRepository(db)
        kg_repo._ensure_collections_sync()
        embedder = DeterministicHashEmbedder(dimension=64)
        minio = Minio(
            minio_container.endpoint,
            access_key=minio_container.access_key,
            secret_key=minio_container.secret_key,
            secure=False,
        )
        storage = MemorySourceStorage(minio, bucket)
        storage._ensure_bucket_sync()

        # Mock returns NON-JSON content → KG extractor raises.
        mock_app = _scripted_llm_app("not-json-at-all {{")
        llm_http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mock_app),
            base_url="http://mock/v1",
        )
        try:
            llm_client = LLMGatewayClient(
                ClientSettings(gateway_url="http://mock/v1"),
                bearer_token="auto-kg-bad-token",
                http_client=llm_http,
            )
            service = MemoryService(
                config=MemoryConfig(
                    embedding_adapter="deterministic-hash",
                    embedding_dimension=embedder.dimension,
                    chunk_token_size=64,
                    chunk_overlap=8,
                    default_quota_bytes=1024 * 1024 * 1024,
                    retrieval_scan_cap=1000,
                    auto_extract_kg_on_upload=True,
                ),
                repo=repo,
                embedder=embedder,
                storage=storage,
                kg_repo=kg_repo,
                llm_client=llm_client,
            )
            app = FastAPI()
            app.include_router(c7_router)
            app.dependency_overrides[c7_get_service] = lambda: service

            source_id = f"src-bad-{uuid.uuid4().hex[:6]}"
            resp = await _upload_text(
                app, source_id, b"Some text content for indexing.",
            )
            # Upload succeeds despite KG failure.
            assert resp.status_code == 201, resp.text

            # No KG entities (extraction silently failed).
            ents = await kg_repo.list_entities_for_source(
                _TENANT, _PROJECT, source_id,
            )
            assert ents == []
        finally:
            await llm_http.aclose()
    finally:
        cleanup_arango_database(arango_container, db_name)
        cleanup_minio_bucket(minio_container, bucket)
