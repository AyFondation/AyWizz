# =============================================================================
# File: conftest.py
# Version: 2
# Path: ay_platform_core/tests/system/conftest.py
# Description: Fixtures for the `system` test tier. Assumes the platform
#              docker-compose stack is ALREADY running (use
#              `ay_platform_core/scripts/e2e_stack.sh up && … seed` to
#              bring it up). All tests hit Traefik on the host port
#              published per R-100-122 — default `http://localhost:56000`
#              (override via STACK_BASE_URL env var).
# =============================================================================

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio


def _base_url() -> str:
    return os.environ.get("STACK_BASE_URL", "http://localhost:56000").rstrip("/")


def _mock_llm_admin_url() -> str | None:
    """Mock-LLM admin URL for per-test queue manipulation.

    The admin endpoint is NOT part of the Traefik public surface — it is
    exclusively test infrastructure. `tests/docker-compose.yml` exposes
    the mock service on host port `${PORT_MOCK_LLM}` (default 59800,
    R-100-122) so system tests running outside the compose network can
    still script LLM responses. Override via ``MOCK_LLM_ADMIN_URL`` when
    running pytest inside the network (in which case you'd use
    http://mock_llm:8000).
    """
    return os.environ.get("MOCK_LLM_ADMIN_URL", "http://localhost:59800")


@pytest_asyncio.fixture(scope="session")
async def stack_ready() -> None:
    """Block until the gateway is responsive. Fails fast if the stack is down.

    Tests that depend on this fixture cannot run unless the compose stack
    is up. No auto-start: the suite deliberately separates orchestration
    (docker compose up) from testing (pytest) so a broken test run does
    not silently trigger an expensive stack rebuild.
    """
    base = _base_url()
    deadline = time.monotonic() + 30.0
    last_err: str | None = None
    async with httpx.AsyncClient(timeout=3.0) as client:
        while time.monotonic() < deadline:
            try:
                resp = await client.get(f"{base}/auth/config")
                if resp.status_code == 200:
                    return
                last_err = f"/auth/config -> {resp.status_code}"
            except httpx.RequestError as exc:
                last_err = f"{type(exc).__name__}: {exc}"
            time.sleep(1.0)
    pytest.fail(
        f"Platform stack not reachable at {base}. Bring it up first with "
        f"`ay_platform_core/scripts/e2e_stack.sh up`. Last error: {last_err}"
    )


@pytest_asyncio.fixture(scope="function")
async def gateway_client(
    stack_ready: None,
) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=_base_url(), timeout=10.0) as client:
        yield client


@pytest_asyncio.fixture(scope="session")
async def admin_token(stack_ready: None) -> str:
    """Obtain a JWT for the bootstrap admin (alice / seed-password).

    **Session-scoped** so the suite issues a single `/auth/login` for
    the whole run. C1 Traefik rate-limits `/auth/login` to 10 RPM per
    source IP (R-100-039); function-scoped login from N tests blows
    that budget and the rest of the suite gets 429s. Token TTL is
    `C2_TOKEN_TTL_SECONDS` (default 3600s in `.env.test`), comfortably
    longer than any local run.

    The compose stack runs C2 in `local` auth mode. The C2 lifespan
    bootstraps the admin user from `C2_LOCAL_ADMIN_USERNAME` /
    `C2_LOCAL_ADMIN_PASSWORD` (defaults `alice` / `seed-password` in
    `.env.test`); login with those credentials issues a JWT with
    global role `admin`, which clears the project-scoped permission
    checks the gateway applies downstream.
    """
    async with httpx.AsyncClient(base_url=_base_url(), timeout=10.0) as client:
        resp = await client.post(
            "/auth/login",
            json={"username": "alice", "password": "seed-password"},
        )
        if resp.status_code != 200:
            pytest.fail(f"/auth/login failed: {resp.status_code} {resp.text}")
        return str(resp.json()["access_token"])


@pytest_asyncio.fixture(scope="function")
async def auth_headers(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest_asyncio.fixture(scope="function")
async def mock_llm_admin() -> AsyncIterator[httpx.AsyncClient]:
    """httpx client pointed at the mock-LLM admin endpoint.

    Resets the mock at fixture entry so each test starts with an empty
    queue + call log — tests SHALL NOT depend on state from earlier
    fixtures or the seeder.
    """
    url = _mock_llm_admin_url()
    assert url is not None  # conftest sets a default
    async with httpx.AsyncClient(base_url=url, timeout=5.0) as client:
        try:
            await client.post("/admin/reset")
        except httpx.RequestError as exc:
            pytest.fail(
                f"mock LLM unreachable at {url}. Is the stack up? "
                f"(last error: {type(exc).__name__}: {exc})"
            )
        yield client
