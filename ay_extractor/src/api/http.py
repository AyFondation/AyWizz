# src/api/http.py — v1
"""FastAPI HTTP surface for C13 (D-020 R-100-125 v2 §2).

Exposes exactly three endpoints — the sole platform-facing contract:

  POST /analyze            — async kick-off, returns {run_id, status}
  GET  /status/{run_id}    — current RunManifest excerpt
  GET  /healthz            — liveness/readiness

The HTTP layer is intentionally thin: it parses the request body, builds
a `DocumentInput` + `Metadata`, schedules `facade.analyze` as a
background task, and tracks the run_id → status mapping in memory + in
MinIO (`status.json` is the authoritative source — the in-memory cache
serves fast polls).

In-cluster deployment (C12 n8n → C13):
  n8n POSTs the MinIO key of the raw upload + tenant/project/source ids,
  receives a run_id back immediately, then polls /status until terminal.
  C13 itself reads the raw bytes from MinIO via the configured writer
  (R-400-220 v2 source layout).

Standalone dev / direct invocation:
  Curl POST with the same payload reaching localhost:8000.

Per CLAUDE.md §4.5 (infra layout), the C13 container's CMD launches
this app via uvicorn — see `infra/c13_extractor/docker/Dockerfile`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

try:
    from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, status
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "FastAPI not installed — install with `pip install ayextractor[http]` "
        "to use the HTTP surface."
    ) from exc

from ayextractor.api.facade import analyze
from ayextractor.api.models import DocumentInput, Metadata
from ayextractor.config.settings import Settings

logger = logging.getLogger(__name__)


# --- Request / response schemas --------------------------------------------


class AnalyzeRequest(BaseModel):
    """Payload for `POST /analyze`.

    The request is intentionally narrow: C12 (n8n) provides the MinIO
    key of the raw upload and the scoping triple (tenant, project,
    source). All extraction internals (formats, quality tiers, urgency,
    config overrides) come from this request body too.

    Fields:
      tenant_id / project_id / source_id — MinIO scoping (R-400-220 v2).
      raw_object_key — MinIO key of the uploaded file (read by AyExtractor).
      filename / mime_type / format — input format hints.
      quality_tier — `minimal` / `standard` / `high` (R-400-224).
      urgency — `interactive` / `background` (R-100-125 v2 §2).
      document_type — optional document classification (book/article/...).
      language — optional language hint (auto-detected if absent).
      config_overrides — passthrough to `Metadata.config_overrides`.
    """

    tenant_id: str = Field(..., min_length=1)
    project_id: str = Field(..., min_length=1)
    source_id: str = Field(..., min_length=1)
    raw_object_key: str = Field(..., min_length=1)
    filename: str = Field(..., min_length=1)
    mime_type: str = Field(default="application/octet-stream")
    format: str = Field(..., min_length=1)
    quality_tier: Literal["minimal", "standard", "high"] = "minimal"
    urgency: Literal["interactive", "background"] = "interactive"
    document_type: str = "report"
    language: str | None = None
    config_overrides: dict[str, Any] | None = None


class AnalyzeResponse(BaseModel):
    """Synchronous response to `POST /analyze` — returned BEFORE the run
    completes. Caller polls `GET /status/{run_id}` for terminal status.
    """

    run_id: str
    status: Literal["running"] = "running"
    accepted_at: str  # ISO-8601 UTC


class StatusResponse(BaseModel):
    """`GET /status/{run_id}` response — mirror of `status.json` per R-400-220 v2."""

    run_id: str
    status: Literal["running", "completed", "failed"]
    urgency: Literal["interactive", "background"]
    phases_completed: list[str]
    errors: list[dict[str, Any]] = Field(default_factory=list)
    started_at: str | None = None
    completed_at: str | None = None


class HealthResponse(BaseModel):
    """`GET /healthz` response."""

    status: Literal["ok"] = "ok"
    version: str = "unknown"


# --- In-memory run registry -------------------------------------------------
#
# Best-effort cache of "in-flight" runs — keyed by run_id. The
# authoritative source is `status.json` in MinIO (written by
# `facade.analyze` at every phase boundary), so a pod restart drops the
# in-memory state but the operator can still query status via MinIO.

_runs: dict[str, dict[str, Any]] = {}


def _put_run(run_id: str, status_payload: dict[str, Any]) -> None:
    _runs[run_id] = status_payload


def _get_run(run_id: str) -> dict[str, Any] | None:
    return _runs.get(run_id)


# --- FastAPI app factory ----------------------------------------------------


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app, optionally with a pre-built Settings.

    Used both by `uvicorn ayextractor.api.http:app` (which calls
    `create_app()` with default Settings) and by tests that pass a
    Settings stub.
    """
    settings = settings or Settings()
    app = FastAPI(
        title="C13 — Extraction & Chunking Service (AyExtractor)",
        version=_resolve_version(),
        description=(
            "D-020 R-100-125 v2 contract surface. Thin async wrapper "
            "around `facade.analyze`, writing artifacts to MinIO."
        ),
    )

    @app.get("/healthz", response_model=HealthResponse, tags=["meta"])
    async def healthz() -> HealthResponse:
        """Liveness/readiness probe — no auth, no side effects."""
        return HealthResponse(status="ok", version=_resolve_version())

    @app.post(
        "/analyze",
        response_model=AnalyzeResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["c13"],
    )
    async def analyze_endpoint(
        payload: AnalyzeRequest,
        background_tasks: BackgroundTasks,
        request: Request,
    ) -> AnalyzeResponse:
        """Kick off a Phase 1+2 analysis and return immediately.

        The actual pipeline runs as a background task; the caller polls
        `GET /status/{run_id}` for completion. The run_id is generated
        client-side (here) so the response can be returned before the
        pipeline starts.

        Failures during the pipeline are persisted to
        `{prefix}/status.json` in MinIO; this endpoint NEVER returns a
        non-2xx for pipeline failures (only for malformed requests).
        """
        from ayextractor.api.facade import _generate_document_id, _generate_run_id

        # We pre-mint document_id + run_id here so the caller can poll
        # IMMEDIATELY — facade.analyze would mint them too, but later.
        document_id = _generate_document_id()
        run_id = _generate_run_id(document_id)
        accepted_at = datetime.now(timezone.utc).isoformat()

        _put_run(run_id, {
            "status": "running",
            "urgency": payload.urgency,
            "phases_completed": [],
            "errors": [],
            "started_at": accepted_at,
            "completed_at": None,
        })

        # Schedule the actual work. We do NOT await — the function
        # returns to the caller while the pipeline runs.
        background_tasks.add_task(
            _execute_analyze,
            payload=payload,
            document_id=document_id,
            run_id=run_id,
            settings=settings,
        )

        return AnalyzeResponse(
            run_id=run_id,
            status="running",
            accepted_at=accepted_at,
        )

    @app.get(
        "/status/{run_id}",
        response_model=StatusResponse,
        tags=["c13"],
    )
    async def status_endpoint(
        run_id: str,
        tenant_id: str | None = None,
        project_id: str | None = None,
        source_id: str | None = None,
    ) -> StatusResponse:
        """Return the current RunManifest excerpt for a given run.

        Resolution order (D-020 session 6):
          1. In-memory `_runs` cache — fastest path, hits on the same pod
             that launched the run.
          2. MinIO `status.json` fallback — when `tenant_id` /
             `project_id` / `source_id` query params are supplied, the
             handler reads the persisted `status.json` from MinIO. This
             survives pod restarts and lets the n8n workflow keep
             polling across the C13 deployment's lifecycle.

        Without the scope query params (or when MinIO lookup fails) the
        handler returns 404. The n8n workflow `extract_and_ingest.json`
        v1 supplies the scope automatically — operators using bare CURL
        SHALL supply `?tenant_id=…&project_id=…&source_id=…`.
        """
        cached = _get_run(run_id)
        if cached is not None:
            return StatusResponse(run_id=run_id, **cached)

        # MinIO-backed fallback (D-020 session 6).
        if tenant_id and project_id and source_id:
            payload = await _read_status_from_minio(
                tenant_id=tenant_id,
                project_id=project_id,
                source_id=source_id,
                run_id=run_id,
                settings=settings,
            )
            if payload is not None:
                # status.json on disk uses the same field names; supply
                # safe defaults for the few fields the StatusResponse
                # contract requires.
                return StatusResponse(
                    run_id=run_id,
                    status=payload.get("status", "running"),
                    urgency=payload.get("urgency", "interactive"),
                    phases_completed=payload.get("phases_completed", []),
                    errors=payload.get("errors", []),
                    started_at=payload.get("started_at"),
                    completed_at=payload.get("completed_at"),
                )

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"run_id={run_id} not in the in-memory registry. Supply "
                "?tenant_id=…&project_id=…&source_id=… on the query string "
                "to enable the MinIO-backed `status.json` lookup."
            ),
        )

    return app


