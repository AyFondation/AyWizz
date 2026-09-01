# =============================================================================
# File: c7_livedocs_client.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/c7_livedocs_client.py
# Description: Thin outbound client C4 -> C7 for the LIGHT live-docs RAG index
#              (D-021 / R-400-232). When a project-tree document is written /
#              deleted / moved, C4 keeps the C7 LIVE_DOCS index in sync via the
#              `.../live-docs/index` endpoints. Called AS THE SYSTEM
#              (self-asserted `project_editor` forward-auth headers, same
#              convention as the n8n->C7 calls) since this is an internal
#              service-to-service sync, not the operator's own request.
#
#              Best-effort by design: an indexing failure is LOGGED, never
#              raised — a document save/delete must not fail because the RAG
#              index is momentarily unreachable (the doc re-indexes on its next
#              edit, and re-embed/staleness paths remain the safety net).
#              Disabled (no-op) when no base_url is configured.
#
#              v2 (R-200-173): adds `kg_indexed(path)` — a best-effort READ so
#              C4's source-file meta endpoint can report real KG membership
#              (resolves Q-200-018).
#
# @relation implements:R-400-232
# @relation implements:R-200-173
# =============================================================================

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from ay_platform_core.observability import make_traced_client

_log = logging.getLogger(__name__)


class C7LiveDocsClient:
    """Keeps the C7 LIVE_DOCS index in sync with the C4 live-docs tree."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_s: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        if client is not None:
            self._client: httpx.AsyncClient | None = client
            self._owned = False
        elif base_url:
            self._client = make_traced_client(timeout=httpx.Timeout(timeout_s))
            self._owned = True
        else:
            self._client = None
            self._owned = False

    def _headers(self, tenant_id: str, actor: str) -> dict[str, str]:
        # System identity asserting a content role (project_editor) — the same
        # trust model n8n uses; never `platform_manager` (content-excluded).
        return {
            "X-User-Id": actor or "system",
            "X-Tenant-Id": tenant_id,
            "X-User-Roles": "project_editor",
        }

    async def index(
        self,
        *,
        tenant_id: str,
        project_id: str,
        path: str,
        content: str,
        actor: str = "system",
    ) -> None:
        if not self._base or self._client is None:
            return
        url = f"{self._base}/api/v1/memory/projects/{quote(project_id)}/live-docs/index"
        try:
            resp = await self._client.put(
                url,
                headers=self._headers(tenant_id, actor),
                json={"path": path, "content": content, "uploaded_by": actor},
            )
        except httpx.HTTPError as exc:
            _log.warning("live-docs index unreachable (%s/%s %s): %s",
                         tenant_id, project_id, path, exc)
            return
        if resp.status_code >= 400:
            _log.warning("live-docs index %s -> %s: %s",
                         path, resp.status_code, resp.text[:200])

    async def remove(
        self,
        *,
        tenant_id: str,
        project_id: str,
        path: str,
        actor: str = "system",
    ) -> None:
        if not self._base or self._client is None:
            return
        # The C7 route is `/live-docs/index/{path:path}` — a catch-all, so the
        # path segments pass through un-encoded (only guard against a leading
        # slash collapsing the route).
        url = (
            f"{self._base}/api/v1/memory/projects/{quote(project_id)}"
            f"/live-docs/index/{path.lstrip('/')}"
        )
        try:
            resp = await self._client.delete(
                url, headers=self._headers(tenant_id, actor)
            )
        except httpx.HTTPError as exc:
            _log.warning("live-docs remove unreachable (%s/%s %s): %s",
                         tenant_id, project_id, path, exc)
            return
        if resp.status_code >= 400 and resp.status_code != 404:
            _log.warning("live-docs remove %s -> %s: %s",
                         path, resp.status_code, resp.text[:200])

    async def kg_indexed(
        self,
        *,
        tenant_id: str,
        project_id: str,
        path: str,
        actor: str = "system",
    ) -> bool | None:
        """R-200-173 / R-400-232 — ask C7 whether a live-doc PATH contributes
        to the project's structural KG. Best-effort READ: returns the bool on
        success, or None when the sync is disabled/unreachable/errors — so the
        meta endpoint can leave `kg_indexed` null rather than fail."""
        if not self._base or self._client is None:
            return None
        url = (
            f"{self._base}/api/v1/memory/projects/{quote(project_id)}"
            "/live-docs/kg-indexed"
        )
        try:
            resp = await self._client.get(
                url, headers=self._headers(tenant_id, actor), params={"path": path},
            )
        except httpx.HTTPError as exc:
            _log.warning("live-docs kg-indexed unreachable (%s/%s %s): %s",
                         tenant_id, project_id, path, exc)
            return None
        if resp.status_code >= 400:
            _log.warning("live-docs kg-indexed %s -> %s: %s",
                         path, resp.status_code, resp.text[:200])
            return None
        try:
            return bool(resp.json()["kg_indexed"])
        except (KeyError, ValueError):
            return None

    async def aclose(self) -> None:
        if self._owned and self._client is not None:
            await self._client.aclose()
