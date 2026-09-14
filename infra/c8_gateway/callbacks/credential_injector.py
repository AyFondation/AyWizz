# =============================================================================
# File: credential_injector.py
# Version: 1
# Path: infra/c8_gateway/callbacks/credential_injector.py
# Description: Standalone LiteLLM CustomLogger mounted into the off-the-shelf
#              LiteLLM proxy (§4.5 — image unmodified, this file is mounted +
#              put on PYTHONPATH ; only `litellm` + `httpx`, both already in the
#              proxy image, are imported — NO ay_platform_core import).
#
#              WHAT IT SOLVES. Every tier in `model_list` is `model: "*"` with
#              `configurable_clientside_auth_params` — deliberately holding NO
#              upstream model, NO api_base and NO key (D-011 option B: the proxy
#              is a credential-free forwarder). The app tier supplies all three
#              per request via `C8LLMClient._inject_upstream_key`. A caller that
#              reaches the proxy DIRECTLY never passes through that client — the
#              OpenHands generate engine today, C13 tomorrow — so its calls land
#              on a tier with nothing to route to and nothing to authenticate
#              with. This hook resolves the same call target for those callers.
#
#              IT NEVER OVERRIDES THE APP TIER. When `api_key` is already in the
#              body, the request came from a client that resolved its own target
#              and the hook returns the data untouched. The established path is
#              therefore unaffected by construction, not by careful ordering.
#
#              SECRET DISCIPLINE. The resolved key is written into the request
#              body and NEVER logged, at any level — log lines carry the alias
#              and the outcome, never the credential. Decryption happens app-side
#              in c8_admin, which already holds the master key; this file
#              receives one resolved credential per alias and holds nothing.
#
#              BEST-EFFORT, ALWAYS. Any failure — resolver unreachable, 404 on an
#              unknown alias, malformed response — leaves the body untouched so
#              the proxy behaves exactly as it does today. A credential resolver
#              must never be the reason a call fails that would otherwise work.
#
#              R-800-149 makes the behaviour above NORMATIVE — in particular
#              "SHALL NOT alter a request that already carries an api_key" and
#              "failure SHALL leave the request untouched". They are the
#              requirement, not defensive habits.
#
# @relation implements:R-800-149
# =============================================================================

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from litellm.integrations.custom_logger import CustomLogger

_log = logging.getLogger("c8_gateway.credential_injector")

_RESOLVER_URL = os.environ.get("CALL_TARGET_RESOLVER_URL", "").rstrip("/")
_GATEWAY_KEY = os.environ.get("C8_GATEWAY_API_KEY", "")
_TIMEOUT_S = float(os.environ.get("CALL_TARGET_RESOLVER_TIMEOUT_S", "3.0"))


async def _resolve(alias: str) -> dict[str, Any] | None:
    """Ask c8_admin for the call target. None on ANY failure — see BEST-EFFORT.

    Split out of the hook so the hook itself stays a short, readable sequence of
    guards: every failure mode here collapses to one "leave the body alone".
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(
                f"{_RESOLVER_URL}/internal/v1/llm/call-target",
                json={"alias": alias},
                headers={"Authorization": f"Bearer {_GATEWAY_KEY}"},
            )
    except Exception as exc:  # broad by design: never fail an LLM call
        _log.warning("call-target resolver unreachable for %s: %s", alias, exc)
        return None

    if resp.status_code != 200:
        # 404 is the ordinary "alias not in the registry" case: the caller may
        # be using the mock model or a pass-through the proxy handles on its
        # own. Not an error worth raising the log level for.
        _log.info("call-target unresolved for %s (HTTP %s)", alias, resp.status_code)
        return None

    try:
        target = resp.json()
    except ValueError:
        _log.warning("call-target response for %s was not JSON", alias)
        return None

    if not isinstance(target, dict) or not isinstance(
        target.get("model"), str
    ) or not isinstance(target.get("api_base"), str):
        _log.warning("call-target response for %s missing model/api_base", alias)
        return None
    return target


class CredentialInjector(CustomLogger):  # type: ignore[misc]
    """Resolves `model` → provider call target for direct proxy callers."""

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: dict[str, Any],
        call_type: str,
    ) -> dict[str, Any]:
        """Inject `model` / `api_base` / `api_key` when the caller supplied none.

        Returns the (possibly modified) request data. Returning the dict — rather
        than a string or an Exception — is what tells LiteLLM to proceed with the
        call; the other two return shapes reject it, which this hook must never
        do (see BEST-EFFORT above).
        """
        if not _RESOLVER_URL or not _GATEWAY_KEY:
            return data

        # The app tier already resolved this call. Leave it strictly alone.
        if data.get("api_key"):
            return data

        alias = data.get("model")
        if not isinstance(alias, str) or not alias:
            return data

        target = await _resolve(alias)
        if target is None:
            return data

        data["model"] = target["model"]
        data["api_base"] = target["api_base"]
        api_key = target.get("api_key")
        if isinstance(api_key, str) and api_key:
            data["api_key"] = api_key
        # Alias and resolved model only. The credential is never logged.
        _log.info("call-target injected: %s -> %s", alias, target["model"])
        return data


# The proxy config references this INSTANCE, not the class:
#   litellm_settings.callbacks: ["credential_injector.credential_injector_instance"]
credential_injector_instance = CredentialInjector()
