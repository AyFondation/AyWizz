# =============================================================================
# File: call_target_router.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/call_target_router.py
# Description: INTERNAL resolver that turns a model alias into the provider
#              call target (`<wire>/<upstream>` + `api_base` + DECRYPTED
#              `api_key`). Exists for ONE consumer: the LiteLLM proxy's
#              credential-injection hook, which serves callers that reach the
#              proxy directly and therefore never pass through
#              `C8LLMClient._inject_upstream_key` — the OpenHands generate
#              engine today, C13 tomorrow.
#
#              WHY THIS IS NOT A PUBLIC ENDPOINT, and how it is contained.
#              It returns a decrypted provider credential, so it is the one
#              object in the platform whose whole purpose is to hand out a key.
#              Three containments, and all three matter:
#
#                1. NOT ROUTED BY C1. No router in `dynamic/routers.yml`
#                   matches `/internal/`, and no service binding exists for it,
#                   so the path is unreachable from the edge — it falls to the
#                   UI catch-all. Verified, not assumed. Keep it that way:
#                   adding an `/internal` router would expose provider keys to
#                   the internet.
#                2. SHARED-BEARER GUARDED. The caller must present the C8
#                   gateway key. This is deliberately NOT the user forward-auth
#                   contract: the proxy has no user identity. `/internal` is
#                   therefore exempted from `AuthGuardMiddleware` and guarded
#                   here instead.
#                3. DECRYPTION STAYS APP-SIDE. Hosted by c8_admin, which
#                   ALREADY holds `AY_SECRET_MASTER_KEY` because it encrypts
#                   provider keys on upsert. No new component learns the master
#                   key, and the off-the-shelf LiteLLM image never does — it
#                   receives one resolved credential per alias, exactly as it
#                   already receives one per request from the app tier.
#
#              Residual risk, stated plainly: this endpoint is ENUMERABLE by
#              anything holding the gateway key and able to reach c8_admin
#              in-cluster. That is a wider surface than the push model, where a
#              key only ever travels attached to a call the app tier decided to
#              make. Accepted deliberately (operator decision, 2026-09-10) for
#              the benefit it buys: every direct proxy caller is fixed at once
#              instead of one adapter at a time.
#
#              The five containments above are NORMATIVE, not defensive style:
#              R-800-149 states each one, precisely because weakening any single
#              one re-opens the enumeration risk. Read it before relaxing a
#              guard here.
#
#              (The requirement did not exist until 800-SPEC v13. The whole
#              credential-injection contract lived in a version note, which is
#              why `key_provider.py` had no marker either — §8.1 structural gap
#              found 2026-09-11 while auditing a marker of my own that claimed
#              R-800-011, which governs ACCESS MODE, not credentials.)
#
# @relation implements:R-800-149
# =============================================================================

from __future__ import annotations

import hmac
import logging
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

_log = logging.getLogger("c8_llm.call_target_router")

router = APIRouter()


class CallTargetRequest(BaseModel):
    """The alias the proxy received, exactly as it arrived in `body['model']`."""

    model_config = ConfigDict(extra="forbid")

    alias: str = Field(min_length=1, max_length=200)


class CallTargetResponse(BaseModel):
    """The rewritten call. Mirrors what `C8LLMClient._inject_upstream_key`
    already writes into the body, so both paths produce identical upstream
    requests — one resolved in the app tier, one resolved for the proxy."""

    model_config = ConfigDict(extra="forbid")

    model: str
    api_base: str
    api_key: str | None = None


def _require_gateway_bearer(authorization: str | None) -> None:
    """Constant-time comparison against `C8_GATEWAY_API_KEY`.

    `hmac.compare_digest`, not `==`: this guards a credential-dispensing
    endpoint, and a naive comparison leaks the key byte-by-byte through timing
    to anything that can reach it in-cluster.

    An UNSET gateway key denies every request rather than allowing them. A
    misconfigured deployment must fail closed — the opposite default would
    turn a blank env var into an open key-dispensing endpoint.
    """
    expected = os.environ.get("C8_GATEWAY_API_KEY", "")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="call-target resolution disabled: no gateway key configured",
        )
    presented = ""
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()
    if not presented or not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid gateway credential",
        )


@router.post("/internal/v1/llm/call-target", response_model=CallTargetResponse)
async def resolve_call_target(
    body: CallTargetRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> CallTargetResponse:
    """Resolve one alias → provider call target for the proxy hook.

    404 when the alias is unknown or its provider is dangling: the hook then
    leaves the request untouched, so an unresolvable alias degrades to the
    proxy's own behaviour instead of failing the call here.

    NOTHING about the resolved credential is logged, at any level. The alias is
    logged only on the miss path, where no secret exists to leak.
    """
    _require_gateway_bearer(authorization)

    key_provider: Any = getattr(request.app.state, "call_target_provider", None)
    if key_provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="call-target resolution disabled: no registry resolver wired",
        )

    target = await key_provider(body.alias)
    if target is None:
        _log.info("call-target miss for alias %s", body.alias)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no call target for alias '{body.alias}'",
        )
    return CallTargetResponse(
        model=target.model, api_base=target.api_base, api_key=target.api_key
    )
