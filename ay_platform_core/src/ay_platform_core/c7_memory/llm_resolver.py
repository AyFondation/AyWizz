# =============================================================================
# File: llm_resolver.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c7_memory/llm_resolver.py
# Description: Thin outbound client C7 → C8 admin model-quality resolver
#              (LLM-governance #5). At upload C7 maps a project's
#              `model_quality` (low/med/high) to a CONCRETE model alias by
#              calling the c8_admin `GET /api/v1/llm/catalog/resolve` endpoint
#              (HTTP-only between components, like the C7→C12 webhook). The
#              resolved alias is injected into the C13 `llm_assignments`.
#
#              GRACEFUL BY DESIGN: every failure mode — resolver not configured,
#              c8_admin unreachable, 404 (no model of that quality), malformed
#              body — yields None. The caller then simply omits the assignment,
#              so ingestion NEVER breaks on a governance lookup. This also means
#              the feature degrades cleanly until c8_admin is deployed.
# =============================================================================

from __future__ import annotations

import logging

import httpx

from ay_platform_core.observability import make_traced_client

_log = logging.getLogger("c7_memory.llm_resolver")


class LLMResolverClient:
    """Resolves `(tenant, model_quality[, capabilities])` → a model alias via
    the c8_admin catalogue. Best-effort: returns None on ANY failure."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_s: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or make_traced_client(timeout=httpx.Timeout(timeout_s))
        self._owned_client = client is None

    @property
    def enabled(self) -> bool:
        """True when a c8_admin URL is configured. When False the caller skips
        resolution entirely (no network call)."""
        return bool(self._base_url)

    async def resolve(
        self,
        *,
        tenant_id: str,
        user_id: str,
        model_quality: str,
        project_id: str | None = None,
        require_vision: bool = False,
        require_tool_calling: bool = False,
    ) -> str | None:
        """Return the resolved model alias, or None (best-effort) when the
        resolver is disabled, unreachable, or no model matches. When
        `project_id` is given, resolution is SCOPED to the project's model set."""
        if not self.enabled:
            return None
        url = f"{self._base_url}/api/v1/llm/catalog/resolve"
        params = {
            "model_quality": model_quality,
            "require_vision": str(require_vision).lower(),
            "require_tool_calling": str(require_tool_calling).lower(),
        }
        # C7 calls c8_admin service-to-service (no Traefik), so it stamps the
        # forward-auth identity headers itself from the upload's actor/tenant.
        headers = {"X-User-Id": user_id, "X-Tenant-Id": tenant_id}
        if project_id:
            headers["X-Project-Id"] = project_id
        try:
            resp = await self._client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            _log.warning("c8_admin resolver unreachable at %s: %s", url, exc)
            return None
        if resp.status_code == 404:
            # No model of that quality in the tenant catalogue — caller falls
            # back to C13 defaults (NOT an error).
            return None
        if resp.status_code != 200:
            _log.warning(
                "c8_admin resolver returned %s for quality=%s: %s",
                resp.status_code, model_quality, resp.text[:200],
            )
            return None
        try:
            alias = resp.json().get("model_alias")
        except (ValueError, AttributeError):
            return None
        return alias if isinstance(alias, str) and alias else None

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()
