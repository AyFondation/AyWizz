# =============================================================================
# File: test_enrichment_config_flow.py
# Version: 1
# Path: ay_platform_core/tests/integration/c7_memory/test_enrichment_config_flow.py
# Description: Integration tests for the per-project enrichment config endpoints
#              (R-400-224) over the real C7 app + ArangoDB: GET returns the
#              default when unset, PUT (owner) persists, GET reflects it, and
#              the PUT role gate rejects an insufficient role.
#
# @relation validates:R-400-070
# =============================================================================

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]

_TENANT = "tenant-cfg"
_PROJECT = "project-cfg"
_READ = {"X-User-Id": "alice", "X-Tenant-Id": _TENANT, "X-User-Roles": "project_editor"}
_OWNER = {"X-User-Id": "olive", "X-Tenant-Id": _TENANT, "X-User-Roles": "project_owner"}


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    )


async def test_enrichment_config_put_then_get(c7_upload_app: FastAPI) -> None:
    async with _client(c7_upload_app) as c:
        # Default (unset) → minimal tier, no overrides.
        r0 = await c.get(
            "/api/v1/memory/projects/project-cfg/enrichment-config", headers=_READ
        )
        assert r0.status_code == 200, r0.text
        assert r0.json()["quality_tier"] == "minimal"

        # Owner persists a config (independent image model included).
        body = {
            "quality_tier": "high",
            "image_vision_enabled": True,
            "image_analyzer_model": "ollama:llava",
        }
        r1 = await c.put(
            "/api/v1/memory/projects/project-cfg/enrichment-config",
            headers=_OWNER,
            json=body,
        )
        assert r1.status_code == 200, r1.text
        assert r1.json()["quality_tier"] == "high"

        # GET reflects the persisted config.
        r2 = await c.get(
            "/api/v1/memory/projects/project-cfg/enrichment-config", headers=_READ
        )
        assert r2.status_code == 200, r2.text
        body2 = r2.json()
        assert body2["image_vision_enabled"] is True
        assert body2["image_analyzer_model"] == "ollama:llava"


async def test_enrichment_config_put_rejects_insufficient_role(
    c7_upload_app: FastAPI,
) -> None:
    async with _client(c7_upload_app) as c:
        # project_editor cannot change ingestion behaviour (owner/admin only).
        resp = await c.put(
            "/api/v1/memory/projects/project-cfg/enrichment-config",
            headers=_READ,
            json={"quality_tier": "high"},
        )
        assert resp.status_code == 403
