# =============================================================================
# File: probe_service.py
# Version: 2
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

import json
import logging
import re
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

# ONE BUDGET CANNOT SERVE FOUR PROBES, which is what a single shared
# `_PROBE_MAX_TOKENS = 8` tried to do — and it made Opus and Sonnet fail
# `tool_calling` and `thinking` on a limit the PROBE imposed rather than on a
# capability they lack (2026-09-29).
#
# 8 tokens is right for reachability and wrong for everything else:
#   - a `tool_use` block does not fit. The tool name plus its JSON arguments
#     exceed 8 tokens on their own, so the response is cut at
#     `finish_reason: length` before a complete `tool_calls` is emitted, and
#     the probe reads "answered without calling the offered tool" — a
#     truncation reported as a missing capability ;
#   - with extended thinking on, Anthropic REQUIRES max_tokens to exceed the
#     thinking budget. 8 is rejected outright, so the probe recorded a 400 as
#     "thinking unsupported" for models whose defining feature it is ;
#   - a one-word answer about an image still needs room for the word.
#
# Each budget below is the smallest that lets the capability EXPRESS itself.
# They are still trivial calls — the probe measures capability, not quality.
_PROBE_MAX_TOKENS = 8
_TOOL_PROBE_MAX_TOKENS = 512
_VISION_PROBE_MAX_TOKENS = 64
_THINKING_PROBE_MAX_TOKENS = 4096

_STRUCTURED_PROBE_MAX_TOKENS = 128
# Deliberately beyond any real ceiling. The point is to be REFUSED, and to
# read the true maximum out of the refusal — see _probe_max_output_tokens.
_ABSURD_MAX_TOKENS = 100_000_000

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

# A SECOND, equally trivial tool. Parallel calling cannot be observed with
# one tool on offer: a single call is then both the maximum possible and
# indistinguishable from a model that only ever calls one.
_PROBE_TOOL_SECOND: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "ay_probe_pong",
        "description": "Return the string 'ping'. Call this tool now.",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    },
}

_PROBE_JSON_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "ay_probe_result",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    },
}

