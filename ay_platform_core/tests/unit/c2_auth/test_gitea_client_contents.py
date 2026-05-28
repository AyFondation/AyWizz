# =============================================================================
# File: test_gitea_client_contents.py
# Version: 1
# Path: ay_platform_core/tests/unit/c2_auth/test_gitea_client_contents.py
# Description: Unit tests for GiteaClient.get_file_at_ref (R-200-147
#              version-history viewer). Uses an httpx MockTransport so
#              the base64-decode, 404→None, directory→None, and
#              error→raise paths are exercised without a Gitea server.
#
# @relation validates:R-200-147
# =============================================================================

from __future__ import annotations

import base64

import httpx
import pytest

from ay_platform_core.c2_auth.gitea_client import GiteaClient, GiteaError

pytestmark = pytest.mark.unit


def _client(handler) -> GiteaClient:  # type: ignore[no-untyped-def]
    return GiteaClient(
        "http://gitea",
        "root",
        "pw",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.asyncio
async def test_returns_decoded_bytes_for_a_file() -> None:
    """A 200 with base64-encoded `content` SHALL decode to raw bytes ;
    the `ref` SHALL be forwarded as the query param."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ref"] = request.url.params.get("ref", "")
        seen["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "encoding": "base64",
                "content": base64.b64encode(b"# Old content\n").decode("ascii"),
                "sha": "abc",
            },
        )

    client = _client(handler)
    try:
        data = await client.get_file_at_ref(
            owner="svc-t-p", repo="p", path="docs/intro.md", ref="deadbeef",
        )
    finally:
        await client.aclose()
    assert data == b"# Old content\n"
    assert seen["ref"] == "deadbeef"
    assert seen["path"].endswith("/repos/svc-t-p/p/contents/docs/intro.md")


@pytest.mark.asyncio
async def test_missing_file_at_ref_returns_none() -> None:
    """A 404 (file absent at that ref) maps to None, not an error —
    the caller turns that into a clean 404 for the UX."""
    client = _client(lambda _req: httpx.Response(404, json={"message": "Not Found"}))
    try:
        assert (
            await client.get_file_at_ref(
                owner="o", repo="r", path="x.md", ref="sha",
            )
            is None
        )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_directory_listing_returns_none() -> None:
    """Gitea returns a JSON LIST for a directory path (no base64
    `content`) — treated as 'not a file' → None."""
    client = _client(
        lambda _req: httpx.Response(200, json=[{"type": "file", "name": "a.md"}]),
    )
    try:
        assert (
            await client.get_file_at_ref(owner="o", repo="r", path="docs", ref="sha")
            is None
        )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_server_error_raises_gitea_error() -> None:
    """A non-200/404 (e.g. 500) SHALL raise GiteaError so the service
    can surface a 502 rather than silently returning empty content."""
    client = _client(lambda _req: httpx.Response(500, text="boom"))
    try:
        with pytest.raises(GiteaError):
            await client.get_file_at_ref(owner="o", repo="r", path="x.md", ref="s")
    finally:
        await client.aclose()
