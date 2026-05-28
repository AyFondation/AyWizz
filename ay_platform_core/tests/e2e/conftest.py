# =============================================================================
# File: conftest.py
# Version: 1
# Path: ay_platform_core/tests/e2e/conftest.py
# Description: Cross-component end-to-end fixtures. Assembles a `platform_stack`
#              that wires real instances of C2, C3, C4, C5 against:
#                - ONE shared ArangoDB instance (logical DB per component)
#                - ONE shared MinIO instance (distinct buckets per component)
#                - Scripted LiteLLM mock (C8 impersonated)
#
#              Traefik (C1) is NOT part of the stack — its routing is
#              validated by the C1 contract tests. E2e tests call each
#              service's FastAPI app directly with headers simulating what
#              forward-auth would propagate downstream.
#
#              Per session directive: **integration/e2e tests SHALL exercise
#              real components end-to-end wherever the full chain is
#              available**. The LLM mock is the only stub (no paid provider
#              key in CI).
# =============================================================================

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI, Header, HTTPException, Request
from minio import Minio

# ---------------------------------------------------------------------------
# C2 Auth wiring
# ---------------------------------------------------------------------------
from ay_platform_core.c2_auth.config import AuthConfig
from ay_platform_core.c2_auth.db.repository import AuthRepository
from ay_platform_core.c2_auth.router import router as c2_router
from ay_platform_core.c2_auth.service import AuthService
from ay_platform_core.c2_auth.service import get_service as c2_get_service

# ---------------------------------------------------------------------------
# C3 Conversation wiring
# ---------------------------------------------------------------------------
from ay_platform_core.c3_conversation.db.repository import ConversationRepository
from ay_platform_core.c3_conversation.router import router as c3_router
from ay_platform_core.c3_conversation.service import ConversationService

# ---------------------------------------------------------------------------
# C4 Orchestrator wiring
# ---------------------------------------------------------------------------
from ay_platform_core.c4_orchestrator.config import OrchestratorConfig
from ay_platform_core.c4_orchestrator.db.repository import OrchestratorRepository
from ay_platform_core.c4_orchestrator.dispatcher.in_process import InProcessDispatcher
from ay_platform_core.c4_orchestrator.domains.code.plugin import CodeDomainPlugin
from ay_platform_core.c4_orchestrator.events.null_publisher import (
    NullPublisher as C4NullPublisher,
)
from ay_platform_core.c4_orchestrator.router import router as c4_router
from ay_platform_core.c4_orchestrator.service import OrchestratorService

# ---------------------------------------------------------------------------
# C5 Requirements wiring
# ---------------------------------------------------------------------------
from ay_platform_core.c5_requirements.db.repository import RequirementsRepository
from ay_platform_core.c5_requirements.events.null_publisher import (
    NullPublisher as C5NullPublisher,
)
from ay_platform_core.c5_requirements.router import router as c5_router
from ay_platform_core.c5_requirements.service import RequirementsService
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage

# ---------------------------------------------------------------------------
# C8 LLM client wiring
# ---------------------------------------------------------------------------
from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.config import ClientSettings
from tests.fixtures.containers import (
    ArangoEndpoint,
    MinioEndpoint,
    cleanup_arango_database,
    cleanup_minio_bucket,
)

# ===========================================================================
# Scripted LLM
# ===========================================================================


class ScriptedLLM:
    """Same contract as the C4 integration fixture — FIFO queue of
    canned agent completions. E2e tests use this to drive a full run
    through the pipeline without a real provider."""

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


def _build_mock_llm_app(script: ScriptedLLM) -> FastAPI:
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
        script.calls_seen.append(body)
        envelope = script.next_response()
        return {
            "id": f"mock-{len(script.calls_seen)}",
            "object": "chat.completion",
            "created": 1_700_000_000,
            "model": "mock",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(envelope),
                    },
                    "finish_reason": "stop",
                },
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    return app


# ===========================================================================
# Platform stack — the bundle of services + shared dependencies
# ===========================================================================


@dataclass
class PlatformStack:
    """Holds the wired services + clients used by e2e tests.

    Each `*_app` is a FastAPI application ready to be driven with
    httpx.AsyncClient(ASGITransport=...). Components share the same
    ArangoDB instance (distinct logical DBs) and the same MinIO
    instance (distinct buckets).
    """

    c2_app: FastAPI
    c3_app: FastAPI
    c4_app: FastAPI
    c5_app: FastAPI
    scripted_llm: ScriptedLLM
    # Direct service refs for tests that want to assert on internal state.
    c2_service: AuthService
    c3_service: ConversationService
    c4_service: OrchestratorService
    c5_service: RequirementsService
    cleanup: list[Any] = field(default_factory=list)


def _build_c2(
    arango_url: str, arango_password: str, db_name: str
) -> tuple[FastAPI, AuthService]:
    repo = AuthRepository.from_config(arango_url, db_name, "root", arango_password)
    repo._ensure_collections_sync()
    config = AuthConfig.model_validate({
        "auth_mode": "none",
        "jwt_secret_key": "e2e-test-secret-key-32-chars-min!",
        "platform_environment": "testing",
    })
    service = AuthService(config, repo)
    app = FastAPI()
    app.include_router(c2_router, prefix="/auth")
    app.dependency_overrides[c2_get_service] = lambda: service
    return app, service


