# =============================================================================
# File: test_ux_config.py
# Version: 2
# Path: ay_platform_core/tests/integration/c2_auth/test_ux_config.py
# Description: Pin the public `GET /ux/config` endpoint contract used
#              by the Next.js frontend bootstrap. Verifies:
#                1. Response shape matches `UXConfigResponse` schema.
#                2. Defaults — brand "AyWizz Platform", chat/kg/file
#                   features enabled, cross-tenant disabled.
#                3. Env-var override — `C2_UX_BRAND_NAME=...` flows
#                   through to the response without rebuilding.
#                4. Endpoint is OPEN — no `X-User-Id` required.
#                5. HEAD method works (probes / cache validation).
#                6. (v2) `dev_credentials` is None outside dev mode.
#                7. (v2) `dev_credentials` populated when both
#                   `auth_mode=local` AND `ux_dev_mode_enabled=True`,
#                   matching the `demo_seed_*` envelope (4 entries).
#
# @relation validates:R-100-114
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Iterator

import httpx
import pytest
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c2_auth.config import AuthConfig
from ay_platform_core.c2_auth.db.repository import AuthRepository
from ay_platform_core.c2_auth.service import AuthService, get_service
from ay_platform_core.c2_auth.ux_router import ux_router
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]

_JWT_SECRET = "ux-config-test-secret-32chars!!!"


@pytest.fixture(scope="function")
def ux_repo(arango_container: ArangoEndpoint) -> Iterator[AuthRepository]:
    db_name = f"c2_ux_test_{uuid.uuid4().hex[:8]}"
    sys_db = ArangoClient(hosts=arango_container.url).db(
        "_system",
        username=arango_container.username,
        password=arango_container.password,
    )
    sys_db.create_database(db_name)
    repo = AuthRepository.from_config(
        arango_container.url, db_name,
        arango_container.username, arango_container.password,
    )
    repo._ensure_collections_sync()
    try:
        yield repo
    finally:
        cleanup_arango_database(arango_container, db_name)


def _make_app(cfg: AuthConfig, repo: AuthRepository) -> FastAPI:
    """C2-style mini app with ONLY the ux_router mounted — keeps the
    test focused on the public bootstrap endpoint."""
    app = FastAPI()
    service = AuthService(cfg, repo)
    app.include_router(ux_router, prefix="/ux")
    app.dependency_overrides[get_service] = lambda: service
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    )


