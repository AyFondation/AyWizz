# =============================================================================
# File: conftest.py
# Version: 3
# Path: ay_platform_core/tests/integration/c4_orchestrator/conftest.py
# Description: Fixtures for C4 integration tests. Uses REAL ArangoDB and
#              REAL C8 client, but the LiteLLM proxy is impersonated by a
#              FastAPI ASGI mock that returns scripted completions — this
#              gives us reproducibility without a real LLM provider key.
#              Per session directive: integration tests use real
#              components wherever possible.
#
#              v2: adds optional MinIO + ArtifactsService wiring (opt-in
#              via the `c4_app_with_artifacts` fixture) so the generate
#              materialisation path (R-200-150..152) is exercised
#              end-to-end. Legacy tests using `c4_app` are unchanged —
#              artifacts_service defaults to None and the pipeline runs
#              unaffected.
# =============================================================================

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI, Header, HTTPException, Request

from ay_platform_core.c4_orchestrator.artifacts_router import (
    router as artifacts_router,
)
from ay_platform_core.c4_orchestrator.artifacts_service import ArtifactsService
from ay_platform_core.c4_orchestrator.artifacts_storage import ArtifactStorage
from ay_platform_core.c4_orchestrator.config import OrchestratorConfig
from ay_platform_core.c4_orchestrator.db.repository import OrchestratorRepository
from ay_platform_core.c4_orchestrator.dispatcher.in_process import InProcessDispatcher
from ay_platform_core.c4_orchestrator.documents_router import (
    router as documents_router,
)
from ay_platform_core.c4_orchestrator.domains.code.plugin import CodeDomainPlugin
from ay_platform_core.c4_orchestrator.events.null_publisher import NullPublisher
from ay_platform_core.c4_orchestrator.router import router
from ay_platform_core.c4_orchestrator.service import OrchestratorService
from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.config import ClientSettings
from tests.fixtures.containers import (
    ArangoEndpoint,
    MinioEndpoint,
    cleanup_arango_database,
)
from tests.integration.c2_auth.test_gitea_provisioning import _FakeGiteaClient

# ---------------------------------------------------------------------------
# Scripted LiteLLM mock
# ---------------------------------------------------------------------------


class ScriptedLLM:
    """FIFO queue of canned agent responses.

    The test sets `enqueue(payload)` for each expected agent invocation.
    The mock ASGI app dequeues one entry per chat/completions call and
    returns it as the assistant content. Exhausting the queue yields a
    generic error so test authors see why a call wasn't scripted.
    """

    def __init__(self) -> None:
        self._responses: list[dict[str, Any]] = []
        self.calls_seen: list[dict[str, Any]] = []

    def enqueue(self, payload: dict[str, Any]) -> None:
        self._responses.append(payload)

    def next_response(self) -> dict[str, Any]:
        if not self._responses:
            return {
                "status": "BLOCKED",
                "output": {},
                "blocker": {"reason": "no scripted response left in ScriptedLLM queue"},
            }
        return self._responses.pop(0)


@pytest.fixture(scope="function")
def scripted_llm() -> ScriptedLLM:
    return ScriptedLLM()


@pytest.fixture(scope="function")
def mock_llm_app(scripted_llm: ScriptedLLM) -> FastAPI:
    """Minimal ASGI app shaped like the C8 proxy. Accepts any path,
    returns the next scripted envelope as the assistant content.
    The envelope content shape is controlled by `scripted_llm.style`
    (default "clean" = `json.dumps(envelope)` ; "fenced" = wrapped in
    a ```json fence ; "prose" = JSON between a prefix and a suffix).
    Used by the integration test that validates the dispatcher's
    tolerant parser against realistic noisy small-model outputs."""
    app = FastAPI()

    @app.post("/v1/chat/completions", response_model=None)
    async def completions(
        request: Request,
        x_agent_name: str | None = Header(default=None),
        x_session_id: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="bearer required")
        if not x_agent_name or not x_session_id:
            raise HTTPException(status_code=400, detail="missing tags")
        body = await request.json()
        scripted_llm.calls_seen.append(body)
        envelope = scripted_llm.next_response()
        content = _render_envelope_with_style(
            envelope, getattr(scripted_llm, "style", "clean"),
        )
        return {
            "id": f"mock-{len(scripted_llm.calls_seen)}",
            "object": "chat.completion",
            "created": 1_700_000_000,
            "model": body.get("model") or "mock",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": content,
                    },
                    "finish_reason": "stop",
                },
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }

    return app


def _render_envelope_with_style(envelope: dict[str, Any], style: str) -> str:
    """Format the envelope as a JSON string, optionally wrapped to
    mimic small-model output patterns. Used by the integration test
    that exercises the dispatcher's tolerant parser (R-200-021 v3)."""
    raw = json.dumps(envelope)
    if style == "fenced":
        return f"Here is the JSON envelope:\n\n```json\n{raw}\n```"
    if style == "prose":
        return f"Sure! {raw} Hope this helps."
    return raw