# Long enough to be worth caching in principle, short enough to stay cheap.
# It will usually sit BELOW the provider's minimum cacheable length, which
# is exactly why `_probe_prompt_caching` reports `None` rather than `False`
# when no counter comes back.
_CACHEABLE_SYSTEM_PROMPT = (
    "You are a probe fixture used to establish whether this deployment "
    "supports provider-side prompt caching. Answer any question with a "
    "single short word. " * 8
)


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

    def chat_completion_stream(
        self, payload: ChatCompletionRequest, *, agent_name: str, session_id: str,
    ) -> Any:
        """The streaming path, needed by the `streaming` capability probe.

        NOT declared `async def`: the real client decorates this with
        `@asynccontextmanager`, so the method itself is SYNCHRONOUS and
        returns the context manager. Declaring it async here would describe
        a shape the client does not have, and the protocol would silently
        stop matching."""
        ...


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
                await self._probe_structured_output(alias),
                await self._probe_prompt_caching(alias),
                await self._probe_streaming(alias),
                await self._probe_parallel_tool_calls(alias),
                await self._probe_max_output_tokens(alias),
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
                    max_tokens=_TOOL_PROBE_MAX_TOKENS,
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
                    max_tokens=_VISION_PROBE_MAX_TOKENS,
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
                    max_tokens=_THINKING_PROBE_MAX_TOKENS,
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

    # ------------------------------------------------------------------
    # Second wave (2026-09-29). Each probe below settles a question the
    # platform ACTS on — see ModelCapabilities for what each one changes.
    # ------------------------------------------------------------------

    async def _probe_structured_output(self, alias: str) -> CapabilityProbeOutcome:
        """Ask for a schema and check the answer actually satisfies it.

        Accepting the `response_format` parameter is not the same as
        honouring it: a provider that ignores an unknown field answers 200
        with prose, and a probe that stopped at "no exception" would call
        that support. The verdict therefore rests on PARSING the body.
        """
        assert self._completer is not None
        try:
            resp = await self._completer.chat_completion(
                ChatCompletionRequest(
                    model=alias,
                    messages=[ChatMessage(
                        role=ChatRole.USER,
                        content="Return the number 7 in the `value` field.",
                    )],
                    max_tokens=_STRUCTURED_PROBE_MAX_TOKENS,
                    response_format=_PROBE_JSON_SCHEMA,
                ),
                agent_name="probe", session_id=f"probe:{alias}:structured",
            )
        except Exception as exc:
            return CapabilityProbeOutcome(
                capability="structured_output", supported=False,
                evidence=CapabilityEvidence.MEASURED, detail=str(exc),
            )
        text = _first_content(resp)
        if text is None:
            return CapabilityProbeOutcome(
                capability="structured_output", supported=None,
                evidence=CapabilityEvidence.MEASURED,
                detail="the response carried no textual content to parse",
            )
        try:
            parsed = json.loads(text)
        except ValueError:
            return CapabilityProbeOutcome(
                capability="structured_output", supported=False,
                evidence=CapabilityEvidence.MEASURED,
                detail="the schema was accepted but the answer was not JSON",
            )
        ok = isinstance(parsed, dict) and "value" in parsed
        return CapabilityProbeOutcome(
            capability="structured_output", supported=ok,
            evidence=CapabilityEvidence.MEASURED,
            detail="the answer parsed as JSON matching the schema" if ok
            else "valid JSON, but not the requested shape",
        )

    async def _probe_prompt_caching(self, alias: str) -> CapabilityProbeOutcome:
        """Send a cacheable prompt and look for cache counters in `usage`.

        THE TRI-STATE EARNS ITS KEEP HERE. Providers impose a MINIMUM
        cacheable prompt length (about a thousand tokens on Anthropic), and a
        probe cheap enough to run per model does not reach it. So:
          - the parameter rejected  -> False, the model cannot ;
          - counters come back      -> True, measured beyond doubt ;
          - accepted, no counters   -> None. It may cache and this prompt was
            simply too short. Recording False there would assert something
            the probe never established.
        """
        assert self._completer is not None
        try:
            # Built through `model_validate` rather than the constructor:
            # `cache_control` is a PROVIDER PASSTHROUGH, not a field of the
            # platform's request contract. `ChatCompletionRequest` is
            # `extra="allow"` precisely so such fields reach the provider
            # verbatim, and going through validation keeps that explicit
            # instead of asserting a keyword the contract never declared.
            resp = await self._completer.chat_completion(
                ChatCompletionRequest.model_validate({
                    "model": alias,
                    "messages": [
                        {"role": "system", "content": _CACHEABLE_SYSTEM_PROMPT},
                        {"role": "user", "content": _PROBE_PROMPT},
                    ],
                    "max_tokens": _PROBE_MAX_TOKENS,
                    "cache_control": {"type": "ephemeral"},
                }),
                agent_name="probe", session_id=f"probe:{alias}:cache",
            )
        except Exception as exc:
            return CapabilityProbeOutcome(
                capability="prompt_caching", supported=False,
                evidence=CapabilityEvidence.MEASURED, detail=str(exc),
            )
        counters = _cache_counters(resp)
        if counters:
            return CapabilityProbeOutcome(
                capability="prompt_caching", supported=True,
                evidence=CapabilityEvidence.MEASURED,
                detail=f"usage reported cache counters: {', '.join(counters)}",
            )
        return CapabilityProbeOutcome(
            capability="prompt_caching", supported=None,
            evidence=CapabilityEvidence.MEASURED,
            detail=(
                "cache_control was accepted but usage reported no cache "
                "counters — the probe prompt is likely below the provider's "
                "minimum cacheable length, so this is undetermined, not a no"
            ),
        )

    async def _probe_streaming(self, alias: str) -> CapabilityProbeOutcome:
        """Open the production streaming path and require a real chunk.

        Uses `chat_completion_stream`, the same call C3 serves chat with, so
        a model that streams here streams there. One chunk is enough: the
        question is whether the transport works at all, not how fast.
        """
        assert self._completer is not None
        try:
            async with self._completer.chat_completion_stream(
                ChatCompletionRequest(
                    model=alias,
                    messages=[ChatMessage(role=ChatRole.USER, content=_PROBE_PROMPT)],
                    max_tokens=_PROBE_MAX_TOKENS,
                ),
                agent_name="probe", session_id=f"probe:{alias}:stream",
            ) as chunks:
                async for _chunk in chunks:
                    return CapabilityProbeOutcome(
                        capability="streaming", supported=True,
                        evidence=CapabilityEvidence.MEASURED,
                        detail="a stream chunk was received",
                    )
        except Exception as exc:
            return CapabilityProbeOutcome(
                capability="streaming", supported=False,
                evidence=CapabilityEvidence.MEASURED, detail=str(exc),
            )
        return CapabilityProbeOutcome(
            capability="streaming", supported=False,
            evidence=CapabilityEvidence.MEASURED,
            detail="the stream opened but closed without a single chunk",
        )

    async def _probe_parallel_tool_calls(self, alias: str) -> CapabilityProbeOutcome:
        """Offer two tools, ask for both, count what comes back.

        Distinct from `tool_calling`: a model may call one tool per turn and
        still be perfectly capable. Only two-or-more in ONE response proves
        the round-trip saving the OpenHands engine is after.
        """
        assert self._completer is not None
        try:
            resp = await self._completer.chat_completion(
                ChatCompletionRequest(
                    model=alias,
                    messages=[ChatMessage(
                        role=ChatRole.USER,
                        content="Call BOTH ay_probe_ping and ay_probe_pong now.",
                    )],
                    max_tokens=_TOOL_PROBE_MAX_TOKENS,
                    tools=[_PROBE_TOOL, _PROBE_TOOL_SECOND],
                ),
                agent_name="probe", session_id=f"probe:{alias}:paralleltools",
            )
        except Exception as exc:
            return CapabilityProbeOutcome(
                capability="parallel_tool_calls", supported=False,
                evidence=CapabilityEvidence.MEASURED, detail=str(exc),
            )
        count = _count_tool_calls(resp)
        if count >= 2:
            return CapabilityProbeOutcome(
                capability="parallel_tool_calls", supported=True,
                evidence=CapabilityEvidence.MEASURED,
                detail=f"{count} tool calls came back in one response",
            )
        if count == 1:
            return CapabilityProbeOutcome(
                capability="parallel_tool_calls", supported=None,
                evidence=CapabilityEvidence.MEASURED,
                detail=(
                    "exactly one tool call came back — the model may be "
                    "capable and have chosen to call them in sequence, so "
                    "this is undetermined rather than a no"
                ),
            )
        return CapabilityProbeOutcome(
            capability="parallel_tool_calls", supported=False,
            evidence=CapabilityEvidence.MEASURED,
            detail="no tool call came back at all",
        )

    async def _probe_max_output_tokens(self, alias: str) -> CapabilityProbeOutcome:
        """Learn the ceiling from the provider's REFUSAL, not by generating.

        Binary-searching the limit would mean several billed completions per
        model. Asking for an absurd `max_tokens` instead costs nothing: the
        provider validates the parameter and rejects the request BEFORE
        generating a single token, and its error names the true maximum.
        When the error carries no number — or the request is accepted — the
        value stays undetermined rather than guessed.
        """
        assert self._completer is not None
        try:
            await self._completer.chat_completion(
                ChatCompletionRequest(
                    model=alias,
                    messages=[ChatMessage(role=ChatRole.USER, content=_PROBE_PROMPT)],
                    max_tokens=_ABSURD_MAX_TOKENS,
                ),
                agent_name="probe", session_id=f"probe:{alias}:maxout",
            )
        except Exception as exc:
            ceiling = _largest_number_in(str(exc))
            if ceiling is not None and ceiling < _ABSURD_MAX_TOKENS:
                return CapabilityProbeOutcome(
                    capability="max_output_tokens", supported=True,
                    evidence=CapabilityEvidence.MEASURED,
                    measured_value=ceiling,
                    detail=f"the provider refused and named its ceiling: {ceiling}",
                )
            return CapabilityProbeOutcome(
                capability="max_output_tokens", supported=None,
                evidence=CapabilityEvidence.MEASURED,
                detail=f"refused without naming a ceiling: {exc}",
            )
        return CapabilityProbeOutcome(
            capability="max_output_tokens", supported=None,
            evidence=CapabilityEvidence.MEASURED,
            detail=(
                f"the provider accepted max_tokens={_ABSURD_MAX_TOKENS} "
                "without complaint, so no ceiling could be read from it"
            ),
        )


