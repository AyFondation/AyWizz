# =============================================================================
# File: c12_client.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c7_memory/c12_client.py
# Description: Thin outbound client C7 → C12 (n8n) ingestion webhook
#              (R-100-081 v3). After C7 stores the raw upload in MinIO it
#              triggers the C12 `extract-and-ingest` workflow by POSTing
#              METADATA ONLY (raw_object_key + ids — never the file bytes).
#              C12 then orchestrates C13 + calls back C7 /ingest-chunks.
#
#              Uses `make_traced_client` (R-100-105) so the request carries
#              the trace context; the n8n webhook ignores the forward-auth
#              headers the factory also stamps (harmless).
#
# @relation implements:R-100-081
# =============================================================================

from __future__ import annotations

from typing import Any

import httpx

from ay_platform_core.observability import make_traced_client


class C12WebhookError(RuntimeError):
    """Raised when the C12 ingestion webhook cannot be triggered."""


class C12WebhookClient:
    """POSTs the metadata-only trigger to the C12 ingestion webhook.

    One owned `httpx.AsyncClient` per instance (or an injected one for
    tests). The body is pure metadata — the file bytes already live in
    MinIO at `raw_object_key`, written by C7 (R-100-081 v3).
    """

    def __init__(
        self,
        *,
        webhook_url: str,
        timeout_s: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = webhook_url
        self._client = client or make_traced_client(timeout=httpx.Timeout(timeout_s))
        self._owned_client = client is None

    async def trigger_ingestion(self, payload: dict[str, Any]) -> None:
        """POST the metadata payload to the webhook. Raises
        ``C12WebhookError`` on transport failure or a non-2xx response so
        the caller can mark the source `failed` (best-effort)."""
        try:
            resp = await self._client.post(self._url, json=payload)
        except httpx.HTTPError as exc:  # connect / timeout / protocol
            raise C12WebhookError(f"C12 webhook unreachable at {self._url}: {exc}") from exc
        if resp.status_code >= 400:
            raise C12WebhookError(
                f"C12 webhook returned {resp.status_code} for {self._url}: "
                f"{resp.text[:500]}"
            )

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()
