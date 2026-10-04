# =============================================================================
# File: test_object_tools.py
# Version: 1
# Path: ay_platform_core/tests/unit/c3_conversation/test_object_tools.py
# Description: The object-grain agent tool catalogue — 310-SPEC §4.1 / §4.7.
#
#              THE CENTRAL TESTS HERE ARE NEGATIVE. The catalogue's value is
#              what it withholds: an agent that could accept its own
#              proposal defeats the supervision model entirely. The review
#              and lock routes exist on C5 and are reachable, so their
#              absence from this catalogue is a real restriction — and the
#              failure mode is a well-meaning addition in a later
#              increment, which only a by-name assertion catches.
#
#              The URL shapes are asserted against the LIVE C5 router
#              rather than hard-coded, because a client inventing a path
#              that no longer exists is invisible to a mocked test — the
#              front/back drift that a 405 in production is made of.
#
# @relation validates:R-310-025
# @relation validates:R-310-190
# =============================================================================

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import httpx
import pytest
from fastapi.routing import APIRoute

from ay_platform_core.c3_conversation.object_tools import (
    FORBIDDEN_TOOL_CAPABILITIES,
    OBJECT_TOOL_NAMES,
    OBJECT_TOOLS,
    ObjectToolClient,
)
from ay_platform_core.c5_requirements.objects.router import router as c5_objects

_PERMITTED = {
    "list_container_objects",
    "read_object",
    "create_object",
    "propose_object_change",
    "list_object_versions",
}


def _mock_client(
    handler: Callable[[httpx.Request], Coroutine[None, None, httpx.Response]],
) -> ObjectToolClient:
    """Return a client whose transport is the given handler.

    Replacing `_client` is the only seam `ObjectToolClient` offers, and one
    helper is better than five copies of the same reach-in: when a public
    seam appears, there is a single place to change.
    """
    client = ObjectToolClient("http://c5")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


# ---------------------------------------------------------------------------
# The catalogue is exactly the five permitted tools
# ---------------------------------------------------------------------------


def test_the_catalogue_is_exactly_the_five_permitted_tools() -> None:
    assert OBJECT_TOOL_NAMES == _PERMITTED


def test_no_tool_can_review_accept_or_auto_accept() -> None:
    """An agent that accepts its own proposal is the supervision model gone."""
    for name in OBJECT_TOOL_NAMES:
        assert "review" not in name
        assert "accept" not in name


def test_no_tool_can_delete_or_break_a_lock() -> None:
    for name in OBJECT_TOOL_NAMES:
        assert "delete" not in name
        assert "lock" not in name
        assert "force" not in name


def test_the_forbidden_capabilities_are_reachable_on_c5_and_still_withheld() -> None:
    """The prohibition restricts something real, not something unbuilt.

    C5 genuinely exposes review and lock routes. If it did not, this
    catalogue's omissions would prove nothing.
    """
    c5_paths = " ".join(
        route.path for route in c5_objects.routes if isinstance(route, APIRoute)
    )
    assert "/review" in c5_paths
    assert "/lock" in c5_paths
    assert "/lock/force" in c5_paths
    assert not OBJECT_TOOL_NAMES & FORBIDDEN_TOOL_CAPABILITIES


def test_every_tool_declares_a_description_written_for_the_model() -> None:
    for tool in OBJECT_TOOLS:
        description = tool["function"]["description"]
        assert len(description) > 60, tool["function"]["name"]


def test_the_writing_tools_tell_the_model_its_output_awaits_review() -> None:
    """Otherwise the model reports work as finished when it is pending."""
    for name in ("create_object", "propose_object_change"):
        tool = next(t for t in OBJECT_TOOLS if t["function"]["name"] == name)
        assert "review" in tool["function"]["description"].lower()


def test_proposing_a_change_offers_the_optimistic_version_check() -> None:
    tool = next(
        t for t in OBJECT_TOOLS if t["function"]["name"] == "propose_object_change"
    )
    assert "expected_version" in tool["function"]["parameters"]["properties"]