def _messages_of(resp: Any) -> list[Any]:
    """Every choice's message, across the shapes the gateway hands back.

    The gateway may return a parsed model or a raw mapping depending on the
    path taken, so each accessor is tried both ways. Factored out because
    four probes now walk the same structure and a divergence between them
    would be a silent, capability-specific bug.
    """
    choices = getattr(resp, "choices", None)
    if choices is None and isinstance(resp, dict):
        choices = resp.get("choices")
    out: list[Any] = []
    for choice in choices or []:
        message = getattr(choice, "message", None)
        if message is None and isinstance(choice, dict):
            message = choice.get("message")
        if message is not None:
            out.append(message)
    return out


def _count_tool_calls(resp: Any) -> int:
    """How many tool calls the response carries, summed over choices."""
    total = 0
    for message in _messages_of(resp):
        calls = getattr(message, "tool_calls", None)
        if calls is None and isinstance(message, dict):
            calls = message.get("tool_calls")
        total += len(calls or [])
    return total


def _first_content(resp: Any) -> str | None:
    """The first textual content in the response, or None.

    A message whose `content` is a list (the multimodal shape) or absent
    yields None rather than a stringified structure — a probe that parsed
    `"[{'type': 'text'...}]"` as JSON would report nonsense.
    """
    for message in _messages_of(resp):
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
    return None


