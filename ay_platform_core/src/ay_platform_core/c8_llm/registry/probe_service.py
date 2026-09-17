# =============================================================================
# File: probe_service.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/probe_service.py
# Description: Provider and model probes (R-800-150, R-800-151, R-800-152).
#
#              NO BYPASS, EITHER PROBE. Both reach the provider through the
#              platform's own plumbing: client → quota → alias resolution →
#              credential injection → LiteLLM proxy → provider. Egress leaves
#              the PROXY pod, as it does in production.
#
#              This is stricter than the first version, on the operator's call
#              (2026-09-17), and the reason is worth keeping. The provider
#              probe used to issue its own GET to `{base_url}/v1/models` from
#              this component: free, fast, and measuring the wrong thing —
#              production egress leaves the proxy pod, so a direct call from
#              here could read green while the real route was blocked, or the
#              reverse. A probe whose verdict does not cover the deployed path
#              converts an unknown into a false assurance, which is exactly
#              what R-800-151 exists to forbid; it now applies to both.
#
#              THE COST OF THAT CHOICE IS EXPLICIT: the provider probe is no
#              longer free. There is no "ping an endpoint" operation in the
#              pipeline — its unit of work is a completion — so probing a
#              provider means one minimal completion through one of its
#              models, and a provider with no model configured has no pipeline
#              path to exercise at all.
#
#              @relation implements:R-800-150
#              @relation implements:R-800-151
#              @relation implements:R-800-152
# =============================================================================

from __future__ import annotations

import logging
import time
from typing import Any, Protocol

from ay_platform_core.c8_llm.models import (
    ChatCompletionRequest,
    ChatMessage,
    ChatRole,
)
from ay_platform_core.c8_llm.registry.models import CapabilityEvidence
from ay_platform_core.c8_llm.registry.probe_models import (
    CapabilityProbeOutcome,
    ModelProbeResult,
    ProbeOutcome,
    ProviderProbeResult,
)

_log = logging.getLogger("c8_llm.probe")

# A 1x1 transparent PNG. Smallest thing that is unambiguously an image, so a
# vision refusal is about capability rather than about payload size.
_PIXEL_PNG_DATA_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)

# Deliberately trivial: the probe establishes reachability and capability, not
# quality. Anything longer just costs more for the same verdict.
_PROBE_PROMPT = "Reply with the single word: ok"
_PROBE_MAX_TOKENS = 8

_PROBE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "ay_probe_ping",
        "description": "Return the string 'pong'. Call this tool now.",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    },
}


class _ProviderStore(Protocol):
    async def get(self, provider_id: str) -> dict[str, Any] | None: ...


class _RegistryStore(Protocol):
    async def get(self, model_id: str) -> dict[str, Any] | None: ...
    async def list_all(self) -> list[dict[str, Any]]: ...


class _Cipher(Protocol):
    def decrypt(self, token: str, *, aad: str) -> str: ...


class _Completer(Protocol):
    """The slice of LLMGatewayClient the model probe needs. Narrow on purpose:
    the probe must not be able to reach past chat completion.

    Declared with the EXACT keyword arguments the probe passes, not
    `**kwargs`: a protocol demanding arbitrary keywords is not satisfied by a
    method with a fixed (if long) signature, and the real client has one."""

    async def chat_completion(
        self, payload: ChatCompletionRequest, *, agent_name: str, session_id: str,
        reasoning_verbose: bool = False,
    ) -> Any: ...


