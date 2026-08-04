# =============================================================================
# File: metering.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/storage/metering.py
# Description: Per-project disk-storage measurement over MinIO. Projects are
#              DISCOVERED from the artifact key layout
#              (`c4-artifacts/{tenant}/{project}/…`) — no dependency on the C2
#              project registry — and measured by summing object sizes under
#              each project prefix. Pure of HTTP; the client is a thin seam so
#              tests can inject a fake object store.
# =============================================================================

from __future__ import annotations

import asyncio
from typing import Any, Protocol

# The artifact key root, mirroring c4_orchestrator.artifacts_storage.PREFIX_ROOT.
PREFIX_ROOT = "c4-artifacts"


class ObjectStoreClient(Protocol):
    """Minimal object-store seam (satisfied by minio.Minio). `list_objects`
    yields entries exposing `.object_name` and `.size`."""

    def list_objects(
        self, bucket_name: str, prefix: str = "", recursive: bool = False
    ) -> Any: ...


class StorageMeter:
    """Measures per-project disk occupation over one bucket."""

    def __init__(self, client: ObjectStoreClient, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def _discover_projects_sync(self) -> list[tuple[str, str]]:
        """Every (tenant_id, project_id) present under the artifact root,
        discovered from the key layout (two levels of pseudo-directories)."""
        out: list[tuple[str, str]] = []
        for tenant_prefix in self._child_dirs(f"{PREFIX_ROOT}/"):
            # tenant_prefix = "c4-artifacts/{tenant}/"
            parts = tenant_prefix.strip("/").split("/")
            if len(parts) != 2:
                continue
            tenant_id = parts[1]
            for project_prefix in self._child_dirs(tenant_prefix):
                pparts = project_prefix.strip("/").split("/")
                if len(pparts) != 3:
                    continue
                out.append((tenant_id, pparts[2]))
        return out

    def _child_dirs(self, prefix: str) -> list[str]:
        """Immediate pseudo-directory children of `prefix` (non-recursive)."""
        dirs: list[str] = []
        for obj in self._client.list_objects(
            self._bucket, prefix=prefix, recursive=False
        ):
            name = getattr(obj, "object_name", "") or ""
            if name.endswith("/") and name != prefix:
                dirs.append(name)
        return dirs

    def _measure_project_sync(self, tenant_id: str, project_id: str) -> int:
        prefix = f"{PREFIX_ROOT}/{tenant_id}/{project_id}/"
        total = 0
        for obj in self._client.list_objects(
            self._bucket, prefix=prefix, recursive=True
        ):
            if getattr(obj, "is_dir", False):
                continue
            total += int(getattr(obj, "size", 0) or 0)
        return total

    async def discover_projects(self) -> list[tuple[str, str]]:
        return await asyncio.to_thread(self._discover_projects_sync)

    async def measure_project(self, tenant_id: str, project_id: str) -> int:
        """Sum of object sizes under the project's artifact prefix, in bytes."""
        return await asyncio.to_thread(
            self._measure_project_sync, tenant_id, project_id
        )
