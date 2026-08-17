# =============================================================================
# File: test_storage_router_e2e.py
# Version: 1
# Path: ay_platform_core/tests/integration/c8_admin/test_storage_router_e2e.py
# Description: Endpoint tests for the per-project storage dashboards
#              (E-100-002 v7). Mounts the real storage router over a
#              StorageService backed by a fake object store + snapshot store (no
#              testcontainer). Validates the operator gate + tenant scoping, the
#              platform_manager-only snapshot trigger, and the 503 when metering
#              is unconfigured (storage_service = None).
# @relation validates:R-100-140
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from ay_platform_core.c8_llm.storage.metering import StorageMeter
from ay_platform_core.c8_llm.storage.router import router
from ay_platform_core.c8_llm.storage.service import StorageService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_NOW = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)


@dataclass
class _Obj:
    object_name: str
    size: int = 0
    is_dir: bool = False


class _FakeMinio:
    def __init__(self, blobs: dict[str, int]) -> None:
        self._blobs = blobs

    def list_objects(
        self, bucket_name: str, prefix: str = "", recursive: bool = False
    ) -> list[_Obj]:
        if recursive:
            return [_Obj(k, v) for k, v in self._blobs.items() if k.startswith(prefix)]
        dirs: set[str] = set()
        out: list[_Obj] = []
        for k in self._blobs:
            if not k.startswith(prefix):
                continue
            rest = k[len(prefix):]
            if "/" in rest:
                dirs.add(prefix + rest.split("/", 1)[0] + "/")
            else:
                out.append(_Obj(k, self._blobs[k]))
        out.extend(_Obj(d) for d in sorted(dirs))
        return out


class _FakeStore:
    def __init__(self) -> None:
        self.snapshots: list[dict[str, Any]] = []

    async def insert_snapshot(self, document: dict[str, Any]) -> None:
        self.snapshots.append(dict(document))

    async def series_since(
        self, project_id: str, since_iso: str
    ) -> list[tuple[str, int]]:
        return [
            (s["measured_at"], s["bytes"])
            for s in self.snapshots
            if s["project_id"] == project_id and s["measured_at"] >= since_iso
        ]


_BLOBS = {
    "c4-artifacts/tenant-a/p1/runs/r1/a.py": 300,
    "c4-artifacts/tenant-b/p9/runs/r0/b.py": 9,
}


def _app(configured: bool = True) -> FastAPI:
    app = FastAPI()
    if configured:
        svc = StorageService(
            StorageMeter(_FakeMinio(_BLOBS), "orchestrator"),
            _FakeStore(),
            clock=lambda: _NOW,
        )
        app.state.storage_service = svc
    else:
        app.state.storage_service = None
    app.include_router(router)
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://c8")


_URL = "/admin/v1/storage/projects"


async def test_admin_report_scoped_to_own_tenant() -> None:
    async with _client(_app()) as c:
        r = await c.get(
            _URL,
            headers={"X-User-Id": "a", "X-User-Roles": "admin", "X-Tenant-Id": "tenant-a"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tenant_id"] == "tenant-a"
    assert [(p["project_id"], p["bytes"]) for p in body["projects"]] == [("p1", 300)]


async def test_platform_manager_report_all_tenants() -> None:
    async with _client(_app()) as c:
        r = await c.get(_URL, headers={"X-User-Id": "p", "X-User-Roles": "platform_manager"})
    assert r.status_code == 200
    assert {p["project_id"] for p in r.json()["projects"]} == {"p1", "p9"}


async def test_report_forbidden_for_baseline_user() -> None:
    async with _client(_app()) as c:
        r = await c.get(
            _URL,
            headers={"X-User-Id": "u", "X-User-Roles": "user", "X-Tenant-Id": "tenant-a"},
        )
    assert r.status_code == 403


async def test_report_401_without_tenant_header_for_operator() -> None:
    async with _client(_app()) as c:
        r = await c.get(_URL, headers={"X-User-Id": "a", "X-User-Roles": "admin"})
    assert r.status_code == 401


async def test_tenant_storage_platform_manager_only() -> None:
    async with _client(_app()) as c:
        ok = await c.get(
            "/admin/v1/storage/tenants",
            headers={"X-User-Id": "p", "X-User-Roles": "platform_manager"},
        )
        denied = await c.get(
            "/admin/v1/storage/tenants",
            headers={"X-User-Id": "a", "X-User-Roles": "admin", "X-Tenant-Id": "tenant-a"},
        )
    assert ok.status_code == 200, ok.text
    # p1 (300) under tenant-a, p9 (9) under tenant-b → per-tenant sums.
    tenants = {t["tenant_id"]: t["bytes"] for t in ok.json()["tenants"]}
    assert tenants == {"tenant-a": 300, "tenant-b": 9}
    assert denied.status_code == 403


async def test_series_happy_path_returns_current_and_shape() -> None:
    async with _client(_app()) as c:
        r = await c.get(
            "/admin/v1/storage/projects/p1/series?tenant_id=tenant-a&window=year",
            headers={"X-User-Id": "p", "X-User-Roles": "platform_manager"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["project_id"] == "p1"
    assert body["window"] == "year"
    assert body["current_bytes"] == 300
    assert body["points"] == []


async def test_series_admin_cross_tenant_forbidden() -> None:
    async with _client(_app()) as c:
        r = await c.get(
            f"{_URL}/p9/series?tenant_id=tenant-b&window=month",
            headers={"X-User-Id": "a", "X-User-Roles": "admin", "X-Tenant-Id": "tenant-a"},
        )
    assert r.status_code == 403


async def test_snapshot_trigger_platform_manager_only() -> None:
    async with _client(_app()) as c:
        denied = await c.post(
            "/admin/v1/storage/snapshot",
            headers={"X-User-Id": "a", "X-User-Roles": "admin", "X-Tenant-Id": "tenant-a"},
        )
        ok = await c.post(
            "/admin/v1/storage/snapshot",
            headers={"X-User-Id": "p", "X-User-Roles": "platform_manager"},
        )
    assert denied.status_code == 403
    assert ok.status_code == 200 and ok.json()["snapshots_written"] == 2


async def test_503_when_metering_unconfigured() -> None:
    async with _client(_app(configured=False)) as c:
        r = await c.get(
            _URL,
            headers={"X-User-Id": "p", "X-User-Roles": "platform_manager"},
        )
    assert r.status_code == 503
