# =============================================================================
# File: bundle_builder.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/bundle_builder.py
# Description: Helper that translates a `DispatchRequest` + a set of
#              `(rel_path, bytes)` context files into a written-to-MinIO
#              sub-agent bundle. Bridge between the orchestrator (which
#              knows the run state) and DispatchStorage (which knows the
#              MinIO surface). Stateless ; one call = one bundle.
#
#              Returns the bundle prefix (e.g. `c4-dispatch/<run>/<sub>/`)
#              the K8sDispatcher passes to the pod as
#              `SUB_AGENT_BUNDLE_PREFIX`.
#
# @relation implements:R-200-033
# =============================================================================

from __future__ import annotations

import uuid
from typing import Any

from ay_platform_core._sub_agent.models import (
    ContextBundleEntry,
    TaskEnvelope,
)
from ay_platform_core.c4_orchestrator.dispatch_storage import DispatchStorage
from ay_platform_core.c4_orchestrator.dispatcher.base import DispatchRequest


async def build_sub_agent_bundle(
    storage: DispatchStorage,
    request: DispatchRequest,
    *,
    sub_agent_id: str | None = None,
    context_files: dict[str, bytes] | None = None,
    content_types: dict[str, str] | None = None,
    purposes: dict[str, str] | None = None,
) -> tuple[str, TaskEnvelope]:
    """Materialise the bundle for one sub-agent invocation.

    Returns `(bundle_prefix, envelope)` ; the caller (K8sDispatcher
    in P2.1.c) uses the prefix to populate the pod's env and the
    envelope to mirror identity for observability/audit.

    `sub_agent_id` defaults to a fresh UUID4 hex slice — the dispatcher
    generates it per dispatch so concurrent sub-agents on the same run
    don't collide.

    `context_files` maps `relative_path → bytes`. Sub-agents
    interpret these as text (UTF-8) so the orchestrator SHALL encode
    text inputs accordingly ; binary support is a v2 concern.
    """
    files = context_files or {}
    cts = content_types or {}
    purpose_map = purposes or {}

    entries = [
        ContextBundleEntry(
            relative_path=rel,
            purpose=purpose_map.get(rel, ""),
            content_type=cts.get(rel, "text/plain"),
        )
        for rel in sorted(files.keys())
    ]
    envelope = TaskEnvelope(
        run_id=request.run_id,
        sub_agent_id=sub_agent_id or uuid.uuid4().hex[:16],
        project_id=request.project_id,
        tenant_id=request.tenant_id,
        session_id=request.session_id,
        user_id=request.user_id,
        phase=request.phase,
        agent=request.agent,
        user_prompt=request.prompt,
        context_bundle=_clean_context_bundle(request.context_bundle),
        context_entries=entries,
    )
    prefix = await storage.put_manifest(envelope)
    for entry in entries:
        await storage.put_context_entry(
            run_id=envelope.run_id,
            sub_agent_id=envelope.sub_agent_id,
            entry=entry,
            data=files[entry.relative_path],
        )
    return prefix, envelope


def _clean_context_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Strip non-JSON-serialisable values so `model_dump_json()` of the
    envelope doesn't blow up later. Pydantic's `mode="json"` covers
    most cases, but a defensive pass here makes the bundle-builder
    independently safe (the orchestrator can pass datetime/UUID/etc.
    without surprise)."""
    cleaned: dict[str, Any] = {}
    for k, v in bundle.items():
        try:
            import json  # noqa: PLC0415 — cold path

            json.dumps(v)
            cleaned[k] = v
        except (TypeError, ValueError):
            cleaned[k] = str(v)
    return cleaned