def test_every_tool_is_a_well_formed_openai_function_schema() -> None:
    for tool in OBJECT_TOOLS:
        assert tool["type"] == "function"
        function = tool["function"]
        assert set(function) == {"name", "description", "parameters"}
        parameters = function["parameters"]
        assert parameters["type"] == "object"
        for required in parameters["required"]:
            assert required in parameters["properties"], function["name"]


# ---------------------------------------------------------------------------
# A forbidden call is refused with a reason, not a shrug
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_forbidden_tool_is_refused_with_the_reason() -> None:
    """A model told only 'unknown tool' tries a synonym."""
    client = ObjectToolClient("http://c5")
    try:
        result = await client.execute(
            name="accept_object",
            arguments={"object_id": "AD-100"},
            project_id="adas",
            user_id="agent",
            tenant_id="acme",
            user_roles="project_editor",
        )
    finally:
        await client.aclose()
    assert "not available to an agent" in result["error"]
    assert "R-310-010" in result["error"]


@pytest.mark.asyncio
async def test_an_unknown_tool_is_refused() -> None:
    client = ObjectToolClient("http://c5")
    try:
        result = await client.execute(
            name="invent_something",
            arguments={},
            project_id="adas",
            user_id="agent",
            tenant_id="acme",
            user_roles="project_editor",
        )
    finally:
        await client.aclose()
    assert "unknown tool" in result["error"]


# ---------------------------------------------------------------------------
# The URLs the client builds must be routes C5 actually serves
# ---------------------------------------------------------------------------


class _Recorder:
    """Captures the requests the client makes, answering 200."""

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._payload = payload or {}

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        return httpx.Response(200, json=self._payload)


def _c5_shapes() -> set[tuple[str, tuple[str, ...]]]:
    """The live C5 object routes as (method, shape) pairs."""
    shapes: set[tuple[str, tuple[str, ...]]] = set()
    for route in c5_objects.routes:
        if not isinstance(route, APIRoute):  # pragma: no cover - none today
            continue
        shape = tuple(
            "*" if segment.startswith("{") else segment
            for segment in route.path.strip("/").split("/")
        )
        for method in route.methods or set():
            shapes.add((method, shape))
    return shapes


def _shape_of(path: str) -> tuple[str, ...]:
    # The concrete ids the client substituted become wildcards, EXCEPT the
    # literal segments C5's own paths carry.
    literals = {"api", "v1", "projects", "containers", "objects", "draft", "versions"}
    return tuple(
        segment if segment in literals else "*"
        for segment in path.strip("/").split("/")
    )


async def _run(tool: str, arguments: dict[str, Any]) -> tuple[str, str]:
    recorder = _Recorder()
    client = _mock_client(recorder.handle)
    try:
        await client.execute(
            name=tool,
            arguments=arguments,
            project_id="adas",
            user_id="agent",
            tenant_id="acme",
            user_roles="project_editor",
        )
    finally:
        await client.aclose()
    assert len(recorder.calls) == 1
    return recorder.calls[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("list_container_objects", {"container": "030-ARCH"}),
        ("read_object", {"container": "030-ARCH", "object_id": "AD-100"}),
        (
            "create_object",
            {
                "container": "030-ARCH",
                "object_type": "paragraph",
                "content": "The system shall...",
            },
        ),
        (
            "propose_object_change",
            {
                "container": "030-ARCH",
                "object_id": "AD-100",
                "content": "Revised.",
                "expected_version": 2,
            },
        ),
        ("list_object_versions", {"container": "030-ARCH", "object_id": "AD-100"}),
    ],
)
async def test_every_tool_targets_a_route_c5_actually_serves(
    tool: str, arguments: dict[str, Any]
) -> None:
    """Catches the front/back drift a mocked test otherwise hides.

    The first draft of this client called `/objects/containers/{c}`; C5
    serves `/containers/{c}/objects`. Nothing but comparing against the
    live router would have found it.
    """
    method, path = await _run(tool, arguments)
    assert (method, _shape_of(path)) in _c5_shapes(), f"{method} {path}"


