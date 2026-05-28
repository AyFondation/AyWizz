# =============================================================================
# File: main.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/main.py
# Description: C8 cost receiver — the in-process FastAPI service of the C8
#              tier (the LiteLLM proxy itself is off-the-shelf, §4.5). It
#              exposes ONE internal endpoint that the proxy's mounted cost
#              forwarder POSTs to after every call ; it computes the cost
#              and persists one `llm_calls` row in ArangoDB (R-800-070).
#
#              Deployed via the shared image with `COMPONENT_MODULE=c8_llm`
#              (uvicorn `ay_platform_core.c8_llm.main:app`, per R-100-114).
#              Internal-only : reached from the proxy inside the cluster
#              network, never from users — no forward-auth guard. A
#              NetworkPolicy SHALL restrict ingress to the proxy in prod.
#
# @relation implements:R-100-114
# @relation implements:R-800-070
# =============================================================================

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ay_platform_core.c8_llm.callbacks.cost_tracker import build_call_record
from ay_platform_core.c8_llm.config import LiteLLMConfig, ModelInfo
from ay_platform_core.c8_llm.cost_sink_arango import ArangoCallRecordSink
from ay_platform_core.c8_llm.models import CostCallEnvelope
from ay_platform_core.observability import (
    TraceContextMiddleware,
    configure_logging,
)
from ay_platform_core.observability.config import LoggingSettings


class CostReceiverConfig(BaseSettings):
    """Runtime config for the C8 cost receiver. Arango coordinates are the
    shared platform knobs (un-prefixed `ARANGO_*` via validation_alias) ;
    `litellm_config_path` points at the proxy config whose `model_list`
    provides the cost catalog (model_name → ModelInfo)."""

    model_config = SettingsConfigDict(
        env_prefix="c8_", extra="ignore", populate_by_name=True,
    )

    arango_url: str = Field(default="http://arangodb:8529", validation_alias="ARANGO_URL")
    arango_db: str = Field(default="platform", validation_alias="ARANGO_DB")
    arango_username: str = Field(default="ay_app", validation_alias="ARANGO_USERNAME")
    arango_password: str = Field(default="changeme", validation_alias="ARANGO_PASSWORD")
    # Path to the LiteLLM config YAML (mounted). Its `model_list` is the
    # cost catalog. Empty / missing → cost recorded as 0.0 (the call is
    # still logged ; cost is a computed field).
    litellm_config_path: str = ""


def _load_catalog(path: str) -> dict[str, ModelInfo]:
    """Build {model_name → ModelInfo} from the LiteLLM config YAML. Returns
    an empty catalog (cost 0.0) when the path is unset or unreadable —
    cost tracking is best-effort and never blocks call recording."""
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        return {}
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    cfg = LiteLLMConfig.model_validate(raw)
    return {entry.model_name: entry.model_info for entry in cfg.model_list}


def create_app(config: CostReceiverConfig | None = None) -> FastAPI:
    cfg = config or CostReceiverConfig()
    log_cfg = LoggingSettings()
    configure_logging(component="c8_cost_receiver", settings=log_cfg)
    catalog = _load_catalog(cfg.litellm_config_path)
    db = ArangoClient(hosts=cfg.arango_url).db(
        cfg.arango_db, username=cfg.arango_username, password=cfg.arango_password,
    )
    sink = ArangoCallRecordSink(db)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await asyncio.to_thread(sink.ensure_collection)
        yield

    app = FastAPI(title="C8 Cost Receiver", lifespan=lifespan)
    app.add_middleware(TraceContextMiddleware, sample_rate=log_cfg.trace_sample_rate)
    # Exposed for tests / introspection.
    app.state.cost_sink = sink
    app.state.cost_catalog = catalog

    @app.post("/internal/llm-calls")
    async def ingest(envelope: CostCallEnvelope) -> dict[str, str]:
        """Record one LLM call (R-800-070). Best-effort from the caller's
        view : the forwarder swallows any error, so a transient failure
        here simply drops the record rather than affecting an LLM call."""
        record = build_call_record(envelope, catalog)
        await sink.insert(record)
        return {"stored": record.call_id}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "component": "c8_cost_receiver"}

    return app


app = create_app()
