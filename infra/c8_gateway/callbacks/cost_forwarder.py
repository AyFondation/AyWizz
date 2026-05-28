# =============================================================================
# File: cost_forwarder.py
# Version: 1
# Path: infra/c8_gateway/callbacks/cost_forwarder.py
# Description: Standalone LiteLLM CustomLogger mounted into the off-the-shelf
#              LiteLLM proxy (§4.5 — image unmodified, this file is mounted
#              + put on PYTHONPATH ; only `litellm` + `httpx`, both already
#              in the proxy image, are imported — NO ay_platform_core import).
#
#              On each completed (or failed) call it POSTs a compact JSON
#              envelope to the AyWizz cost receiver (`COST_RECEIVER_URL`),
#              which computes the cost + persists one `llm_calls` row in
#              ArangoDB (R-800-070). Best-effort : any forwarding error is
#              swallowed so cost tracking NEVER breaks an LLM call, and a
#              missing/unreachable receiver simply drops the record.
#
#              The proxy config references this instance via
#              `litellm_settings.callbacks: ["cost_forwarder.cost_forwarder_instance"]`.
#
# @relation implements:R-800-070
# =============================================================================

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime
from typing import Any

import httpx
from litellm.integrations.custom_logger import CustomLogger

_log = logging.getLogger("c8_gateway.cost_forwarder")

_RECEIVER_URL = os.environ.get("COST_RECEIVER_URL", "").rstrip("/")
_TIMEOUT_S = float(os.environ.get("COST_RECEIVER_TIMEOUT_S", "2.0"))

# Request fields included in the cache-sensitivity fingerprint (mirrors
# ay_platform_core.c8_llm.callbacks.cost_tracker._fingerprint — kept in
# sync deliberately ; this file cannot import the platform package).
_FINGERPRINT_KEYS = (
    "model",
    "messages",
    "tools",
    "temperature",
    "max_tokens",
    "response_format",
)


def _fingerprint(request: dict[str, Any]) -> str:
    projection = {k: request.get(k) for k in _FINGERPRINT_KEYS if k in request}
    serialised = json.dumps(projection, sort_keys=True, default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(serialised).hexdigest()


def _headers_from_kwargs(kwargs: dict[str, Any]) -> dict[str, str]:
    """Pull the forward-auth / attribution headers LiteLLM stashes under
    `proxy_server_request.headers` (or `litellm_params.metadata.headers`
    across versions)."""
    psr = kwargs.get("proxy_server_request")
    if isinstance(psr, dict) and isinstance(psr.get("headers"), dict):
        return {str(k): str(v) for k, v in psr["headers"].items()}
    meta = (kwargs.get("litellm_params") or {}).get("metadata") or {}
    headers = meta.get("headers") if isinstance(meta, dict) else None
    if isinstance(headers, dict):
        return {str(k): str(v) for k, v in headers.items()}
    return {}


def _usage_of(response_obj: Any) -> dict[str, int]:
    usage = getattr(response_obj, "usage", None)
    if usage is None and isinstance(response_obj, dict):
        usage = response_obj.get("usage")
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0}

    def _get(name: str) -> int:
        val = getattr(usage, name, None)
        if val is None and isinstance(usage, dict):
            val = usage.get(name)
        try:
            return int(val or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "prompt_tokens": _get("prompt_tokens"),
        "completion_tokens": _get("completion_tokens"),
        "cached_tokens": _get("cached_tokens"),
    }


def _model_of(kwargs: dict[str, Any], response_obj: Any) -> str:
    model = getattr(response_obj, "model", None)
    if not model and isinstance(response_obj, dict):
        model = response_obj.get("model")
    return str(model or kwargs.get("model") or "unknown")


async def _post(envelope: dict[str, Any]) -> None:
    if not _RECEIVER_URL:
        return  # receiver not configured — drop silently
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            await client.post(f"{_RECEIVER_URL}/internal/llm-calls", json=envelope)
    except Exception as exc:  # noqa: BLE001 — best-effort ; never break a call
        _log.warning("cost forward failed: %s", exc)


class CostForwarder(CustomLogger):
    """Forwards one envelope per call to the AyWizz cost receiver."""

    async def async_log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        await _post(
            {
                "status": "success",
                "model": _model_of(kwargs, response_obj),
                "usage": _usage_of(response_obj),
                "headers": _headers_from_kwargs(kwargs),
                "fingerprint": _fingerprint(kwargs),
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
            }
        )

    async def async_log_failure_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        exc = kwargs.get("exception")
        await _post(
            {
                "status": "failure",
                "model": _model_of(kwargs, response_obj),
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0},
                "headers": _headers_from_kwargs(kwargs),
                "fingerprint": _fingerprint(kwargs),
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "error_code": type(exc).__name__ if exc else "Unknown",
                "error_message": (str(exc)[:500] if exc else None),
            }
        )


# Referenced from litellm-config*.yaml `litellm_settings.callbacks`.
cost_forwarder_instance = CostForwarder()