async def _read_status_from_minio(
    *,
    tenant_id: str,
    project_id: str,
    source_id: str,
    run_id: str,
    settings: Settings,
) -> dict[str, Any] | None:
    """Best-effort read of `status.json` from MinIO (D-020 session 6).

    Returns the parsed JSON payload or None on any failure (missing
    object, network error, malformed JSON, MinIO unreachable). 404
    surfaces to the caller from the handler when this returns None.
    """
    import json as _json

    from ayextractor.storage.minio_layout import RunPrefix, status_key
    from ayextractor.storage.writer_factory import create_writer

    try:
        writer = create_writer(settings)
        prefix = RunPrefix(
            tenant_id=tenant_id,
            project_id=project_id,
            source_id=source_id,
            run_id=run_id,
        )
        raw = await writer.read(status_key(prefix))
        return _json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001 — fallback is best-effort
        logger.debug(
            "status.json fallback read failed for run %s (tenant=%s project=%s)",
            run_id, tenant_id, project_id,
        )
        return None


# --- Background execution ---------------------------------------------------


async def _execute_analyze(
    payload: AnalyzeRequest,
    document_id: str,
    run_id: str,
    settings: Settings,
) -> None:
    """Run the pipeline + write artifacts, updating in-memory status.

    Errors are caught and persisted to the in-memory cache + via
    `facade.analyze` to `status.json` in MinIO. This function NEVER
    raises (called from BackgroundTasks).
    """
    try:
        raw_bytes = await _read_raw_object(payload.raw_object_key, settings)
        document = DocumentInput(
            content=raw_bytes,
            format=payload.format,
            filename=payload.filename,
        )
        metadata = Metadata(
            document_id=document_id,
            document_type=payload.document_type,
            output_path=Path("/tmp/c13"),  # unused in MinIO mode
            language=payload.language,
            tenant_id=payload.tenant_id,
            project_id=payload.project_id,
            source_id=payload.source_id,
            quality_tier=payload.quality_tier,
            urgency=payload.urgency,
            # D-020 session 5 fix — pre-minted run_id propagated through
            # facade.analyze so the polled run_id matches the MinIO
            # artifact prefix.
            run_id=run_id,
            config_overrides=_build_overrides(payload.config_overrides),
        )

        result = await analyze(document, metadata, settings=settings)

        _put_run(run_id, {
            "status": "completed",
            "urgency": payload.urgency,
            "phases_completed": ["01_extraction", "02_chunks"],
            "errors": [],
            "started_at": _get_run(run_id)["started_at"],
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "artifact_prefix": result.artifact_prefix,
            "manifest_key": result.manifest_key,
            "chunks_key": result.chunks_key,
            "embeddings_key": result.embeddings_key,
        })
        # D-020 session 5 — run_id mismatch resolved: HTTP-side pre-mint
        # propagates through Metadata.run_id, so result.run_id == run_id.
        assert result.run_id == run_id, (
            f"facade did not honour pre-minted run_id: "
            f"HTTP={run_id} vs facade={result.run_id}"
        )
        logger.info("Run %s completed (chunks=%d)", run_id, result.chunks_count)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Run %s failed", run_id)
        cached = _get_run(run_id) or {}
        _put_run(run_id, {
            "status": "failed",
            "urgency": payload.urgency,
            "phases_completed": cached.get("phases_completed", []),
            "errors": [{
                "error_class": exc.__class__.__name__,
                "error_message": str(exc),
            }],
            "started_at": cached.get("started_at"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })


async def _read_raw_object(object_key: str, settings: Settings) -> bytes:
    """Read the raw bytes of the uploaded source from MinIO.

    The writer abstraction exposes `read()` so we re-use it on the
    source-raw key. The source bucket and the artifact bucket are
    typically the same; the path conventions differ (the source-raw
    key is on the platform's `sources/...` prefix per R-400-020 v2,
    not on the C13 artifact layout).
    """
    from ayextractor.storage.writer_factory import create_writer
    writer = create_writer(settings)
    return await writer.read(object_key)


def _build_overrides(overrides_dict: dict[str, Any] | None) -> Any:
    """Project a dict into `api.models.ConfigOverrides` (or None)."""
    if not overrides_dict:
        return None
    from ayextractor.api.models import ConfigOverrides
    return ConfigOverrides(**overrides_dict)


def _resolve_version() -> str:
    try:
        from ayextractor.version import __version__
        return __version__
    except Exception:  # noqa: BLE001
        return "unknown"


# Module-level `app` for `uvicorn ayextractor.api.http:app`.
app = create_app()
