# =============================================================================
# File: object_tools.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c3_conversation/object_tools.py
# Description: Object-grain tool catalogue + executor for the traceability
#              workbench — 310-SPEC §4.1 / §4.7, D-023 (as amended).
#
#              ALONGSIDE DocGen, NOT INSTEAD OF IT. `document_tools.py`
#              drives live-docs (D-015, R-200-150..156): free-form notes
#              and drafts, whole-file writes, no cycle and no review gate.
#              310-SPEC never asks to replace that surface. These tools
#              drive the OBJECT corpus, where every write is a proposal a
#              human reviews. Two surfaces, two vocabularies, one chat.
#
#              THE CATALOGUE IS CLOSED, AND ITS OMISSIONS ARE THE POINT:
#
#                  list_container_objects   read
#                  read_object              read
#                  create_object            proposes
#                  propose_object_change    proposes
#                  list_object_versions     read
#
#              There is NO `review`, NO `accept`, NO `auto_accept`, NO
#              lock-breaking and NO delete. An agent that could accept its
#              own proposal is the entire supervision model defeated — the
#              operator's whole premise is that the LLM does the work and
#              humans vet it systematically. A test asserts each absence
#              by name rather than trusting the list, because the failure
#              mode is a well-meaning addition, not a typo.
#
#              Deletion is absent for a second, independent reason: there
#              is nothing to call. `objects/storage.py` exposes no delete
#              or compact API at all (DV-08), so this catalogue could not
#              offer one even if it wanted to.
#
#              The review and lock routes, by contrast, DO exist on C5
#              (`.../objects/{id}/review`, `/lock`, `/lock/force`). Their
#              absence here is therefore a real restriction of a reachable
#              capability, not an artefact of nothing being implemented —
#              which is exactly why the prohibition needs a test.
#
#              EVERY WRITE LANDS AS `proposed`. `create_object` and
#              `propose_object_change` go to C5's object surface, which
#              refuses to advance a version without a review record
#              (R-310-010) — so the gate is enforced by the producer, not
#              promised by this client.
#
# @relation implements:R-310-025
# @relation implements:R-310-190
# =============================================================================

from __future__ import annotations

import logging
from typing import Any, ClassVar

import httpx

_log = logging.getLogger("c3_conversation.object_tools")
"""Observability for object-tool dispatch. WARNING on an unrecognised
name, which is how a model inventing `accept_object` surfaces instead of
failing silently."""

_CONTAINER_RULE = (
    "The container slug exactly as the published cycle declares it, "
    "e.g. '030-ARCH'. Call list_container_objects first if unsure; do not "
    "invent one."
)
_OBJECT_RULE = (
    "The object identifier as returned by list_container_objects or "
    "read_object, e.g. 'AD-100'."
)

#: What an agent may do to the object corpus. Descriptions are written for
#: the model — they ARE its only specification of each tool — and they say
#: plainly that a write is a proposal, so the model does not report work as
#: finished when it is awaiting review.
OBJECT_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_container_objects",
            "description": (
                "List the objects of one container in document order, with "
                "their type, review state and current version. Call this "
                "before writing anything, to see what already answers the "
                "requirements allocated to the container."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "container": {
                        "type": "string",
                        "description": _CONTAINER_RULE,
                    },
                },
                "required": ["container"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_object",
            "description": (
                "Return one object's current content and metadata. Use it "
                "before proposing a change so you edit from the real "
                "current text rather than from memory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "container": {
                        "type": "string",
                        "description": _CONTAINER_RULE,
                    },
                    "object_id": {
                        "type": "string",
                        "description": _OBJECT_RULE,
                    },
                },
                "required": ["container", "object_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_object",
            "description": (
                "Create a new object in a container. The object is created "
                "in the 'proposed' state and a human must accept it before "
                "it counts as coverage — so do not report the work as done, "
                "report it as awaiting review."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "container": {
                        "type": "string",
                        "description": _CONTAINER_RULE,
                    },
                    "object_type": {
                        "type": "string",
                        "description": (
                            "One of: heading, subheading, paragraph, figure, "
                            "table, requirement."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": "The object's full text.",
                    },
                    "ordinal": {
                        "type": "integer",
                        "description": "Position within the container.",
                    },
                },
                "required": ["container", "object_type", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_object_change",
            "description": (
                "Propose new content for an existing object. This writes a "
                "working draft; it does NOT change the object. A human "
                "reviews the draft and decides. Supply the version you read, "
                "so a change made by someone else since is detected instead "
                "of being overwritten."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "container": {
                        "type": "string",
                        "description": _CONTAINER_RULE,
                    },
                    "object_id": {
                        "type": "string",
                        "description": _OBJECT_RULE,
                    },
                    "content": {
                        "type": "string",
                        "description": "The proposed full replacement text.",
                    },
                    "expected_version": {
                        "type": "integer",
                        "description": (
                            "The version you read. The write is refused if "
                            "the object has moved on."
                        ),
                    },
                    "rationale": {
                        "type": "string",
                        "description": (
                            "Why the change is needed, for the reviewer."
                        ),
                    },
                },
                "required": ["container", "object_id", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_object_versions",
            "description": (
                "List an object's version history with the review decision "
                "that produced each one. Use it to understand how the object "
                "reached its current form before proposing a change."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "container": {
                        "type": "string",
                        "description": _CONTAINER_RULE,
                    },
                    "object_id": {
                        "type": "string",
                        "description": _OBJECT_RULE,
                    },
                },
                "required": ["container", "object_id"],
            },
        },
    },
]