@pytest.mark.asyncio
async def test_a_proposed_change_goes_to_the_draft_route_not_the_object() -> None:
    """R-310-010: an agent writes a draft; it does not advance a version."""
    method, path = await _run(
        "propose_object_change",
        {"container": "030-ARCH", "object_id": "AD-100", "content": "Revised."},
    )
    assert method == "PUT"
    assert path.endswith("/draft")


@pytest.mark.asyncio
async def test_creating_an_object_posts_to_the_container() -> None:
    method, path = await _run(
        "create_object",
        {"container": "030-ARCH", "object_type": "paragraph", "content": "x"},
    )
    assert method == "POST"
    assert path.endswith("/containers/030-ARCH/objects")


# ---------------------------------------------------------------------------
# Errors are interpreted, so the model can recover
# ---------------------------------------------------------------------------


async def _with_status(status: int, tool: str = "read_object") -> dict[str, Any]:
    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"detail": "because"})

    client = _mock_client(respond)
    try:
        return await client.execute(
            name=tool,
            arguments={"container": "030-ARCH", "object_id": "AD-100"},
            project_id="adas",
            user_id="agent",
            tenant_id="acme",
            user_roles="project_editor",
        )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_a_stale_version_tells_the_model_to_re_read() -> None:
    """A model told only 'HTTP 409' retries identically."""
    result = await _with_status(409)
    assert "re-propose" in result["advice"]


@pytest.mark.asyncio
async def test_a_foreign_lease_tells_the_model_not_to_retry() -> None:
    """423 and 409 need different responses: wait versus re-read (DV-14)."""
    result = await _with_status(423)
    assert "Do not retry" in result["advice"]
    assert result["advice"] != (await _with_status(409))["advice"]


@pytest.mark.asyncio
async def test_a_missing_object_points_at_the_listing_tool() -> None:
    result = await _with_status(404)
    assert "list_container_objects" in result["advice"]


@pytest.mark.asyncio
async def test_a_refusal_carries_the_servers_detail() -> None:
    result = await _with_status(403)
    assert result["detail"] == "because"
    assert "not permitted" in result["advice"]


@pytest.mark.asyncio
async def test_a_transport_failure_is_returned_not_raised() -> None:
    """The chat loop must keep going when C5 is unreachable."""

    async def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    client = _mock_client(explode)
    try:
        result = await client.execute(
            name="read_object",
            arguments={"container": "030-ARCH", "object_id": "AD-100"},
            project_id="adas",
            user_id="agent",
            tenant_id="acme",
            user_roles="project_editor",
        )
    finally:
        await client.aclose()
    assert "transport error calling C5" in result["error"]


@pytest.mark.asyncio
async def test_a_successful_create_tells_the_model_it_awaits_acceptance() -> None:
    recorder = _Recorder({"object_id": "AD-100", "review_state": "proposed"})
    client = _mock_client(recorder.handle)
    try:
        result = await client.execute(
            name="create_object",
            arguments={
                "container": "030-ARCH",
                "object_type": "paragraph",
                "content": "x",
            },
            project_id="adas",
            user_id="agent",
            tenant_id="acme",
            user_roles="project_editor",
        )
    finally:
        await client.aclose()
    assert "does not count as coverage" in result["note"]


@pytest.mark.asyncio
async def test_the_callers_identity_is_forwarded_so_c5_rbac_holds() -> None:
    seen: dict[str, str] = {}

    async def respond(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={})

    client = _mock_client(respond)
    try:
        await client.execute(
            name="list_container_objects",
            arguments={"container": "030-ARCH"},
            project_id="adas",
            user_id="o.mathieu",
            tenant_id="acme",
            user_roles="project_editor",
        )
    finally:
        await client.aclose()
    assert seen["x-user-id"] == "o.mathieu"
    assert seen["x-tenant-id"] == "acme"
    assert seen["x-user-roles"] == "project_editor"
