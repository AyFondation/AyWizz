# =============================================================================
# File: test_project_consumption_e2e.py
# Version: 1
# Path: ay_platform_core/tests/integration/c8_admin/test_project_consumption_e2e.py
# Description: Endpoint-level tests for GET /admin/v1/quota/consumption/projects
#              (E-100-002 v7 project cost dashboards). Mounts the real quota
#              router + a real QuotaService over an in-memory store (no
#              testcontainer). Validates the OPERATOR gate + tenant scoping:
#              platform_manager cross-tenant (optional ?tenant_id filter);
#              admin/tenant_admin forced to its X-Tenant-Id; baseline user 403;
#              a tenant operator without X-Tenant-Id 401.
# @relation validates:R-800-145
# @relation validates:R-800-146
# =============================================================================

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from ay_platform_core.c8_llm.quota.router import router
from ay_platform_core.c8_llm.quota.service import QuotaService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class _Store:
    """Minimal in-memory QuotaStore. Returns one project row when the scope is
    the whole platform (None) or tenant-a; empty for any other tenant."""

    async def get_policy(self) -> dict[str, Any] | None:
        return None

    async def set_policy(self, doc: dict[str, Any]) -> None: ...

    async def get_anchor(self, key: str) -> str | None:
        return None

    async def set_anchor(self, key: str, iso: str) -> None: ...

    async def usage_in_window(
        self, since_iso: str, **kw: object
    ) -> tuple[float, int, str | None]:
        return (0.0, 0, None)

    async def consumption_by_tenant(
        self, since_iso: str
    ) -> list[tuple[str, float, int]]:
        return [("tenant-a", 5.0, 500)]

    async def consumption_by_project(
        self, since_iso: str, tenant_id: str | None = None
    ) -> list[tuple[str, float, int]]:
        if tenant_id in (None, "tenant-a"):
            return [("proj-1", 1.0, 100)]
        return []

    async def consumption_by_user(
        self, since_iso: str, tenant_id: str | None = None
    ) -> list[tuple[str, float, int]]:
        if tenant_id in (None, "tenant-a"):
            return [("user-1", 2.0, 200)]
        return []

    async def breakdown_by_model(
        self, field: str, value: str, tenant_id: str | None = None
    ) -> list[tuple[str, int, int, float]]:
        # value "req-1" → opus 2.5M + haiku 7.5M tokens (25% / 75%).
        if value == "req-1" and tenant_id in (None, "tenant-a"):
            return [("opus", 2_000_000, 500_000, 6.0), ("haiku", 6_000_000, 1_500_000, 2.0)]
        return []


def _app() -> FastAPI:
    app = FastAPI()
    app.state.quota_service = QuotaService(_Store(), currency="EUR")
    app.include_router(router)
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://c8"
    )


_URL = "/admin/v1/quota/consumption/projects"


async def test_admin_gets_own_tenant_scoped_report() -> None:
    async with _client(_app()) as c:
        r = await c.get(
            _URL,
            headers={"X-User-Id": "a", "X-User-Roles": "admin", "X-Tenant-Id": "tenant-a"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tenant_id"] == "tenant-a"
    assert body["windows"] == ["day", "week", "month", "quarter", "semester", "year"]
    assert [p["project_id"] for p in body["projects"]] == ["proj-1"]


async def test_platform_manager_sees_all_tenants() -> None:
    async with _client(_app()) as c:
        r = await c.get(
            _URL, headers={"X-User-Id": "p", "X-User-Roles": "platform_manager"}
        )
    assert r.status_code == 200, r.text
    assert r.json()["tenant_id"] is None
    assert [p["project_id"] for p in r.json()["projects"]] == ["proj-1"]


async def test_baseline_user_is_forbidden() -> None:
    async with _client(_app()) as c:
        r = await c.get(
            _URL,
            headers={"X-User-Id": "u", "X-User-Roles": "user", "X-Tenant-Id": "tenant-a"},
        )
    assert r.status_code == 403


async def test_tenant_operator_without_tenant_header_401() -> None:
    async with _client(_app()) as c:
        r = await c.get(_URL, headers={"X-User-Id": "a", "X-User-Roles": "admin"})
    assert r.status_code == 401


async def test_tenant_cost_report_platform_manager_only() -> None:
    async with _client(_app()) as c:
        ok = await c.get(
            "/admin/v1/quota/consumption/tenants",
            headers={"X-User-Id": "p", "X-User-Roles": "platform_manager"},
        )
        denied = await c.get(
            "/admin/v1/quota/consumption/tenants",
            headers={"X-User-Id": "a", "X-User-Roles": "admin", "X-Tenant-Id": "tenant-a"},
        )
    assert ok.status_code == 200, ok.text
    assert ok.json()["windows"] == ["day", "week", "month", "quarter", "semester", "year"]
    assert [t["tenant_id"] for t in ok.json()["tenants"]] == ["tenant-a"]
    assert denied.status_code == 403


async def test_request_breakdown_model_mix() -> None:
    async with _client(_app()) as c:
        r = await c.get(
            "/admin/v1/quota/requests/req-1/breakdown?by=run",
            headers={"X-User-Id": "p", "X-User-Roles": "platform_manager"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_tokens"] == 10_000_000
    pct = {m["model"]: m["tokens_pct"] for m in body["models"]}
    assert pct == {"opus": 25.0, "haiku": 75.0}


async def test_request_breakdown_rejects_bad_by() -> None:
    async with _client(_app()) as c:
        r = await c.get(
            "/admin/v1/quota/requests/req-1/breakdown?by=bogus",
            headers={"X-User-Id": "p", "X-User-Roles": "platform_manager"},
        )
    assert r.status_code == 400


async def test_user_cost_report_scoped_for_admin() -> None:
    async with _client(_app()) as c:
        r = await c.get(
            "/admin/v1/quota/consumption/users",
            headers={"X-User-Id": "a", "X-User-Roles": "admin", "X-Tenant-Id": "tenant-a"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tenant_id"] == "tenant-a"
    assert [u["user_id"] for u in body["users"]] == ["user-1"]
    assert body["users"][0]["windows"]["day"]["cost"] == 2.0
