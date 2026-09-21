#!/usr/bin/env python3
# =============================================================================
# File: seed_e2e.py
# Version: 2
# Path: ay_platform_core/scripts/seed_e2e.py
# Description: Inject deterministic test data into a running docker-compose
#              stack. Calls exclusively through the Traefik public gateway
#              (http://localhost by default) to exercise the same ingress
#              path that system tests will use. No direct DB or MinIO writes.
#
#              What the seeder does:
#                1. Polls /auth/config until C2 answers (stack readiness gate).
#                2. Obtains a token for a canonical admin user via the
#                   `none`-mode /auth/login (works with AUTH_MODE=none).
#                3. Creates a `demo` project document + one approved entity
#                   in C5.
#                4. Creates an external source ingestion in C7 (small text).
#                5. Enqueues one scripted LLM response in the mock LLM so the
#                   first orchestrator run has something to consume.
#
#              Re-running the seeder is idempotent: existing records are
#              silently skipped (409/204 responses tolerated).
#
# Usage:
#   python -m ay_platform_core.scripts.seed_e2e [--base-url http://localhost]
#                                                [--timeout-s 60]
# =============================================================================

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://localhost:56000"  # R-100-122 PORT_C1_PUBLIC
# The mock-LLM admin endpoint is NOT routed through Traefik; it is reachable
# only from inside the compose network or when the mock service is
# temporarily exposed to the host for seeding.
DEFAULT_MOCK_LLM_URL: str | None = None

ADMIN_USER = "alice"
ADMIN_ROLES = "project_editor,project_owner,admin"
DEMO_PROJECT = "demo"
DEMO_SLUG = "900-SPEC-DEMO"

_DEMO_DOC = """---
document: 900-SPEC-DEMO
version: 1
path: projects/demo/requirements/900-SPEC-DEMO.md
language: en
status: draft
---

# Demo spec for the system test suite

#### R-900-001

```yaml
id: R-900-001
version: 1
status: approved
category: functional
```

The platform SHALL serve seeded entities via C5 routed through Traefik.
"""


class SeedError(RuntimeError):
    """Raised when the seeder cannot complete because the stack is unreachable or
    inconsistent."""


def _headers(*, include_auth_mode_token: bool = False) -> dict[str, str]:
    """Forward-auth headers the seeder impersonates.

    In AUTH_MODE=none, Traefik's forward-auth middleware produces the three
    headers from C2's /auth/verify and injects them into the downstream
    request. In that mode, the seeder can simply set them here if we bypass
    forward-auth — but we don't: all seeder traffic goes through Traefik,
    so forward-auth will overwrite them. We therefore rely on C2's
    none-mode behaviour (returns `john.doe`-like user) AND set X-User-Id
    here only as a hint for the few endpoints that bypass forward-auth
    (e.g. /auth/config).
    """
    _ = include_auth_mode_token  # unused but kept for future symmetry
    return {
        "X-User-Id": ADMIN_USER,
        "X-User-Roles": ADMIN_ROLES,
        "X-Tenant-Id": "t-demo",
    }


async def wait_stack_ready(
    base_url: str, *, timeout_s: float, poll_interval_s: float = 1.0
) -> None:
    """Block until the gateway + C2 respond, or raise."""
    deadline = time.monotonic() + timeout_s
    last_err: str | None = None
    async with httpx.AsyncClient(timeout=3.0) as client:
        while time.monotonic() < deadline:
            try:
                resp = await client.get(f"{base_url}/auth/config")
                if resp.status_code == 200:
                    return
                last_err = f"/auth/config -> HTTP {resp.status_code}"
            except httpx.RequestError as exc:
                last_err = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(poll_interval_s)
    raise SeedError(f"stack never became ready: {last_err!r}")