async def test_ux_config_returns_default_shape(
    ux_repo: AuthRepository,
) -> None:
    cfg = AuthConfig(jwt_secret_key=_JWT_SECRET)
    app = _make_app(cfg, ux_repo)
    async with _client(app) as c:
        resp = await c.get("/ux/config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Top-level keys
    assert body["api_version"] == "v1"
    assert body["auth_mode"] in ("none", "local", "sso")
    # Brand defaults
    assert body["brand"]["name"] == "AyWizz Platform"
    assert body["brand"]["short_name"] == "AyWizz"
    assert body["brand"]["accent_color_hex"] == "#3b82f6"
    # Feature defaults
    assert body["features"]["chat_enabled"] is True
    assert body["features"]["kg_enabled"] is True
    assert body["features"]["cross_tenant_enabled"] is False
    assert body["features"]["file_download_enabled"] is True


async def test_ux_config_env_overrides_flow_through(
    ux_repo: AuthRepository,
) -> None:
    """Pin the "no rebuild needed" promise — flipping `C2_UX_*` fields
    on AuthConfig SHALL change the response without code change."""
    cfg = AuthConfig(
        jwt_secret_key=_JWT_SECRET,
        ux_brand_name="ACME Internal Wiki",
        ux_brand_short_name="ACME",
        ux_brand_accent_color="#ff0066",
        ux_feature_kg_enabled=False,
        ux_feature_cross_tenant_enabled=True,
    )
    app = _make_app(cfg, ux_repo)
    async with _client(app) as c:
        resp = await c.get("/ux/config")
    body = resp.json()
    assert body["brand"]["name"] == "ACME Internal Wiki"
    assert body["brand"]["short_name"] == "ACME"
    assert body["brand"]["accent_color_hex"] == "#ff0066"
    assert body["features"]["kg_enabled"] is False
    assert body["features"]["cross_tenant_enabled"] is True


async def test_ux_config_is_public_no_user_id_required(
    ux_repo: AuthRepository,
) -> None:
    """Frontend hits `/ux/config` BEFORE login — the endpoint SHALL
    return 200 without any `X-User-Id` header. Pinned here separately
    because the auth-matrix `Auth.OPEN` flag covers anonymous access
    only at the path-level — this test confirms zero-header behaviour
    end-to-end."""
    cfg = AuthConfig(jwt_secret_key=_JWT_SECRET)
    app = _make_app(cfg, ux_repo)
    async with _client(app) as c:
        # No X-User-Id, no Authorization, no nothing.
        resp = await c.get("/ux/config")
    assert resp.status_code == 200


async def test_ux_config_supports_head_method(
    ux_repo: AuthRepository,
) -> None:
    """HEAD is supported for cache validators / liveness probes (same
    pattern as `/auth/config`)."""
    cfg = AuthConfig(jwt_secret_key=_JWT_SECRET)
    app = _make_app(cfg, ux_repo)
    async with _client(app) as c:
        resp = await c.head("/ux/config")
    # FastAPI strips the body on HEAD ; the status code SHALL be 200.
    assert resp.status_code == 200
    assert resp.content == b""


async def test_ux_config_dev_credentials_omitted_by_default(
    ux_repo: AuthRepository,
) -> None:
    """Defense-in-depth : `dev_credentials` SHALL be None / omitted
    when `ux_dev_mode_enabled` is False, even if `auth_mode=local`.
    Production overlays leave the flag False and rely on this for
    secret-leak avoidance."""
    cfg = AuthConfig(
        jwt_secret_key=_JWT_SECRET,
        auth_mode="local",
        # ux_dev_mode_enabled defaults to False
    )
    app = _make_app(cfg, ux_repo)
    async with _client(app) as c:
        resp = await c.get("/ux/config")
    body = resp.json()
    # Pydantic v2 with default=None serialises as null OR omits the
    # field entirely depending on the response model config — we
    # accept both as "absent".
    assert body.get("dev_credentials") in (None, [])


async def test_ux_config_dev_credentials_populated_when_dev_mode_on(
    ux_repo: AuthRepository,
) -> None:
    """When both `auth_mode=local` AND `ux_dev_mode_enabled=True`,
    `dev_credentials` SHALL list the 4 demo accounts with their
    plaintext passwords (intentional, for auto-fill)."""
    cfg = AuthConfig(
        jwt_secret_key=_JWT_SECRET,
        auth_mode="local",
        ux_dev_mode_enabled=True,
        # demo_seed_enabled is independent — dev_credentials are read
        # from config, not from the DB. Useful for testing the UX
        # surface without spinning up a seed.
    )
    app = _make_app(cfg, ux_repo)
    async with _client(app) as c:
        resp = await c.get("/ux/config")
    body = resp.json()
    creds = body.get("dev_credentials")
    assert isinstance(creds, list)
    assert len(creds) == 4

    usernames = {entry["username"] for entry in creds}
    assert usernames == {
        "superroot",
        "tenant-admin",
        "project-editor",
        "project-viewer",
    }
    # Plaintext password present for auto-fill.
    by_user = {entry["username"]: entry for entry in creds}
    assert by_user["superroot"]["password"] == "dev-superroot"
    assert by_user["tenant-admin"]["password"] == "dev-tenant"
    assert by_user["project-editor"]["password"] == "dev-editor"
    assert by_user["project-viewer"]["password"] == "dev-viewer"

    # Role label + note shape preserved (the UX surfaces both).
    for entry in creds:
        assert "role_label" in entry
        assert "note" in entry


async def test_ux_config_dev_credentials_skipped_when_auth_mode_not_local(
    ux_repo: AuthRepository,
) -> None:
    """Even with `ux_dev_mode_enabled=True`, the credentials are
    pointless under `auth_mode=none` (every request is anonymous-OK)
    or `sso` (auth handled externally) — SHALL be omitted."""
    cfg = AuthConfig(
        jwt_secret_key=_JWT_SECRET,
        auth_mode="none",
        ux_dev_mode_enabled=True,
    )
    app = _make_app(cfg, ux_repo)
    async with _client(app) as c:
        resp = await c.get("/ux/config")
    body = resp.json()
    assert body.get("dev_credentials") in (None, [])