class LLMProbeService:
    """Operator-facing connectivity and capability probes."""

    def __init__(
        self,
        provider_repo: _ProviderStore,
        registry_repo: _RegistryStore,
        cipher: _Cipher | None,
        completer: _Completer | None = None,
        *,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._providers = provider_repo
        self._registry = registry_repo
        self._cipher = cipher
        self._completer = completer
        self._timeout = timeout_seconds

    # ------------------------------------------------------------------
    # R-800-150 — provider endpoint
    # ------------------------------------------------------------------

    async def probe_provider(self, provider_id: str) -> ProviderProbeResult:
        """Reach the provider THROUGH THE PLATFORM'S OWN PLUMBING.

        There is no "ping an endpoint" operation in the pipeline — its unit of
        work is a completion for a model. So a provider probe that refuses to
        bypass anything IS a minimal completion through one of that provider's
        models: client → quota → alias resolution → credential injection →
        proxy → provider, with egress leaving from the proxy pod exactly as it
        does in production.

        An earlier version issued its own GET to `{base_url}/v1/models` from
        this component. It was free and fast, and it measured the wrong thing:
        production egress leaves the PROXY pod, so a direct call from here
        could be green while the real route was blocked, or the reverse.
        """
        prov = await self._providers.get(provider_id)
        if prov is None:
            return ProviderProbeResult(
                provider_id=provider_id,
                outcome=ProbeOutcome.NOT_CONFIGURED,
                effective_url="",
                error="no such provider",
            )

        hint = str(prov.get("api_key_hint", ""))
        # What the pipeline will send as `api_base` — read from the same
        # document `RegistryKeyProvider` reads, so it is the value in play and
        # not a second composition of our own.
        api_base = str(prov.get("base_url", ""))

        model = await self._first_model_of(provider_id)
        if model is None:
            return ProviderProbeResult(
                provider_id=provider_id,
                outcome=ProbeOutcome.NOT_CONFIGURED,
                effective_url=api_base,
                api_key_hint=hint,
                error=(
                    "no model is configured for this provider, so there is no "
                    "pipeline path to exercise. Add a model, then probe."
                ),
            )
        if self._completer is None:
            return ProviderProbeResult(
                provider_id=provider_id,
                outcome=ProbeOutcome.NOT_CONFIGURED,
                effective_url=api_base,
                api_key_hint=hint,
                error="no gateway client configured for probing",
            )

        alias = str(model.get("alias", ""))
        started = time.monotonic()
        outcome, status, error = await self._complete(
            alias, ChatCompletionRequest(
                model=alias,
                messages=[ChatMessage(role=ChatRole.USER, content=_PROBE_PROMPT)],
                max_tokens=_PROBE_MAX_TOKENS,
            ),
        )
        return ProviderProbeResult(
            provider_id=provider_id,
            outcome=outcome,
            effective_url=api_base,
            status_code=status,
            error=error,
            api_key_hint=hint,
            latency_ms=int((time.monotonic() - started) * 1000),
            via_model_id=str(model.get("_key", "")),
            via_alias=alias,
        )

    async def _first_model_of(self, provider_id: str) -> dict[str, Any] | None:
        """Pick a model to probe the provider with. Enabled models first: a
        disabled one may be disabled precisely because it does not work, which
        would make the provider look broken when only that model is."""
        models = await self._registry.list_all()
        owned: list[dict[str, Any]] = [
            m for m in models if m.get("provider_id") == provider_id
        ]
        if not owned:
            return None
        return next((m for m in owned if m.get("enabled", True)), owned[0])

    # ------------------------------------------------------------------
    # R-800-151 / R-800-152 — model + capabilities
    # ------------------------------------------------------------------

    async def probe_model(
        self, model_id: str, *, probe_capabilities: bool = False,
    ) -> ModelProbeResult:
        entry = await self._registry.get(model_id)
        if entry is None:
            return ModelProbeResult(
                model_id=model_id, alias="", provider_id="",
                outcome=ProbeOutcome.NOT_CONFIGURED, resolved_target="",
                error="no such model",
            )

        alias = str(entry.get("alias", ""))
        provider_id = str(entry.get("provider_id", ""))
        prov = await self._providers.get(provider_id) if provider_id else None
        wire = str(prov.get("wire_format", "")) if prov else ""
        target = f"{wire}/{entry.get('upstream_model', '')}" if wire else ""

        if self._completer is None:
            return ModelProbeResult(
                model_id=model_id, alias=alias, provider_id=provider_id,
                outcome=ProbeOutcome.NOT_CONFIGURED, resolved_target=target,
                error="no gateway client configured for probing",
            )

        base = ModelProbeResult(
            model_id=model_id, alias=alias, provider_id=provider_id,
            outcome=ProbeOutcome.OK, resolved_target=target,
        )

        started = time.monotonic()
        outcome, status, error = await self._complete(
            alias, ChatCompletionRequest(
                model=alias,
                messages=[ChatMessage(role=ChatRole.USER, content=_PROBE_PROMPT)],
                max_tokens=_PROBE_MAX_TOKENS,
            ),
        )
        base = base.model_copy(update={
            "outcome": outcome,
            "status_code": status,
            "error": error,
            "latency_ms": int((time.monotonic() - started) * 1000),
        })

        if outcome is not ProbeOutcome.OK or not probe_capabilities:
            return base

        return base.model_copy(update={
            "capabilities": [
                await self._probe_tool_calling(alias),
                await self._probe_vision(alias),
                await self._probe_thinking(alias),
            ],
        })

    async def _complete(
        self, alias: str, payload: ChatCompletionRequest,
    ) -> tuple[ProbeOutcome, int | None, str | None]:
        """One completion through the production path. Returns a verdict rather
        than raising: a probe reports failures, it does not propagate them."""
        assert self._completer is not None
        try:
            await self._completer.chat_completion(
                payload, agent_name="probe", session_id=f"probe:{alias}",
            )
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            body = getattr(exc, "body", None)
            return (
                ProbeOutcome.REJECTED if status else ProbeOutcome.UNREACHABLE,
                status if isinstance(status, int) else None,
                str(body) if body is not None else str(exc),
            )
        return ProbeOutcome.OK, 200, None

    async def _probe_tool_calling(self, alias: str) -> CapabilityProbeOutcome:
        """Ask for a tool call and see whether one comes back.

        This is the capability that matters most: `require_tool_calling=True`
        is mandatory for the generate engine, and an agent that cannot call a
        tool cannot edit a file or run a test."""
        assert self._completer is not None
        try:
            resp = await self._completer.chat_completion(
                ChatCompletionRequest(
                    model=alias,
                    messages=[
                        ChatMessage(
                            role=ChatRole.USER, content="Call ay_probe_ping.",
                        ),
                    ],
                    max_tokens=_PROBE_MAX_TOKENS,
                    tools=[_PROBE_TOOL],
                ),
                agent_name="probe", session_id=f"probe:{alias}:tools",
            )
        except Exception as exc:
            return CapabilityProbeOutcome(
                capability="tool_calling", supported=False,
                evidence=CapabilityEvidence.MEASURED, detail=str(exc),
            )
        called = _mentions_tool_call(resp)
        return CapabilityProbeOutcome(
            capability="tool_calling", supported=called,
            evidence=CapabilityEvidence.MEASURED,
            detail="a tool call was returned" if called
            else "the model answered without calling the offered tool",
        )

    async def _probe_vision(self, alias: str) -> CapabilityProbeOutcome:
        assert self._completer is not None
        content: list[dict[str, Any]] = [
            {"type": "text", "text": "What colour is this pixel?"},
            {"type": "image_url", "image_url": {"url": _PIXEL_PNG_DATA_URI}},
        ]
        try:
            await self._completer.chat_completion(
                ChatCompletionRequest(
                    model=alias,
                    messages=[ChatMessage(role=ChatRole.USER, content=content)],
                    max_tokens=_PROBE_MAX_TOKENS,
                ),
                agent_name="probe", session_id=f"probe:{alias}:vision",
            )
        except Exception as exc:
            return CapabilityProbeOutcome(
                capability="vision", supported=False,
                evidence=CapabilityEvidence.MEASURED, detail=str(exc),
            )
        return CapabilityProbeOutcome(
            capability="vision", supported=True,
            evidence=CapabilityEvidence.MEASURED,
            detail="an inline image was accepted",
        )

    async def _probe_thinking(self, alias: str) -> CapabilityProbeOutcome:
        """`reasoning_verbose` is the same switch production uses, so this
        measures the deployed thinking path rather than a parallel one."""
        assert self._completer is not None
        try:
            await self._completer.chat_completion(
                ChatCompletionRequest(
                    model=alias,
                    messages=[ChatMessage(role=ChatRole.USER, content=_PROBE_PROMPT)],
                    max_tokens=_PROBE_MAX_TOKENS,
                ),
                agent_name="probe", session_id=f"probe:{alias}:thinking",
                reasoning_verbose=True,
            )
        except Exception as exc:
            return CapabilityProbeOutcome(
                capability="thinking", supported=False,
                evidence=CapabilityEvidence.MEASURED, detail=str(exc),
            )
        return CapabilityProbeOutcome(
            capability="thinking", supported=True,
            evidence=CapabilityEvidence.MEASURED,
            detail="the adaptive-thinking parameter was accepted",
        )


def _mentions_tool_call(resp: Any) -> bool:
    """True when the response carries a tool call, across the shapes the
    gateway may hand back (parsed model or raw mapping)."""
    choices = getattr(resp, "choices", None)
    if choices is None and isinstance(resp, dict):
        choices = resp.get("choices")
    for choice in choices or []:
        message = getattr(choice, "message", None)
        if message is None and isinstance(choice, dict):
            message = choice.get("message")
        calls = getattr(message, "tool_calls", None)
        if calls is None and isinstance(message, dict):
            calls = message.get("tool_calls")
        if calls:
            return True
    return False