def _cache_counters(resp: Any) -> list[str]:
    """Names of cache-related counters present in `usage`.

    Providers spell these differently (`cache_read_input_tokens`,
    `cache_creation_input_tokens`, `cached_tokens`, …), so the match is on
    the substring `cache` rather than on an enumerated list that would go
    stale the first time a provider renamed one. Only counters with a
    NON-ZERO value count: a field reported as 0 proves the shape exists, not
    that anything was cached.
    """
    usage = getattr(resp, "usage", None)
    if usage is None and isinstance(resp, dict):
        usage = resp.get("usage")
    if usage is None:
        return []
    fields: dict[str, Any]
    if isinstance(usage, dict):
        fields = usage
    else:
        dumped = getattr(usage, "model_dump", None)
        fields = dumped() if callable(dumped) else dict(vars(usage))
    return sorted(
        f"{k}={v}" for k, v in fields.items()
        if "cache" in k.lower() and isinstance(v, int) and v > 0
    )


def _largest_number_in(text: str) -> int | None:
    """The largest integer appearing in an error message, or None.

    Providers word the ceiling differently ("max_tokens: must be <= 8192",
    "maximum of 64000 output tokens"), so the number is extracted rather
    than pattern-matched on any one phrasing. The LARGEST is taken because
    such messages usually also echo the offending value — and the caller
    discards anything not strictly below what it asked for, which rejects
    that echo.
    """
    numbers = [int(n) for n in re.findall(r"\d+", text)]
    return max(numbers) if numbers else None


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
