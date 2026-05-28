# =============================================================================
# File: dispatch_storage.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/dispatch_storage.py
# Description: MinIO surface for sub-agent dispatch bundles (R-200-033).
#              Distinct from ArtifactStorage : artifacts live under
#              `orchestrator/c4-artifacts/{tenant}/{project}/{run}/{path}`
#              (project-scoped, tenant-guarded), bundles live under
#              `orchestrator/c4-dispatch/{run_id}/{sub_agent_id}/...`
#              (orchestrator-internal, ephemeral). Mixing the two
#              namespaces would muddle the access-policy story —
#              keeping them separate keeps R-200-031's "egress-policy
#              to C8 + C10 only" coherent (the sub-agent pod's mount
#              path is the dispatch prefix, never the artifacts one).
#
#              Surface is intentionally narrow :
#                - put_manifest(run_id, sub_agent_id, envelope) — writes
#                  manifest.json AT the bundle root.
#                - put_context_entry(...) — writes one file under
#                  `<prefix>/context/<rel_path>`.
#                - get_completion_report(...) — reads back the report the
#                  sub-agent wrote at `<prefix>/output/completion.json`.
#                - delete_bundle(...) — purges the prefix after the
#                  orchestrator has consumed the result (the K8sDispatcher
#                  calls this in its `finally` block).
#
# @relation implements:R-200-033
# =============================================================================

from __future__ import annotations

import asyncio
import io
import json
import logging
from typing import Any

from ay_platform_core._sub_agent.models import (
    ContextBundleEntry,
    SubAgentRunReport,
    TaskEnvelope,
)

_log = logging.getLogger("c4_orchestrator.dispatch_storage")

_BUNDLE_NAMESPACE = "c4-dispatch"


def _bundle_prefix(run_id: str, sub_agent_id: str) -> str:
    """Compute the trailing-slashed prefix used as the MinIO key root
    for one sub-agent bundle. Identifiers are constrained to the same
    POSIX-safe shape ArtifactStorage uses (per R-200-130) — no leading
    `/`, no `..`, no `\\` ; the orchestrator generates them so we
    don't validate again here."""
    return f"{_BUNDLE_NAMESPACE}/{run_id}/{sub_agent_id}/"


class DispatchStorage:
    """MinIO operations scoped to the sub-agent dispatch namespace.

    Holds the raw MinIO client + bucket — does NOT go through
    ArtifactStorage so the prefix shape stays independent of the
    artifact-runs key scheme."""

    def __init__(self, minio_client: Any, bucket: str) -> None:
        self._client = minio_client
        self._bucket = bucket

    # ------------------------------------------------------------------
    # Sync primitives — wrapped in `asyncio.to_thread` below.
    # ------------------------------------------------------------------

    def _put_blob_sync(
        self, key: str, data: bytes, content_type: str,
    ) -> None:
        self._client.put_object(
            self._bucket,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )

    def _get_blob_text_sync(self, key: str) -> str | None:
        try:
            response = self._client.get_object(self._bucket, key)
        except Exception:  # MinIO raises S3Error on NoSuchKey ; treat as None
            return None
        try:
            raw: bytes = response.read()
            return raw.decode("utf-8")
        finally:
            response.close()
            response.release_conn()

    def _delete_prefix_sync(self, prefix: str) -> None:
        # `list_objects(recursive=True)` then `remove_object` per key.
        # MinIO has a batch API but it requires a `DeleteObject` list ;
        # the per-key call is simpler and bundles hold O(10) objects.
        objects = self._client.list_objects(
            self._bucket, prefix=prefix, recursive=True,
        )
        for obj in objects:
            name = obj.object_name
            if not isinstance(name, str):
                continue
            try:
                self._client.remove_object(self._bucket, name)
            except Exception as exc:  # best-effort cleanup
                _log.warning("dispatch_storage delete %s failed: %s", name, exc)

    # ------------------------------------------------------------------
    # Public async surface
    # ------------------------------------------------------------------

    async def put_manifest(self, envelope: TaskEnvelope) -> str:
        """Write `manifest.json` at the bundle root. Returns the
        full MinIO key prefix (no leading `/`, trailing `/`) so the
        K8sDispatcher can pass it to the pod via env var."""
        prefix = _bundle_prefix(envelope.run_id, envelope.sub_agent_id)
        key = f"{prefix}manifest.json"
        body = envelope.model_dump_json().encode("utf-8")
        await asyncio.to_thread(
            self._put_blob_sync, key, body, "application/json",
        )
        return prefix

    async def put_context_entry(
        self,
        *,
        run_id: str,
        sub_agent_id: str,
        entry: ContextBundleEntry,
        data: bytes,
    ) -> None:
        """Persist one context file under `<prefix>context/<rel_path>`."""
        prefix = _bundle_prefix(run_id, sub_agent_id)
        key = f"{prefix}context/{entry.relative_path}"
        await asyncio.to_thread(
            self._put_blob_sync, key, data, entry.content_type,
        )

    async def get_completion_report(
        self, *, run_id: str, sub_agent_id: str,
    ) -> SubAgentRunReport | None:
        """Read `output/completion.json` written by the sub-agent.
        Returns None when the file is absent (pod hasn't completed yet
        OR crashed before writing it — caller distinguishes via pod
        status)."""
        prefix = _bundle_prefix(run_id, sub_agent_id)
        key = f"{prefix}output/completion.json"
        raw = await asyncio.to_thread(self._get_blob_text_sync, key)
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            _log.warning(
                "dispatch_storage : completion.json malformed at %s : %s",
                key, exc,
            )
            return None
        return SubAgentRunReport.model_validate(payload)

    async def delete_bundle(
        self, *, run_id: str, sub_agent_id: str,
    ) -> None:
        """Purge every object under the bundle prefix. Best-effort —
        a failing delete is logged but doesn't raise (the bundle is
        orchestrator-internal ephemeral state, NOT audit-grade ; a
        stale prefix is harmless until the next retention sweep)."""
        prefix = _bundle_prefix(run_id, sub_agent_id)
        await asyncio.to_thread(self._delete_prefix_sync, prefix)
