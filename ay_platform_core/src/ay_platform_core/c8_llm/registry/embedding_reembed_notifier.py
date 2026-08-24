# =============================================================================
# File: embedding_reembed_notifier.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/embedding_reembed_notifier.py
# Description: Best-effort outbound trigger fired when a project's EFFECTIVE
#              embedding model changes (a registry model is edited in a
#              vector-affecting way, or a project switches selection). POSTs a
#              `reembed` job to the C12/n8n webhook; the workflow then calls C7
#              `POST /projects/{id}/reembed` as the SYSTEM (self-asserted
#              `project_owner` headers, per the extract-and-ingest convention)
#              — reembed is a CONTENT op, and the triggering operator may be a
#              content-blind `platform_manager`, so the recompute must run
#              decoupled as the system, not under the operator's identity.
#
#              Best-effort by design: a failure is logged, never raised — the
#              admin operation still succeeds, staleness stays visible via
#              `processing_version` drift, and the operator can reembed
#              manually. Disabled (no-op) when no webhook_url is configured.
#
# @relation implements:R-400-222
# =============================================================================

from __future__ import annotations

import logging

import httpx

from ay_platform_core.observability import make_traced_client

_log = logging.getLogger(__name__)


class ReembedNotifier:
    """Fires a best-effort `reembed` trigger to the C12/n8n webhook."""

    def __init__(
        self,
        *,
        webhook_url: str,
        timeout_s: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = webhook_url
        if client is not None:
            self._client: httpx.AsyncClient | None = client
            self._owned = False
        elif webhook_url:
            self._client = make_traced_client(timeout=httpx.Timeout(timeout_s))
            self._owned = True
        else:
            self._client = None
            self._owned = False

    async def notify(
        self, *, tenant_id: str, project_id: str, model_id: str
    ) -> None:
        if not self._url or self._client is None:
            return
        payload = {
            "job": "reembed",
            "tenant_id": tenant_id,
            "project_id": project_id,
            "model_id": model_id,
        }
        try:
            resp = await self._client.post(self._url, json=payload)
        except httpx.HTTPError as exc:
            _log.warning(
                "reembed webhook unreachable at %s for %s/%s: %s",
                self._url,
                tenant_id,
                project_id,
                exc,
            )
            return
        if resp.status_code >= 400:
            _log.warning(
                "reembed webhook %s returned %s for %s/%s",
                self._url,
                resp.status_code,
                tenant_id,
                project_id,
            )

    async def aclose(self) -> None:
        if self._owned and self._client is not None:
            await self._client.aclose()
