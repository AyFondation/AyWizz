# =============================================================================
# File: _stack.py
# Version: 1
# Path: ay_platform_core/tests/e2e/auth_matrix/_stack.py
# Description: Composes a multi-component PlatformStack for the auth matrix.
#              Shares one ArangoDB instance (logical DB per component)
#              and one MinIO instance (bucket per component) across the
#              7 in-process FastAPI apps (C2, C3, C4, C5, C6, C7, C9).
#
#              Per session directive: e2e tests SHALL exercise real
#              components against real backends (testcontainers) — no
#              mocks except the LLM gateway (no paid provider key in CI).
# =============================================================================

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI, Header, HTTPException, Request
from minio import Minio

from ay_platform_core.c2_auth.admin_router import router as c2_admin_router
from ay_platform_core.c2_auth.config import AuthConfig
from ay_platform_core.c2_auth.db.repository import AuthRepository
from ay_platform_core.c2_auth.projects_router import router as c2_projects_router
from ay_platform_core.c2_auth.router import router as c2_router
from ay_platform_core.c2_auth.service import AuthService
from ay_platform_core.c2_auth.service import get_service as c2_get_service
from ay_platform_core.c3_conversation.db.repository import ConversationRepository
from ay_platform_core.c3_conversation.router import router as c3_router
from ay_platform_core.c3_conversation.service import ConversationService
from ay_platform_core.c4_orchestrator.config import OrchestratorConfig
from ay_platform_core.c4_orchestrator.db.repository import OrchestratorRepository
from ay_platform_core.c4_orchestrator.dispatcher.in_process import InProcessDispatcher
from ay_platform_core.c4_orchestrator.domains.code.plugin import CodeDomainPlugin
from ay_platform_core.c4_orchestrator.events.null_publisher import (
    NullPublisher as C4NullPublisher,
)
from ay_platform_core.c4_orchestrator.router import router as c4_router
from ay_platform_core.c4_orchestrator.service import OrchestratorService
from ay_platform_core.c5_requirements.db.repository import RequirementsRepository
from ay_platform_core.c5_requirements.events.null_publisher import (
    NullPublisher as C5NullPublisher,
)
from ay_platform_core.c5_requirements.router import router as c5_router
from ay_platform_core.c5_requirements.service import RequirementsService
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage
from ay_platform_core.c6_validation.config import ValidationConfig
from ay_platform_core.c6_validation.db.repository import ValidationRepository
from ay_platform_core.c6_validation.plugin.registry import get_registry as c6_get_registry
from ay_platform_core.c6_validation.router import router as c6_router
from ay_platform_core.c6_validation.service import ValidationService
from ay_platform_core.c6_validation.storage.minio_storage import (
    ValidationSnapshotStorage,
)
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
from ay_platform_core.c9_mcp.config import MCPConfig
from ay_platform_core.c9_mcp.remote import (
    RemoteRequirementsService,
    RemoteValidationService,
)
from ay_platform_core.c9_mcp.router import router as c9_router
from ay_platform_core.c9_mcp.server import MCPServer
from ay_platform_core.c9_mcp.tools.base import build_default_toolset

# ---------------------------------------------------------------------------
# Scripted LLM (re-used pattern from existing e2e/conftest.py)
# ---------------------------------------------------------------------------


class ScriptedLLM:
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


# ---------------------------------------------------------------------------
# Stack
# ---------------------------------------------------------------------------


@dataclass
class PlatformStack:
    """All seven component FastAPI apps + shared backend handles.

    `app_for(component)` returns the FastAPI app to drive an httpx
    AsyncClient against. Backend handles (`arango_db`, `minio_client`)
    let tests assert directly on persisted state.
    """

    c2_app: FastAPI
    c3_app: FastAPI
    c4_app: FastAPI
    c5_app: FastAPI
    c6_app: FastAPI
    c7_app: FastAPI
    c9_app: FastAPI
    c2_service: AuthService
    c5_service: RequirementsService
    c7_service: MemoryService
    arango_client: ArangoClient
    minio_client: Minio
    db_names: dict[str, str]
    bucket_names: dict[str, str]
    arango_password: str
    jwt_secret: str
    scripted_llm: ScriptedLLM
    cleanup: list[Any] = field(default_factory=list)

    def app_for(self, component: str) -> FastAPI:
        return {
            "c2_auth": self.c2_app,
            "c3_conversation": self.c3_app,
            "c4_orchestrator": self.c4_app,
            "c5_requirements": self.c5_app,
            "c6_validation": self.c6_app,
            "c7_memory": self.c7_app,
            "c9_mcp": self.c9_app,
        }[component]

    def db_for(self, component: str) -> Any:
        """Return the python-arango StandardDatabase for `component`."""
        return self.arango_client.db(
            self.db_names[component],
            username="root",
            password=self.arango_password,
        )

    def bucket_for(self, component: str) -> str:
        return self.bucket_names[component]