OBJECT_TOOL_NAMES = frozenset(
    tool["function"]["name"] for tool in OBJECT_TOOLS
)

#: Capabilities an agent SHALL NOT have over the object corpus. Named
#: explicitly so the prohibition is testable: the failure mode here is a
#: well-meaning addition during a later increment, not a typo.
FORBIDDEN_TOOL_CAPABILITIES = frozenset(
    {
        "review",
        "review_object",
        "accept",
        "accept_object",
        "auto_accept",
        "auto_accept_object",
        "reject_object",
        "delete_object",
        "break_lock",
        "release_lock",
        "force_unlock",
    }
)


class ObjectToolClient:
    """Thin async HTTP client over C5's object surface.

    One instance per app; the caller's forward-auth identity is passed
    per call rather than at construction, so a single client serves every
    tenant and user — the same shape as `DocumentToolClient`.

    Args:
        c5_base_url: Base URL of the Requirements Service.
    """

    def __init__(self, c5_base_url: str) -> None:
        self._base = c5_base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=30.0)

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    @staticmethod
    def _headers(
        *, user_id: str, tenant_id: str, user_roles: str
    ) -> dict[str, str]:
        return {
            "X-User-Id": user_id,
            "X-Tenant-Id": tenant_id,
            "X-User-Roles": user_roles,
        }

    async def execute(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        project_id: str,
        user_id: str,
        tenant_id: str,
        user_roles: str,
    ) -> dict[str, Any]:
        """Run one object tool call.

        Returns a dict the caller serialises into the `tool` role message
        fed back to the LLM. A functional error (404, 409, 423) is
        returned as `{"error": ...}` rather than raised, so the model can
        recover — a 409 on `expected_version` in particular is
        actionable: re-read and re-propose.

        A forbidden capability is refused with a message that says WHY,
        because a model told only "unknown tool" will try a synonym.
        """
        if name in FORBIDDEN_TOOL_CAPABILITIES:
            _log.warning("execute FORBIDDEN_TOOL name=%r project=%s", name, project_id)
            return {
                "error": (
                    f"{name!r} is not available to an agent. Reviewing, "
                    "accepting and deleting objects are human decisions "
                    "(R-310-010); propose the change and report it as "
                    "awaiting review."
                )
            }
        handler = self._HANDLERS.get(name)
        if handler is None:
            _log.warning(
                "execute UNKNOWN_TOOL name=%r arg_keys=%s known=%s",
                name,
                sorted(arguments.keys()),
                sorted(self._HANDLERS.keys()),
            )
            return {"error": f"unknown tool {name!r}"}
        _log.info(
            "execute tool=%s arg_keys=%s project=%s",
            name,
            sorted(arguments.keys()),
            project_id,
        )
        headers = self._headers(
            user_id=user_id, tenant_id=tenant_id, user_roles=user_roles
        )
        base = f"{self._base}/api/v1/projects/{project_id}/containers"
        try:
            result: dict[str, Any] = await handler(self, base, headers, arguments)
            return result
        except httpx.HTTPError as exc:
            return {"error": f"transport error calling C5: {exc}"}

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _t_list(
        self, base: str, headers: dict[str, str], args: dict[str, Any]
    ) -> dict[str, Any]:
        container = str(args.get("container", ""))
        response = await self._client.get(
            f"{base}/{container}/objects", headers=headers
        )
        if response.status_code == 200:
            return {"objects": response.json().get("objects", [])}
        return _failed("list_container_objects", response)

    async def _t_read(
        self, base: str, headers: dict[str, str], args: dict[str, Any]
    ) -> dict[str, Any]:
        container = str(args.get("container", ""))
        object_id = str(args.get("object_id", ""))
        response = await self._client.get(
            f"{base}/{container}/objects/{object_id}", headers=headers
        )
        if response.status_code == 200:
            return {"object": response.json()}
        return _failed("read_object", response)

    async def _t_create(
        self, base: str, headers: dict[str, str], args: dict[str, Any]
    ) -> dict[str, Any]:
        container = str(args.get("container", ""))
        payload = {
            "object_type": args.get("object_type"),
            "content": args.get("content"),
            "ordinal": args.get("ordinal", 1),
        }
        response = await self._client.post(
            f"{base}/{container}/objects", json=payload, headers=headers
        )
        if response.status_code in (200, 201):
            return {
                "object": response.json(),
                "note": (
                    "Created in the 'proposed' state. It does not count as "
                    "coverage until a human accepts it."
                ),
            }
        return _failed("create_object", response)

    async def _t_propose(
        self, base: str, headers: dict[str, str], args: dict[str, Any]
    ) -> dict[str, Any]:
        container = str(args.get("container", ""))
        object_id = str(args.get("object_id", ""))
        payload: dict[str, Any] = {
            "content": args.get("content"),
            "rationale": args.get("rationale", ""),
        }
        if args.get("expected_version") is not None:
            payload["expected_version"] = args["expected_version"]
        response = await self._client.put(
            f"{base}/{container}/objects/{object_id}/draft",
            json=payload,
            headers=headers,
        )
        if response.status_code in (200, 201):
            return {
                "draft": response.json(),
                "note": (
                    "A working draft was written. The object is unchanged "
                    "until a human reviews the draft."
                ),
            }
        return _failed("propose_object_change", response)

    async def _t_versions(
        self, base: str, headers: dict[str, str], args: dict[str, Any]
    ) -> dict[str, Any]:
        container = str(args.get("container", ""))
        object_id = str(args.get("object_id", ""))
        response = await self._client.get(
            f"{base}/{container}/objects/{object_id}/versions", headers=headers
        )
        if response.status_code == 200:
            return {"versions": response.json().get("versions", [])}
        return _failed("list_object_versions", response)

    _HANDLERS: ClassVar[dict[str, Any]] = {
        "list_container_objects": _t_list,
        "read_object": _t_read,
        "create_object": _t_create,
        "propose_object_change": _t_propose,
        "list_object_versions": _t_versions,
    }


def _failed(tool: str, response: httpx.Response) -> dict[str, Any]:
    """Return a model-actionable error for a non-success response.

    The status is interpreted rather than echoed, because a model told
    only "HTTP 409" retries identically while one told "re-read and
    re-propose" recovers. 423 is distinguished from 409 for the same
    reason: waiting is the right response to a lease, re-reading is the
    right response to a stale version (DV-14).
    """
    detail = ""
    try:
        body = response.json()
        detail = str(body.get("detail", ""))
    except ValueError:  # pragma: no cover - non-JSON error body
        detail = response.text[:200]

    if response.status_code == 409:
        advice = (
            "The object has changed since you read it. Call read_object "
            "again and re-propose against the new version."
        )
    elif response.status_code == 423:
        advice = (
            "Someone else holds an edit lease on this object. Do not retry; "
            "report that it is locked and by whom."
        )
    elif response.status_code == 403:
        advice = "You are not permitted this operation on this project."
    elif response.status_code == 404:
        advice = "No such container or object. Call list_container_objects."
    else:
        advice = "Report the failure rather than retrying blindly."
    return {
        "error": f"{tool} failed: HTTP {response.status_code}",
        "detail": detail,
        "advice": advice,
    }
