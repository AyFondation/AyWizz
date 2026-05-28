# =============================================================================
# File: main.py
# Version: 6
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/main.py
# Description: FastAPI app factory for C4 Orchestrator. Wires the in-process
#              dispatcher backed by a real C8 LLM client (the C8 URL is read
#              from C4_LLM_GATEWAY_URL).
#
#              v6 (2026-05-22) : the OpenHands `generate` engine (V2 #2 /
#              R-200-029) is built with an `OpenHandsEngineConfig` derived
#              from the shared C8 client settings (same gateway URL + bearer
#              as the dispatcher) + `C4_OPENHANDS_MODEL`. Gated on
#              `C4_GENERATE_ENGINE=openhands` ; default keeps the dispatcher.
#
#              v5 (2026-05-20) : `C4_DISPATCHER_BACKEND=k8s` selects the
#              new K8sDispatcher (P2.1.c / R-200-030..033) ; default
#              `in-process` keeps the existing dev devloop unchanged.
#              The K8sDispatcher needs a separate MinIO config that
#              describes the endpoint AS THE POD SEES IT (different
#              from the orchestrator's view on Docker Desktop K8s) —
#              read from the `C4_K8S_*` env prefix.
#
#              v4: mounts `documents_router` — the chat-direct DocGen
#              document CRUD surface (D-015 / R-200-153..156).
#              v3: passes the `ArtifactsService` instance into the
#              `OrchestratorService` so the generate phase materialises
#              its `output.files` into the artifacts surface and triggers
#              the Gitea push on completion (R-200-150..152).
#              v2: mounts the project-artifacts surface
#              (`artifacts_router`) under
#              `/api/v1/projects/{pid}/artifacts/*` + instantiates
#              the `ArtifactsService` over the shared MinIO client and
#              the orchestrator repository (which gains the
#              `c4_artifact_runs` collection). Lifespan ensures the
#              bucket exists at startup so a fresh dev stack just
#              works.
#
# @relation implements:R-100-114
# @relation implements:R-200-131
# @relation implements:R-200-151
# =============================================================================

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI
from minio import Minio

from ay_platform_core.c2_auth.gitea_client import GiteaClient
from ay_platform_core.c4_orchestrator.artifacts_router import (
    router as artifacts_router,
)
from ay_platform_core.c4_orchestrator.artifacts_service import ArtifactsService
from ay_platform_core.c4_orchestrator.artifacts_storage import ArtifactStorage
from ay_platform_core.c4_orchestrator.config import OrchestratorConfig
from ay_platform_core.c4_orchestrator.db.repository import OrchestratorRepository
from ay_platform_core.c4_orchestrator.dispatch_storage import DispatchStorage
from ay_platform_core.c4_orchestrator.dispatcher.base import AgentDispatcher
from ay_platform_core.c4_orchestrator.dispatcher.in_process import InProcessDispatcher
from ay_platform_core.c4_orchestrator.dispatcher.k8s import (
    K8sDispatcher,
    K8sDispatcherConfig,
)
from ay_platform_core.c4_orchestrator.documents_router import (
    router as documents_router,
)
from ay_platform_core.c4_orchestrator.domains.base import DomainPlugin
from ay_platform_core.c4_orchestrator.domains.code.plugin import CodeDomainPlugin
from ay_platform_core.c4_orchestrator.domains.documentation.plugin import (
    DocumentationDomainPlugin,
)
from ay_platform_core.c4_orchestrator.events.base import OrchestratorEventPublisher
from ay_platform_core.c4_orchestrator.events.nats_publisher import NatsPublisher
from ay_platform_core.c4_orchestrator.events.null_publisher import NullPublisher
from ay_platform_core.c4_orchestrator.pipeline.generate_engine import (
    OpenHandsEngineConfig,
    build_generate_engine,
)
from ay_platform_core.c4_orchestrator.router import router
from ay_platform_core.c4_orchestrator.service import OrchestratorService
from ay_platform_core.c4_orchestrator.source_router import (
    router as source_router,
)
from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.config import ClientSettings
from ay_platform_core.observability import (
    TraceContextMiddleware,
    configure_logging,
)
from ay_platform_core.observability.auth_guard import AuthGuardMiddleware
from ay_platform_core.observability.config import LoggingSettings


def _resolve_dispatcher(
    cfg: OrchestratorConfig,
    llm_client: LLMGatewayClient,
    dispatch_storage: DispatchStorage,
) -> AgentDispatcher:
    """Pick the dispatcher backend per `C4_DISPATCHER_BACKEND` (R-200-030
    v1 note). `k8s` → ephemeral pods in `c4-workers` ; default `in-process`
    keeps the dev devloop fast and is what every existing test fixture
    uses."""
    if cfg.dispatcher_backend == "k8s":
        return K8sDispatcher(
            config=K8sDispatcherConfig(
                namespace=cfg.k8s_namespace,
                image=cfg.k8s_image,
                image_pull_policy=cfg.k8s_image_pull_policy,
                service_account_name=cfg.k8s_service_account_name,
                sub_agent_timeout_seconds=cfg.sub_agent_timeout_seconds,
                pod_view_minio_endpoint=cfg.k8s_pod_view_minio_endpoint,
                pod_view_minio_access_key=cfg.minio_access_key,
                pod_view_minio_secret_key=cfg.minio_secret_key,
                pod_view_minio_secure=cfg.minio_secure,
                pod_view_c8_gateway_url=cfg.k8s_pod_view_c8_gateway_url,
                pod_view_c8_default_model=cfg.k8s_pod_view_c8_default_model,
                c8_gateway_api_key=cfg.c8_gateway_api_key,
                kubeconfig_path=cfg.k8s_kubeconfig_path,
            ),
            dispatch_storage=dispatch_storage,
        )
    return InProcessDispatcher(llm_client)