# ---------------------------------------------------------------------------
# Per-component build helpers
# ---------------------------------------------------------------------------


def _build_c2(
    arango_url: str, arango_password: str, db_name: str, jwt_secret: str
) -> tuple[FastAPI, AuthService]:
    repo = AuthRepository.from_config(arango_url, db_name, "root", arango_password)
    repo._ensure_collections_sync()
    config = AuthConfig.model_validate(
        {
            "auth_mode": "local",
            "jwt_secret_key": jwt_secret,
            "platform_environment": "testing",
        }
    )
    service = AuthService(config, repo)
    app = FastAPI()
    app.include_router(c2_router, prefix="/auth")
    app.include_router(c2_admin_router, prefix="/admin")
    app.include_router(c2_projects_router, prefix="/api/v1/projects")
    app.dependency_overrides[c2_get_service] = lambda: service
    return app, service


def _build_c3(
    client: ArangoClient,
    db_name: str,
    password: str,
    *,
    memory_service: MemoryService | None = None,
    llm_client: LLMGatewayClient | None = None,
) -> tuple[FastAPI, ConversationService]:
    db = client.db(db_name, username="root", password=password)
    repo = ConversationRepository(db)
    repo._ensure_collections_sync()
    service = ConversationService(
        repo, memory_service=memory_service, llm_client=llm_client,
    )
    app = FastAPI()
    app.include_router(c3_router)
    app.state.conversation_service = service
    return app, service


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


def _build_c5(
    client: ArangoClient,
    db_name: str,
    password: str,
    minio_client: Minio,
    bucket: str,
) -> tuple[FastAPI, RequirementsService]:
    db = client.db(db_name, username="root", password=password)
    repo = RequirementsRepository(db)
    repo._ensure_collections_sync()
    storage = RequirementsStorage(minio_client, bucket)
    storage._ensure_bucket_sync()
    service = RequirementsService(repo, storage, C5NullPublisher())
    app = FastAPI()
    app.include_router(c5_router)
    app.state.requirements_service = service
    return app, service


def _build_c6(
    client: ArangoClient,
    db_name: str,
    password: str,
    minio_client: Minio,
    bucket: str,
) -> FastAPI:
    db = client.db(db_name, username="root", password=password)
    repo = ValidationRepository(db)
    repo._ensure_collections_sync()
    snapshot_store = ValidationSnapshotStorage(minio_client, bucket)
    snapshot_store._ensure_bucket_sync()
    service = ValidationService(
        config=ValidationConfig(),
        registry=c6_get_registry(),
        repo=repo,
        snapshot_store=snapshot_store,
    )
    app = FastAPI()
    app.include_router(c6_router)
    app.state.validation_service = service
    return app


def _build_c7(
    client: ArangoClient,
    db_name: str,
    password: str,
    minio_client: Minio,
    bucket: str,
    *,
    llm_client: LLMGatewayClient | None = None,
) -> tuple[FastAPI, MemoryService]:
    db = client.db(db_name, username="root", password=password)
    repo = MemoryRepository(db)
    repo._ensure_collections_sync()
    embedder = DeterministicHashEmbedder()
    storage = MemorySourceStorage(minio_client, bucket)
    storage._ensure_bucket_sync()
    kg_repo = KGRepository(db)
    kg_repo._ensure_collections_sync()
    service = MemoryService(
        config=MemoryConfig(),
        repo=repo,
        embedder=embedder,
        storage=storage,
        kg_repo=kg_repo,
        llm_client=llm_client,
    )
    app = FastAPI()
    app.include_router(c7_router)
    app.dependency_overrides[c7_get_service] = lambda: service
    return app, service


def _build_c9(
    c5_app: FastAPI,
    c6_app: FastAPI,
) -> tuple[FastAPI, list[httpx.AsyncClient]]:
    """C9 holds Remote services that call C5/C6 over HTTP. In-process tests
    point those Remote services at the c5/c6 ASGI apps via MockTransport.
    Returns the app + the httpx clients for the caller to aclose() on
    teardown.
    """
    c5_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c5_app), base_url="http://c5"
    )
    c6_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c6_app), base_url="http://c6"
    )
    c5_remote = RemoteRequirementsService("http://c5", c5_http)
    c6_remote = RemoteValidationService("http://c6", c6_http)
    tools = build_default_toolset(c5_service=c5_remote, c6_service=c6_remote)
    server = MCPServer(MCPConfig(), tools)
    app = FastAPI()
    app.include_router(c9_router)
    app.state.mcp_server = server
    return app, [c5_http, c6_http]


