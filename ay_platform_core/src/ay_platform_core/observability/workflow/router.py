# =============================================================================
# File: router.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/observability/workflow/router.py
# Description: Mountable FastAPI APIRouter exposing the workflow-synthesis
#              HTTP surface. Identical wire shape to the test-tier
#              endpoints in `_observability/main.py`; backed by any
#              `SpanSource` (buffer / loki / elasticsearch). Production
#              apps mount this router; the test-tier collector also
#              re-uses it via `BufferSpanSource` to keep one code path.
#
# @relation implements:R-100-124
# =============================================================================

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status

from ay_platform_core._observability.synthesis import (
    list_recent_traces,
    synthesise_workflow,
)
from ay_platform_core.observability.workflow.sources import SpanSource

_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def make_workflow_router(source: SpanSource) -> APIRouter:
    """Build a FastAPI router that exposes:

    - `GET /workflows/{trace_id}` — synthesised envelope for ONE trace.
    - `GET /workflows?recent=N` — compact summaries of the most recent N traces.

    The router is stateless beyond holding a reference to `source`. Mount
    it on any FastAPI app (production K8s service or test-tier
    collector); cleanup of the source's HTTP resources is the caller's
    responsibility (typically via a `lifespan` context manager).
    """
    router = APIRouter()

    @router.get(
        "/workflows/{trace_id}",
        responses={
            status.HTTP_404_NOT_FOUND: {
                "description": "no span_summary records for the given trace",
            },
            status.HTTP_400_BAD_REQUEST: {
                "description": "trace_id is not 32 hex characters",
            },
        },
    )
    async def workflow(trace_id: str) -> dict[str, Any]:
        if not _TRACE_ID_RE.fullmatch(trace_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="trace_id must be 32 hex characters",
            )
        spans = await source.fetch_for_trace(trace_id)
        if not spans:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"no span_summary records found for trace_id {trace_id} "
                    f"in the configured time window"
                ),
            )
        return synthesise_workflow(spans)

    @router.get("/workflows")
    async def workflows(
        recent: Annotated[int, Query(ge=1, le=200)] = 10,
        fetch_limit: Annotated[int, Query(ge=1, le=100_000)] = 10_000,
    ) -> list[dict[str, Any]]:
        # `fetch_limit` caps the upstream pull (Loki/ES) before we group
        # by trace_id and take the top `recent`. The default 10_000 is
        # generous for a 24h window of normal platform traffic; lower it
        # explicitly when the upstream is slow or expensive.
        spans = await source.fetch_recent(limit=fetch_limit)
        return list_recent_traces(spans, limit=recent)

    return router