async def obtain_token(client: httpx.AsyncClient, base_url: str) -> str:
    """Login as the bootstrap admin and return the issued JWT.

    The compose stack runs C2 in `local` auth mode (set in `.env.test`
    via `C2_AUTH_MODE=local`). The C2 lifespan calls
    `_ensure_local_admin()` which creates the user identified by
    `C2_LOCAL_ADMIN_USERNAME` / `C2_LOCAL_ADMIN_PASSWORD` and grants
    the global `admin` role.

    The seeder logs in with the SAME username / password — both
    default to `alice` / `seed-password`, matching the env file.

    THE RESULTING JWT IS NOT ENOUGH TO WRITE CONTENT, and this docstring
    used to claim the opposite ("carries roles=[admin], which clears the
    project-scoped permission checks"). E-100-002 v7 made `admin`
    content-blind: the content components strip it before every gate, so a
    token carrying only global roles is refused by C5/C6/C7 however
    privileged it looks. `ensure_project_access` closes that gap by
    granting a `project_*` role and re-minting the token.
    """
    resp = await client.post(
        f"{base_url}/auth/login",
        json={"username": ADMIN_USER, "password": "seed-password"},
    )
    if resp.status_code != 200:
        raise SeedError(f"/auth/login failed: {resp.status_code} {resp.text}")
    token = resp.json().get("access_token")
    if not token:
        raise SeedError(f"no access_token in /auth/login response: {resp.text}")
    return str(token)


def _subject_of(token: str) -> str:
    """The `sub` claim of a JWT, read WITHOUT verifying the signature.

    The seeder is not authenticating anyone here — it already holds a token
    the platform issued — it only needs the user id that token belongs to,
    in order to grant that user a project role. Decoding the payload avoids
    hard-coding C2's internal `admin-<username>` id format, which is an
    implementation detail of `_ensure_local_admin` and not a contract.
    """
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)  # restore base64url padding
    return str(json.loads(base64.urlsafe_b64decode(payload))["sub"])


async def ensure_project_access(
    client: httpx.AsyncClient, base_url: str, token: str
) -> str:
    """Give the seed user a PROJECT role on the demo project, and return a
    fresh token that carries it.

    WHY THIS EXISTS (added 2026-09-21). The seeder used to go straight to
    C5 with the bootstrap admin's token, on the documented assumption that
    "the resulting JWT carries roles=[admin], which clears the
    project-scoped permission checks". That stopped being true at
    E-100-002 v7, which made `admin` CONTENT-BLIND: per
    100-SPEC-ARCHITECTURE.md §RBAC, an `admin` "SHALL NOT read or write ANY
    project CONTENT … an `admin` who needs content must hold a project
    role", and the content components enforce it by STRIPPING
    `admin`/`tenant_admin` before every content gate. The seeder was never
    updated, so it failed with
    `403 requires one of: project_editor, project_owner, admin` — a message
    that names `admin` while the code has just removed it.

    `admin` remains the right identity to DO this: access governance
    (granting a `project_*` role within its own tenant) is explicitly one of
    its powers. What it cannot do is skip the grant and write content
    directly.

    THE RE-LOGIN IS NOT OPTIONAL. Project roles travel in the JWT
    `project_scopes` claim, so a grant has no effect on a token minted
    before it.
    """
    auth = {"Authorization": f"Bearer {token}", **_headers()}

    # The project is a governance object owned by the tenant admin; 409 means
    # a previous seed run already created it.
    create = await client.post(
        f"{base_url}/api/v1/projects",
        json={"project_id": DEMO_PROJECT, "name": "Seed demo project"},
        headers=auth,
    )
    if create.status_code not in (201, 409):
        raise SeedError(
            f"create project failed: {create.status_code} {create.text}"
        )

    user_id = _subject_of(token)
    grant = await client.post(
        f"{base_url}/api/v1/projects/{DEMO_PROJECT}/members/{user_id}",
        json={"role": "project_owner"},
        headers=auth,
    )
    # 204 on grant; a re-run may answer 409 if the grant already exists.
    if grant.status_code not in (204, 409):
        raise SeedError(
            f"grant project_owner to {user_id!r} failed: "
            f"{grant.status_code} {grant.text}"
        )

    return await obtain_token(client, base_url)


