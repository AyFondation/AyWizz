# =============================================================================
# File: http.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/quota/http.py
# Description: FastAPI glue mapping a hard quota block to HTTP 429. The guard
#              raises `QuotaExceededError` (pure, FastAPI-free) when any level of
#              any window is exceeded; this registers an app-level handler that
#              turns it into a clean `429 Too Many Requests` carrying the
#              evaluated status. Registered by every LLM-calling component's app
#              factory. NOTE: for STREAMING endpoints the guard runs before the
#              first byte, so the handler still applies as long as the block is
#              raised before the StreamingResponse begins.
# =============================================================================

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from ay_platform_core.c8_llm.quota.guard import QuotaExceededError


def register_quota_handler(app: FastAPI) -> None:
    """Map `QuotaExceededError` → 429 with the breached quota status as body."""

    @app.exception_handler(QuotaExceededError)
    async def _quota_exceeded(  # pyright: ignore[reportUnusedFunction]
        _request: Request, exc: QuotaExceededError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": str(exc), "quota": exc.status.model_dump(mode="json")},
        )