# ---------------------------------------------------------------------------
# Top-level builder
# ---------------------------------------------------------------------------


@asynccontextmanager
async def build_stack(
    arango_url: str,
    arango_password: str,
    minio_endpoint: str,
    minio_access: str,
    minio_secret: str,
) -> AsyncIterator[PlatformStack]:
    """Build the full 7-component stack for one test session."""
    client = ArangoClient(hosts=arango_url)
    sys_db = client.db("_system", username="root", password=arango_password)

    components = ["c2_auth", "c3_conversation", "c4_orchestrator",
                  "c5_requirements", "c6_validation", "c7_memory"]
    db_names = {c: f"e2e_authmtx_{c}_{uuid.uuid4().hex[:6]}" for c in components}
    for db_name in db_names.values():
        sys_db.create_database(db_name)

    minio_client = Minio(
        minio_endpoint,
        access_key=minio_access,
        secret_key=minio_secret,
        secure=False,
    )
    bucket_names = {
        "c5_requirements": f"e2e-authmtx-c5-{uuid.uuid4().hex[:6]}",
        "c6_validation": f"e2e-authmtx-c6-{uuid.uuid4().hex[:6]}",
        "c7_memory": f"e2e-authmtx-c7-{uuid.uuid4().hex[:6]}",
    }

    cleanup: list[Any] = []
    jwt_secret = "auth-matrix-test-secret-32-chars-min!"

    try:
        c2_app, c2_service = _build_c2(
            arango_url, arango_password, db_names["c2_auth"], jwt_secret
        )

        # LLM gateway client + scripted upstream — needed by C4 (orchestrator)
        # AND by C3 (Phase D RAG flow). Built before c3/c4 so both can wire it.
        scripted_llm = ScriptedLLM()
        mock_llm_app = _build_mock_llm_app(scripted_llm)
        llm_transport = httpx.ASGITransport(app=mock_llm_app)
        llm_http = httpx.AsyncClient(transport=llm_transport, base_url="http://mock/v1")
        cleanup.append(("http_client", llm_http))
        llm_client = LLMGatewayClient(
            ClientSettings(gateway_url="http://mock/v1"),
            bearer_token="auth-matrix-test-token",
            http_client=llm_http,
        )
        c4_app, _c4_service = _build_c4(
            client, db_names["c4_orchestrator"], arango_password, llm_client
        )

        c5_app, c5_service = _build_c5(
            client, db_names["c5_requirements"], arango_password,
            minio_client, bucket_names["c5_requirements"],
        )
        c6_app = _build_c6(
            client, db_names["c6_validation"], arango_password,
            minio_client, bucket_names["c6_validation"],
        )
        c7_app, c7_service = _build_c7(
            client, db_names["c7_memory"], arango_password,
            minio_client, bucket_names["c7_memory"],
            llm_client=llm_client,
        )
        # C3 wired LAST so it can consume the live c7_service + llm_client
        # (Phase D RAG flow). The auth-matrix tests still don't drive a
        # full RAG round-trip — they just need the dependencies to exist
        # so the role/isolation matrix runs end-to-end.
        c3_app, _c3_service = _build_c3(
            client, db_names["c3_conversation"], arango_password,
            memory_service=c7_service,
            llm_client=llm_client,
        )
        c9_app, c9_http_clients = _build_c9(c5_app, c6_app)
        for c in c9_http_clients:
            cleanup.append(("http_client", c))

        stack = PlatformStack(
            c2_app=c2_app,
            c3_app=c3_app,
            c4_app=c4_app,
            c5_app=c5_app,
            c6_app=c6_app,
            c7_app=c7_app,
            c9_app=c9_app,
            c2_service=c2_service,
            c5_service=c5_service,
            c7_service=c7_service,
            arango_client=client,
            minio_client=minio_client,
            db_names=db_names,
            bucket_names=bucket_names,
            arango_password=arango_password,
            jwt_secret=jwt_secret,
            scripted_llm=scripted_llm,
            cleanup=cleanup,
        )
        yield stack
    finally:
        for kind, *args in cleanup:
            if kind == "http_client":
                await args[0].aclose()
        # MinIO + Arango cleanup is handled by the conftest's session
        # teardown via the existing `cleanup_arango_database` /
        # `cleanup_minio_bucket` helpers (called there to share with
        # the rest of the e2e suite).