def _build_c3(
    client: ArangoClient, db_name: str, password: str
) -> tuple[FastAPI, ConversationService]:
    db = client.db(db_name, username="root", password=password)
    repo = ConversationRepository(db)
    repo._ensure_collections_sync()
    service = ConversationService(repo)
    app = FastAPI()
    app.include_router(c3_router)
    app.state.conversation_service = service
    return app, service


def _build_c5(
    client: ArangoClient,
    db_name: str,
    password: str,
    minio_endpoint: str,
    minio_access: str,
    minio_secret: str,
    bucket: str,
) -> tuple[FastAPI, RequirementsService, Minio]:
    db = client.db(db_name, username="root", password=password)
    repo = RequirementsRepository(db)
    repo._ensure_collections_sync()
    minio_client = Minio(
        minio_endpoint,
        access_key=minio_access,
        secret_key=minio_secret,
        secure=False,
    )
    storage = RequirementsStorage(minio_client, bucket)
    storage._ensure_bucket_sync()
    service = RequirementsService(repo, storage, C5NullPublisher())
    app = FastAPI()
    app.include_router(c5_router)
    app.state.requirements_service = service
    return app, service, minio_client


def _build_c4(
    client: ArangoClient,
    db_name: str,
    password: str,
    llm_client: LLMGatewayClient,
) -> tuple[FastAPI, OrchestratorService]:
    db = client.db(db_name, username="root", password=password)
    repo = OrchestratorRepository(db)
    repo._ensure_collections_sync()
    service = OrchestratorService(
        config=OrchestratorConfig(),
        repo=repo,
        dispatcher=InProcessDispatcher(llm_client),
        domain_plugin=CodeDomainPlugin(),
        publisher=C4NullPublisher(),
    )
    app = FastAPI()
    app.include_router(c4_router)
    app.state.orchestrator_service = service
    return app, service


@pytest_asyncio.fixture(scope="function")
async def platform_stack(
    arango_container: ArangoEndpoint,
    minio_container: MinioEndpoint,
) -> AsyncIterator[PlatformStack]:
    """End-to-end platform fixture.

    Creates fresh logical databases per component on the shared Arango
    instance plus isolated MinIO buckets. Tears everything down at the
    end of the test.
    """
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)

    db_names = {
        "c2": f"e2e_c2_{uuid.uuid4().hex[:6]}",
        "c3": f"e2e_c3_{uuid.uuid4().hex[:6]}",
        "c4": f"e2e_c4_{uuid.uuid4().hex[:6]}",
        "c5": f"e2e_c5_{uuid.uuid4().hex[:6]}",
    }
    for db_name in db_names.values():
        sys_db.create_database(db_name)

    cleanup: list[Any] = []
    try:
        c2_app, c2_service = _build_c2(
            arango_container.url, arango_container.password, db_names["c2"]
        )
        c3_app, c3_service = _build_c3(
            client, db_names["c3"], arango_container.password
        )
        c5_bucket = f"e2e-c5-{uuid.uuid4().hex[:6]}"
        c5_app, c5_service, c5_minio_client = _build_c5(
            client, db_names["c5"], arango_container.password,
            minio_container.endpoint, minio_container.access_key,
            minio_container.secret_key, c5_bucket,
        )
        cleanup.append(("minio_bucket", c5_minio_client, c5_bucket))

        scripted_llm = ScriptedLLM()
        mock_llm_app = _build_mock_llm_app(scripted_llm)
        llm_transport = httpx.ASGITransport(app=mock_llm_app)
        llm_http = httpx.AsyncClient(transport=llm_transport, base_url="http://mock/v1")
        cleanup.append(("http_client", llm_http))
        llm_client = LLMGatewayClient(
            ClientSettings(gateway_url="http://mock/v1"),
            bearer_token="e2e-test-token",
            http_client=llm_http,
        )
        c4_app, c4_service = _build_c4(
            client, db_names["c4"], arango_container.password, llm_client
        )

        stack = PlatformStack(
            c2_app=c2_app,
            c3_app=c3_app,
            c4_app=c4_app,
            c5_app=c5_app,
            scripted_llm=scripted_llm,
            c2_service=c2_service,
            c3_service=c3_service,
            c4_service=c4_service,
            c5_service=c5_service,
            cleanup=cleanup,
        )
        yield stack
    finally:
        # Close HTTP clients first (async), then drop Arango DBs and
        # MinIO buckets (sync).
        for kind, *args in cleanup:
            if kind == "http_client":
                await args[0].aclose()
        for kind, *args in cleanup:
            if kind == "minio_bucket":
                _, bucket = args
                cleanup_minio_bucket(minio_container, bucket)
        for db_name in db_names.values():
            cleanup_arango_database(arango_container, db_name)


# ===========================================================================
# Helpers
# ===========================================================================


def asgi_client(app: FastAPI) -> httpx.AsyncClient:
    """Shortcut for test code: wraps a FastAPI app in an httpx AsyncClient."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://e2e"
    )


@pytest.fixture(scope="function")
def asgi_client_factory() -> Any:
    return asgi_client
