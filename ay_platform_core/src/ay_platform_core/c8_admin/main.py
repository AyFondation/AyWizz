# =============================================================================
# File: main.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_admin/main.py
# Description: FastAPI app factory for the C8 admin tier (R-100-114). Hosts the
#              platform LLM registry admin surface behind Traefik forward-auth.
#              Deployed via the shared image with `COMPONENT_MODULE=c8_admin`
#              (uvicorn `ay_platform_core.c8_admin.main:app`).
#
#              Wires: Arango registry repository + SecretCipher (master key
#              from env) + LLMRegistryService. On startup it ensures the
#              collection and idempotently seeds missing models from the
#              canonical LiteLLM config (prices/capabilities), without ever
#              seeding a key (env fallback until an operator sets one).
# =============================================================================

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c8_admin.config import C8AdminConfig
from ay_platform_core.c8_llm.config import LiteLLMConfig
from ay_platform_core.c8_llm.quota.repository import QuotaRepository
from ay_platform_core.c8_llm.quota.router import router as quota_router
from ay_platform_core.c8_llm.quota.service import QuotaService
from ay_platform_core.c8_llm.registry.catalog_repository import TenantCatalogRepository
from ay_platform_core.c8_llm.registry.catalog_router import router as catalog_router
from ay_platform_core.c8_llm.registry.catalog_service import TenantCatalogService
from ay_platform_core.c8_llm.registry.provider_repository import LLMProviderRepository
from ay_platform_core.c8_llm.registry.provider_router import router as provider_router
from ay_platform_core.c8_llm.registry.provider_service import LLMProviderService
from ay_platform_core.c8_llm.registry.repository import LLMRegistryRepository
from ay_platform_core.c8_llm.registry.router import router as registry_router
from ay_platform_core.c8_llm.registry.seed import seed_missing
from ay_platform_core.c8_llm.registry.service import LLMRegistryService
from ay_platform_core.crypto.secret_cipher import SecretCipher, SecretCipherError
from ay_platform_core.observability import (
    TraceContextMiddleware,
    configure_logging,
)
from ay_platform_core.observability.auth_guard import AuthGuardMiddleware
from ay_platform_core.observability.config import LoggingSettings


def _cipher_from_env() -> SecretCipher | None:
    """Build the SecretCipher from the env master key, or None when absent.
    None disables key WRITES (503) while keeping read/catalogue/resolve live."""
    import logging  # noqa: PLC0415 — cold path

    try:
        return SecretCipher.from_env()
    except SecretCipherError:
        logging.getLogger("c8_admin").warning(
            "AY_SECRET_MASTER_KEY absent — registry key management disabled "
            "(list / catalogue / resolve still served)"
        )
        return None


def _load_litellm_config(path: str) -> LiteLLMConfig | None:
    """Validate the mounted LiteLLM config for seeding. None when unset or
    unreadable — seeding is then skipped (the registry stays empty until
    populated via the API)."""
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    return LiteLLMConfig.model_validate(raw)


def create_app(
    config: C8AdminConfig | None = None,
    *,
    cipher: SecretCipher | None = None,
) -> FastAPI:
    cfg = config or C8AdminConfig()
    log_cfg = LoggingSettings()
    configure_logging(component="c8_admin", settings=log_cfg)
    db = ArangoClient(hosts=cfg.arango_url).db(
        cfg.arango_db, username=cfg.arango_username, password=cfg.arango_password,
    )
    repo = LLMRegistryRepository(db)
    provider_repo = LLMProviderRepository(db)
    catalog_repo = TenantCatalogRepository(db)
    # The master key lives ONLY in the environment (Tier-2 .env.secret /
    # K8s Secret). When ABSENT the app still boots and serves read / catalogue /
    # resolve ; only provider-key WRITES are disabled (503). The KEY now lives on
    # the PROVIDER (one credential per endpoint), so the cipher is wired into the
    # provider service ; the model registry holds no secret.
    secret_cipher = cipher if cipher is not None else _cipher_from_env()
    service = LLMRegistryService(repo)
    provider_service = LLMProviderService(provider_repo, secret_cipher)
    catalog_service = TenantCatalogService(catalog_repo, service)
    quota_service = QuotaService(QuotaRepository(db))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await repo.ensure_collections()
        await provider_repo.ensure_collections()
        await catalog_repo.ensure_collections()
        if cfg.seed_on_start:
            litellm_cfg = _load_litellm_config(cfg.litellm_config_path)
            if litellm_cfg is not None:
                await seed_missing(repo, provider_repo, litellm_cfg)
        yield

    app = FastAPI(title="C8 Admin", lifespan=lifespan)
    app.add_middleware(
        AuthGuardMiddleware,
        component="c8_admin",
        exempt_prefixes=["/health"],
    )
    app.add_middleware(TraceContextMiddleware, sample_rate=log_cfg.trace_sample_rate)
    app.include_router(registry_router)
    app.include_router(provider_router)
    app.include_router(catalog_router)
    app.include_router(quota_router)
    app.state.registry_service = service
    app.state.provider_service = provider_service
    app.state.catalog_service = catalog_service
    app.state.quota_service = quota_service

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "component": "c8_admin"}

    return app


app = create_app()