async def ensure_project_document(
    client: httpx.AsyncClient, base_url: str, token: str
) -> None:
    """Create (or tolerate-exists) a C5 document with one approved entity."""
    auth = {"Authorization": f"Bearer {token}", **_headers()}

    create_resp = await client.post(
        f"{base_url}/api/v1/projects/{DEMO_PROJECT}/requirements/documents",
        json={"slug": DEMO_SLUG},
        headers=auth,
    )
    if create_resp.status_code not in (201, 409):
        raise SeedError(
            f"create document failed: {create_resp.status_code} {create_resp.text}"
        )

    put_headers = {**auth, "If-Match": f'"{DEMO_SLUG}@v1"'}
    put_resp = await client.put(
        f"{base_url}/api/v1/projects/{DEMO_PROJECT}/requirements/documents/{DEMO_SLUG}",
        json={"content": _DEMO_DOC},
        headers=put_headers,
    )
    # 200 on accept, 412/428 if already seeded with the same content / etag drift.
    if put_resp.status_code not in (200, 412, 428):
        raise SeedError(f"seed doc failed: {put_resp.status_code} {put_resp.text}")


async def ensure_memory_source(
    client: httpx.AsyncClient, base_url: str, token: str
) -> None:
    """Ingest one small text source into C7 so retrieval tests have data."""
    auth = {"Authorization": f"Bearer {token}", **_headers()}
    body = {
        "source_id": "seed-source-001",
        "project_id": DEMO_PROJECT,
        "mime_type": "text/plain",
        "content": "The platform validates seeded requirements through C6.",
        "size_bytes": 62,
        "uploaded_by": ADMIN_USER,  # C7 requires explicit uploader attribution
    }
    resp = await client.post(
        f"{base_url}/api/v1/memory/projects/{DEMO_PROJECT}/sources",
        json=body,
        headers=auth,
    )
    # 201 on accept, 409 if already ingested, 400 if schema drifted.
    if resp.status_code not in (201, 409):
        raise SeedError(
            f"seed memory source failed: {resp.status_code} {resp.text}"
        )


async def enqueue_mock_llm_response(
    mock_llm_base_url: str | None,
) -> None:
    """Best-effort: push one canned orchestrator completion into the mock LLM.

    The mock LLM admin endpoint is NOT routed through Traefik (it's an
    internal tool). When ``mock_llm_base_url`` is None, we skip silently.
    """
    if mock_llm_base_url is None:
        return
    async with httpx.AsyncClient(timeout=5.0) as client:
        canned = {
            "envelope": {
                "status": "SUCCESS",
                "output": {
                    "message": "mock response for seeded run",
                },
            }
        }
        try:
            resp = await client.post(
                f"{mock_llm_base_url}/admin/enqueue", json=canned
            )
            if resp.status_code != 200:
                print(
                    f"WARNING: mock LLM enqueue returned {resp.status_code}: "
                    f"{resp.text}",
                    file=sys.stderr,
                )
        except httpx.RequestError as exc:
            print(
                f"WARNING: mock LLM unreachable at {mock_llm_base_url}: {exc}",
                file=sys.stderr,
            )


async def run(args: argparse.Namespace) -> int:
    await wait_stack_ready(args.base_url, timeout_s=args.timeout_s)
    async with httpx.AsyncClient(timeout=10.0) as client:
        token = await obtain_token(client, args.base_url)
        # Re-bound: the returned token carries the project_scopes the grant
        # just created, which the content endpoints below require.
        token = await ensure_project_access(client, args.base_url, token)
        await ensure_project_document(client, args.base_url, token)
        await ensure_memory_source(client, args.base_url, token)
    await enqueue_mock_llm_response(args.mock_llm_url)

    summary: dict[str, Any] = {
        "base_url": args.base_url,
        "project_id": DEMO_PROJECT,
        "document_slug": DEMO_SLUG,
        "entity_id": "R-900-001",
        "admin_user": ADMIN_USER,
    }
    print("SEED OK:", summary)
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inject deterministic seed data into the running platform stack."
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Public Traefik URL (default: http://localhost:56000 — R-100-122 PORT_C1_PUBLIC)",
    )
    parser.add_argument(
        "--mock-llm-url",
        default=None,
        help=(
            "Mock-LLM admin URL (e.g. http://localhost:59800 — R-100-122 PORT_MOCK_LLM). Omit to skip "
            "LLM enqueue step. The mock-LLM admin endpoint is NOT public; "
            "it is only reachable from the compose network, or when the "
            "mock container is temporarily exposed for seeding."
        ),
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=60.0,
        help="Stack readiness timeout in seconds.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    rc = asyncio.run(run(_parse_args()))
    sys.exit(rc)
