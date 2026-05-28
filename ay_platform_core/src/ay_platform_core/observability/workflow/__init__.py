# =============================================================================
# File: __init__.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/observability/workflow/__init__.py
# Description: Production-tier workflow synthesis library. Storage-agnostic
#              `SpanSource` Protocol + concrete adapters (Loki,
#              Elasticsearch, in-process buffer) + a mountable FastAPI
#              `APIRouter` that exposes `GET /workflows/{trace_id}` and
#              `GET /workflows`.
#
#              The synthesis algorithm itself lives in
#              `_observability/synthesis.py` (storage-agnostic pure
#              functions). This package wires it to real-world log
#              backends and exposes the HTTP surface.
#
#              Test-tier `_observability` re-uses this same router with
#              `BufferSpanSource`. Production K8s deployments use
#              `LokiSpanSource` or `ElasticsearchSpanSource`; the choice
#              is driven by `OBS_SPAN_SOURCE`.
#
# @relation implements:R-100-124
# =============================================================================

from ay_platform_core.observability.workflow.config import WorkflowSourceSettings
from ay_platform_core.observability.workflow.router import make_workflow_router
from ay_platform_core.observability.workflow.sources import (
    BufferSpanSource,
    ElasticsearchSpanSource,
    LokiSpanSource,
    SpanSource,
    create_span_source,
)

__all__ = [
    "BufferSpanSource",
    "ElasticsearchSpanSource",
    "LokiSpanSource",
    "SpanSource",
    "WorkflowSourceSettings",
    "create_span_source",
    "make_workflow_router",
]