def _resolve_domain_plugin(name: str) -> DomainPlugin:
    """R-200-061 v2 / P4.a — per-deployment domain plug-in selection.
    Unknown values fall back to `code` with a WARNING (avoids a silent
    typo blocking pod boot ; the spec considers `code` the safe default)."""
    if name == "documentation":
        return DocumentationDomainPlugin()
    if name != "code":
        import logging  # noqa: PLC0415

        logging.getLogger("c4_orchestrator").warning(
            "Unknown C4_DOMAIN=%r ; falling back to `code`", name,
        )
    return CodeDomainPlugin()


def create_app(config: OrchestratorConfig | None = None) -> FastAPI:
    cfg = config or OrchestratorConfig()
    log_cfg = LoggingSettings()
    configure_logging(component="c4_orchestrator", settings=log_cfg)
    arango_client = ArangoClient(hosts=cfg.arango_url)
    db = arango_client.db(
        cfg.arango_db, username=cfg.arango_username, password=cfg.arango_password
    )
    repo = OrchestratorRepository(db)

    llm_settings = ClientSettings()
    # Bearer = the single shared `C8_GATEWAY_API_KEY` (via ClientSettings),
    # with the "no-auth" placeholder for mock/Ollama-direct (was a
    # hardcoded "c4-orchestrator" placeholder the proxy would 401).
    llm_client = LLMGatewayClient(
        llm_settings, bearer_token=llm_settings.effective_bearer,
    )

    # MinIO client + artifacts service. Same bucket (`orchestrator`)
    # as the existing run state ; artifacts live under the
    # `c4-artifacts/` prefix (R-200-130) — clear separation from the
    # `c4-runs/` prefix that holds orchestrator internal state.
    minio_client = Minio(
        cfg.minio_endpoint,
        access_key=cfg.minio_access_key,
        secret_key=cfg.minio_secret_key,
        secure=cfg.minio_secure,
    )
    artifact_storage = ArtifactStorage(minio_client, cfg.minio_bucket)
    # Gitea client — wired when `C4_GITEA_BASE_URL` is non-empty.
    # Pushes artifacts at run completion (R-200-146) AND backs the
    # `/git/commits` proxy (R-200-147).
    gitea: GiteaClient | None = None
    if cfg.gitea_base_url:
        gitea = GiteaClient(
            base_url=cfg.gitea_base_url,
            admin_username=cfg.gitea_admin_username,
            admin_password=cfg.gitea_admin_password,
        )
    artifacts_service = ArtifactsService(
        repo=repo, storage=artifact_storage, gitea=gitea,
    )

    # NATS event publisher (R-200-070). Wired only when `C4_NATS_URL`
    # is non-empty ; otherwise we fall back to the existing
    # NullPublisher (events live in the trace ledger per R-200-200).
    publisher: OrchestratorEventPublisher
    nats_publisher: NatsPublisher | None = None
    if cfg.nats_url.strip():
        servers = [s.strip() for s in cfg.nats_url.split(",") if s.strip()]
        nats_publisher = NatsPublisher(
            servers=servers if len(servers) > 1 else servers[0],
            connect_timeout=cfg.nats_connect_timeout_seconds,
        )
        publisher = nats_publisher
    else:
        publisher = NullPublisher()

    # R-200-030 v1 note : `C4_DISPATCHER_BACKEND` picks `in-process` (dev
    # fallback, keeps backbone iteration fast) vs `k8s` (spec-correct
    # ephemeral pods). Same DispatchStorage object backs the bundle
    # round-trip ; the in-process path doesn't use it, so building it
    # unconditionally is cheap.
    dispatch_storage = DispatchStorage(minio_client, cfg.minio_bucket)
    dispatcher = _resolve_dispatcher(cfg, llm_client, dispatch_storage)

    domain_plugin = _resolve_domain_plugin(cfg.domain)

    # V2 #2 : opt into the OpenHands `generate` engine via C4_GENERATE_ENGINE
    # (default `in_process` → None → the dispatcher path is unchanged). When
    # enabled, OpenHands routes through the SAME C8/LiteLLM endpoint + bearer
    # the dispatcher uses (R-200-029) ; the model is a C8 model_list name.
    generate_engine = build_generate_engine(
        cfg.generate_engine,
        OpenHandsEngineConfig(
            gateway_url=llm_settings.gateway_url,
            api_key=llm_settings.effective_bearer,
            model=cfg.openhands_model,
            max_iterations=cfg.openhands_max_iterations,
        ),
    )

    service = OrchestratorService(
        config=cfg,
        repo=repo,
        dispatcher=dispatcher,
        domain_plugin=domain_plugin,
        publisher=publisher,
        artifacts_service=artifacts_service,
        generate_engine=generate_engine,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        repo._ensure_collections_sync()
        # Idempotent ; covers the rare case of a fresh MinIO bucket.
        await artifact_storage.ensure_bucket()
        if nats_publisher is not None:
            await nats_publisher.connect()
        yield
        await llm_client.aclose()
        if gitea is not None:
            await gitea.aclose()
        if nats_publisher is not None:
            await nats_publisher.aclose()

    app = FastAPI(title="C4 Orchestrator", lifespan=lifespan)
    app.add_middleware(AuthGuardMiddleware, component="c4_orchestrator")
    app.add_middleware(TraceContextMiddleware, sample_rate=log_cfg.trace_sample_rate)
    app.include_router(router)
    app.include_router(artifacts_router)
    app.include_router(documents_router)
    app.include_router(source_router)
    app.state.orchestrator_service = service
    app.state.artifacts_service = artifacts_service

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "component": "c4_orchestrator"}

    return app


app = create_app()