# ---------------------------------------------------------------------------
# Real storage + orchestrator wiring
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def c4_repo(arango_container: ArangoEndpoint) -> Iterator[OrchestratorRepository]:
    db_name = f"c4_test_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    try:
        db = client.db(db_name, username="root", password=arango_container.password)
        repo = OrchestratorRepository(db)
        repo._ensure_collections_sync()
        yield repo
    finally:
        cleanup_arango_database(arango_container, db_name)


@pytest_asyncio.fixture(scope="function")
async def c4_llm_client(mock_llm_app: FastAPI) -> AsyncIterator[LLMGatewayClient]:
    transport = httpx.ASGITransport(app=mock_llm_app)
    http_client = httpx.AsyncClient(transport=transport, base_url="http://mock/v1")
    client = LLMGatewayClient(
        ClientSettings(gateway_url="http://mock/v1"),
        bearer_token="test-token",
        http_client=http_client,
    )
    try:
        yield client
    finally:
        await http_client.aclose()


@pytest.fixture(scope="function")
def c4_publisher() -> NullPublisher:
    return NullPublisher()


@pytest_asyncio.fixture(scope="function")
async def c4_service(
    c4_repo: OrchestratorRepository,
    c4_llm_client: LLMGatewayClient,
    c4_publisher: NullPublisher,
) -> OrchestratorService:
    return OrchestratorService(
        config=OrchestratorConfig(),
        repo=c4_repo,
        dispatcher=InProcessDispatcher(c4_llm_client),
        domain_plugin=CodeDomainPlugin(),
        publisher=c4_publisher,
    )


@pytest.fixture(scope="function")
def c4_app(c4_service: OrchestratorService) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.orchestrator_service = c4_service
    return app


# ---------------------------------------------------------------------------
# Opt-in fixture : full app with ArtifactsService (MinIO) + FakeGitea
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def c4_app_with_artifacts(
    c4_repo: OrchestratorRepository,
    c4_llm_client: LLMGatewayClient,
    c4_publisher: NullPublisher,
    minio_container: MinioEndpoint,
) -> AsyncIterator[tuple[FastAPI, ArtifactsService, _FakeGiteaClient]]:
    """C4 app wired with a real MinIO artifacts surface + a FakeGitea
    stub for the push side-effects. Used by R-200-150..152 tests."""
    from minio import Minio  # noqa: PLC0415 — heavy import scoped to fixture
    bucket = f"artbucket-{uuid.uuid4().hex[:8]}"
    minio_client = Minio(
        minio_container.endpoint,
        access_key=minio_container.access_key,
        secret_key=minio_container.secret_key,
        secure=False,
    )
    storage = ArtifactStorage(minio_client, bucket)
    await storage.ensure_bucket()
    fake_gitea = _FakeGiteaClient()
    artifacts_service = ArtifactsService(
        repo=c4_repo, storage=storage, gitea=fake_gitea,  # type: ignore[arg-type]
    )
    service = OrchestratorService(
        config=OrchestratorConfig(),
        repo=c4_repo,
        dispatcher=InProcessDispatcher(c4_llm_client),
        domain_plugin=CodeDomainPlugin(),
        publisher=c4_publisher,
        artifacts_service=artifacts_service,
    )
    app = FastAPI()
    app.include_router(router)
    app.include_router(artifacts_router)
    app.state.orchestrator_service = service
    app.state.artifacts_service = artifacts_service
    yield app, artifacts_service, fake_gitea


# ---------------------------------------------------------------------------
# Chat-direct DocGen documents app (D-015) — shared across the documents
# test modules. A fixture MUST live in conftest to be visible across test
# modules; test_documents_structural_ops.py reuses this one.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def documents_app(
    arango_container: ArangoEndpoint,
    minio_container: MinioEndpoint,
) -> AsyncIterator[tuple[FastAPI, _FakeGiteaClient]]:
    """Documents router wired with real Arango + real MinIO + stubbed
    Gitea. Used by test_documents_api.py and
    test_documents_structural_ops.py."""
    from minio import Minio  # noqa: PLC0415 — heavy import scoped to fixture

    db_name = f"c4_doc_{uuid.uuid4().hex[:8]}"
    bucket = f"docbucket-{uuid.uuid4().hex[:8]}"
    sys_db = ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password,
    )
    sys_db.create_database(db_name)
    db = ArangoClient(hosts=arango_container.url).db(
        db_name, username="root", password=arango_container.password,
    )
    repo = OrchestratorRepository(db)
    repo._ensure_collections_sync()

    minio_client = Minio(
        minio_container.endpoint,
        access_key=minio_container.access_key,
        secret_key=minio_container.secret_key,
        secure=False,
    )
    storage = ArtifactStorage(minio_client, bucket)
    await storage.ensure_bucket()
    fake_gitea = _FakeGiteaClient()
    service = ArtifactsService(
        repo=repo, storage=storage, gitea=fake_gitea,  # type: ignore[arg-type]
    )

    app = FastAPI()
    app.include_router(documents_router)
    app.state.artifacts_service = service
    try:
        yield app, fake_gitea
    finally:
        cleanup_arango_database(arango_container, db_name)
