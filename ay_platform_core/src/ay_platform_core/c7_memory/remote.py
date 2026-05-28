# =============================================================================
# File: remote.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c7_memory/remote.py
# Description: HTTP-based stand-in for `MemoryService` when the platform
#              runs as multiple pods in Kubernetes. C3 (or any caller)
#              swaps this in when C7 is reachable only via HTTP — same
#              semantics as the in-process service, transport over the
#              wire instead of in-process Python calls.
#
#              v1 scope:
#                - `retrieve()` — POST /api/v1/memory/retrieve. Critical
#                  for chat-with-RAG (C3._rag_stream calls this).
#                - `ingest_conversation_turn()` — stubbed (raises
#                  NotImplementedError). The caller wraps it in
#                  `contextlib.suppress(Exception)` so a missing
#                  endpoint disables Phase E (conversation memory
#                  loop) silently rather than breaking the chat reply.
#
#              Forward-auth headers (`X-User-Id`, `X-Tenant-Id`,
#              `X-User-Roles`) are passed explicitly as kwargs and
#              flowed onto the HTTP request — Traefik's forward-auth
#              middleware in the cluster has already validated the
#              caller's JWT, so propagating its claims is the
#              equivalent of the in-process service trusting its
#              caller's `tenant_id` arg.
#
# @relation implements:R-100-114
# @relation implements:R-100-117
# =============================================================================

from __future__ import annotations

from typing import Any

import httpx

from ay_platform_core.c7_memory.models import (
    RetrievalRequest,
    RetrievalResponse,
    SourcePublic,
)


class RemoteMemoryService:
    """HTTP-backed implementation of the C7 surface used by C3.

    Construction-time wiring (`base_url`, optional shared `http_client`)
    happens once at app startup. Per-call kwargs (`user_id`,
    `tenant_id`, `user_roles`) propagate the caller's identity so the
    target C7 instance can enforce its own RBAC — exactly as if the
    request had come straight from C1 Traefik with forward-auth.
    """

    def __init__(
        self,
        base_url: str,
        http_client: httpx.AsyncClient | None = None,
        *,
        timeout_s: float = 30.0,
    ) -> None:
        if not base_url:
            raise ValueError("RemoteMemoryService: base_url is required")
        self._base_url = base_url.rstrip("/")
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=timeout_s)

    async def aclose(self) -> None:
        """Close the underlying httpx client iff we own it. Safe to call
        when an external client was injected — it is left untouched."""
        if self._owns_client:
            await self._http.aclose()

    # ------------------------------------------------------------------
    # retrieve — wraps POST /api/v1/memory/retrieve
    # ------------------------------------------------------------------

    async def retrieve(
        self,
        payload: RetrievalRequest,
        *,
        tenant_id: str,
        user_id: str,
        user_roles: str = "project_editor",
    ) -> RetrievalResponse:
        """Send a retrieval request to the remote C7 and parse the
        response. Raises `httpx.HTTPStatusError` on non-2xx — the
        caller (C3._rag_stream) decides whether a retrieve failure
        falls back to a no-context prompt or surfaces an error."""
        url = f"{self._base_url}/api/v1/memory/retrieve"
        headers = self._auth_headers(
            user_id=user_id, tenant_id=tenant_id, user_roles=user_roles,
        )
        # `mode="json"` ensures enums are serialised as their string
        # values (matches what FastAPI expects when validating).
        body = payload.model_dump(mode="json")
        resp = await self._http.post(url, headers=headers, json=body)
        resp.raise_for_status()
        return RetrievalResponse.model_validate(resp.json())

    # ------------------------------------------------------------------
    # ingest_conversation_turn — stubbed in v1
    # ------------------------------------------------------------------

    async def ingest_conversation_turn(
        self,
        *,
        tenant_id: str,
        project_id: str,
        conversation_id: str,
        turn_id: str,
        user_message: str,
        assistant_reply: str,
        actor_id: str,
        user_id: str = "",
        user_roles: str = "project_editor",
    ) -> SourcePublic:
        """Stubbed in v1. The in-process `MemoryService` synthesizes a
        SourceIngestRequest with `index_kind=CONVERSATIONS` and routes
        through `_index_parsed_source`; the existing public HTTP
        endpoint `POST /api/v1/memory/projects/{p}/sources` doesn't
        accept an `index_kind` parameter, so the conversation-turn
        flow has no remote analogue yet.

        C3._rag_stream wraps the call in `contextlib.suppress(Exception)`
        so this stub gracefully disables Phase E (conversation memory
        loop) in K8s — chat replies still work, follow-up turns just
        don't get re-indexed for retrieval. A future revision adds a
        dedicated `POST /api/v1/memory/projects/{p}/conversations/{c}/turns`
        endpoint and lifts this stub."""
        del (
            tenant_id, project_id, conversation_id, turn_id,
            user_message, assistant_reply, actor_id, user_id, user_roles,
        )
        raise NotImplementedError(
            "RemoteMemoryService.ingest_conversation_turn requires a new "
            "HTTP endpoint not yet exposed by C7. Phase E (conversation "
            "memory loop) is disabled in K8s deployments until that "
            "endpoint lands."
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _auth_headers(
        *, user_id: str, tenant_id: str, user_roles: str,
    ) -> dict[str, str]:
        """Build the forward-auth header set the cluster expects.
        These are the same headers Traefik's `forward-auth-c2`
        middleware injects after C2 verifies the JWT — propagating
        them on inter-component calls preserves the auth context."""
        if not user_id:
            raise ValueError("RemoteMemoryService.retrieve: user_id is required")
        if not tenant_id:
            raise ValueError("RemoteMemoryService.retrieve: tenant_id is required")
        return {
            "X-User-Id": user_id,
            "X-Tenant-Id": tenant_id,
            "X-User-Roles": user_roles,
            "Content-Type": "application/json",
        }


# Public re-export — keeps `from ay_platform_core.c7_memory.remote import
# RemoteMemoryService` working as the canonical import path.
__all__: list[str] = ["RemoteMemoryService"]


# Touch unused imports to keep lint clean.
_ = (Any,)
