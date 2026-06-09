# =============================================================================
# File: guard.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/quota/guard.py
# Description: Per-call quota guard wired into the C8 gateway client (Lot 3,
#              soft → hard). Given the call's tenant_id it evaluates the global
#              policy: a WARN state is logged (best-effort, non-blocking); an
#              EXCEEDED state raises `QuotaExceededError`, which the client turns
#              into a hard stop (the upstream LLM call never happens). Built over
#              the shared Arango db, mirroring `build_registry_key_provider`.
# =============================================================================

from __future__ import annotations

import logging
from typing import Any

from ay_platform_core.c8_llm.quota.models import QuotaStatus
from ay_platform_core.c8_llm.quota.repository import QuotaRepository
from ay_platform_core.c8_llm.quota.service import QuotaService

_log = logging.getLogger("c8_llm.quota_guard")


class QuotaExceededError(RuntimeError):
    """Raised when a tenant has exceeded a quota window — the gateway SHALL
    refuse the call (hard stop). Carries the evaluated status for surfacing
    (e.g. a 429 mapping at the caller)."""

    def __init__(self, status: QuotaStatus) -> None:
        breached = [w.key for w in status.windows if w.state == "exceeded"]
        super().__init__(
            f"LLM quota exceeded for tenant {status.tenant_id!r} "
            f"(windows: {', '.join(breached)})"
        )
        self.status = status


class QuotaGuard:
    """Async callable `(tenant_id, *, project_id, user_id) -> None`. Enforces all
    four levels for the call's subject. Best-effort on evaluation errors (never
    breaks an LLM call for an infrastructure hiccup), but a clean EXCEEDED verdict
    at ANY level DOES raise — the intended hard stop."""

    def __init__(self, service: QuotaService) -> None:
        self._service = service

    async def __call__(
        self,
        tenant_id: str,
        *,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        try:
            status = await self._service.evaluate(
                tenant_id, project_id=project_id, user_id=user_id, advance=True
            )
        except Exception as exc:
            # Best-effort: an infra hiccup in evaluation must never break a
            # legitimate LLM call — only a clean EXCEEDED verdict blocks.
            _log.warning("quota evaluation failed for tenant %s: %s", tenant_id, exc)
            return
        if status.blocked:
            raise QuotaExceededError(status)
        if status.warned:
            _log.warning(
                "tenant %s approaching an LLM quota limit (warn threshold reached)",
                tenant_id,
            )


def build_quota_guard(db: Any) -> QuotaGuard:
    """Build a quota guard over the shared Arango `db`. Always returns a guard;
    with no policy configured the default windows have OPEN limits, so the guard
    never blocks until the operator sets real limits. Wired into each LLM-calling
    component's app factory alongside the registry key provider."""
    return QuotaGuard(QuotaService(QuotaRepository(db)))
