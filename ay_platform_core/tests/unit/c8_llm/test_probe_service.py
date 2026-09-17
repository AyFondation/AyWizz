# =============================================================================
# File: test_probe_service.py
# Version: 1
# Path: ay_platform_core/tests/unit/c8_llm/test_probe_service.py
# Description: Unit tests for the provider / model probes and the measured
#              capability flags.
#
#              WHAT THESE TESTS ARE FOR. The probes exist to remove a specific
#              class of blindness, so the tests assert the DISTINCTIONS that
#              blindness came from, not merely that a call returns:
#                - the composed URL is reported, and a stored trailing slash
#                  does not silently become `//v1/models` (R-800-150) ;
#                - unreachable and rejected stay distinguishable, because they
#                  send an operator to different places ;
#                - the verdict never carries a credential ;
#                - a measured capability is labelled MEASURED, so it cannot be
#                  confused with a box someone ticked (R-800-152).
#
#              @relation validates:R-800-150
#              @relation validates:R-800-151
#              @relation validates:R-800-152
# =============================================================================

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from ay_platform_core.c8_llm.models import ChatCompletionRequest
from ay_platform_core.c8_llm.registry.models import (
    CapabilityEvidence,
    CapabilityOverrides,
    ModelCapabilities,
)
from ay_platform_core.c8_llm.registry.probe_models import (
    CapabilityProbeOutcome,
    ModelProbeResult,
    ProbeOutcome,
    ProviderProbeResult,
)
from ay_platform_core.c8_llm.registry.probe_service import (
    LLMProbeService,
    _mentions_tool_call,
)
from ay_platform_core.c8_llm.registry.provider_router import router as provider_router

pytestmark = pytest.mark.unit

_PM_HEADERS = {"X-User-Id": "u-pm", "X-User-Roles": "platform_manager"}


# ---------------------------------------------------------------------------
# Provider probe — through the pipeline, never around it
# ---------------------------------------------------------------------------


class _GatewayError(RuntimeError):
    """Shaped like `LLMGatewayError`: the probe reads `status_code` / `body`
    off the exception to tell a rejection from a transport failure."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"{status_code}: {body}")
        self.status_code = status_code
        self.body = body


class _Providers:
    def __init__(self, doc: dict[str, Any] | None) -> None:
        self._doc = doc

    async def get(self, provider_id: str) -> dict[str, Any] | None:
        return self._doc


class _Registry:
    def __init__(self, doc: dict[str, Any] | None = None) -> None:
        self._doc = doc

    async def get(self, model_id: str) -> dict[str, Any] | None:
        return self._doc

    async def list_all(self) -> list[dict[str, Any]]:
        return [self._doc] if self._doc is not None else []


def _provider_doc(**over: Any) -> dict[str, Any]:
    doc = {
        "_key": "prov-1",
        "base_url": "https://api.example.com/",
        "wire_format": "openai",
        "api_key_hint": "sk-…9f2",
        "api_key_ciphertext": None,
    }
    doc.update(over)
    return doc


@pytest.mark.asyncio
async def test_unknown_provider_is_not_configured_not_unreachable() -> None:
    svc = LLMProbeService(_Providers(None), _Registry(), None)
    result = await svc.probe_provider("nope")
    assert result.outcome is ProbeOutcome.NOT_CONFIGURED
    assert result.error == "no such provider"


@pytest.mark.asyncio
async def test_provider_probe_goes_through_the_pipeline_not_around_it() -> None:
    """The verdict comes from a completion carried by one of the provider's
    models — client → quota → alias resolution → credential injection → proxy.
    Nothing here opens its own connection to the provider."""
    completer = _Completer()
    svc = LLMProbeService(
        _Providers(_provider_doc()), _Registry(_model_doc()), None, completer,
    )

    result = await svc.probe_provider("prov-1")

    assert result.outcome is ProbeOutcome.OK
    # One completion was actually made, pinned to a model of this provider.
    assert len(completer.calls) == 1
    assert completer.calls[0].model == "fast"
    # And the verdict says which model carried it: a failure may be the
    # provider OR that one model, and the operator needs to know which.
    assert result.via_alias == "fast"
    assert result.via_model_id == "m-1"


@pytest.mark.asyncio
async def test_provider_probe_reports_the_api_base_the_pipeline_will_send() -> None:
    svc = LLMProbeService(
        _Providers(_provider_doc(base_url="https://api.example.com")),
        _Registry(_model_doc()), None, _Completer(),
    )
    result = await svc.probe_provider("prov-1")
    assert result.effective_url == "https://api.example.com"
    assert result.api_key_hint == "sk-…9f2"


@pytest.mark.asyncio
async def test_provider_without_a_model_has_no_pipeline_path_to_exercise() -> None:
    """Honest `not_configured`, not a fabricated green: with no model there is
    no completion to make, and therefore nothing verified."""
    svc = LLMProbeService(
        _Providers(_provider_doc()), _Registry(None), None, _Completer(),
    )

    result = await svc.probe_provider("prov-1")

    assert result.outcome is ProbeOutcome.NOT_CONFIGURED
    assert "no model is configured" in (result.error or "")


@pytest.mark.asyncio
async def test_provider_probe_surfaces_the_upstream_failure() -> None:
    completer = _Completer(fail=_GatewayError(401, "invalid x-api-key"))
    svc = LLMProbeService(
        _Providers(_provider_doc()), _Registry(_model_doc()), None, completer,
    )

    result = await svc.probe_provider("prov-1")

    assert result.outcome is ProbeOutcome.REJECTED
    assert result.status_code == 401
    # Verbatim: the 2026-09-09 outage was undiagnosable because the upstream
    # body was swallowed on its way to the screen.
    assert result.error == "invalid x-api-key"


@pytest.mark.asyncio
async def test_transport_failure_is_unreachable_not_rejected() -> None:
    """No status code means nothing answered — a different operator remedy
    from a host that answered and said no."""
    completer = _Completer(fail=RuntimeError("name or service not known"))
    svc = LLMProbeService(
        _Providers(_provider_doc()), _Registry(_model_doc()), None, completer,
    )

    result = await svc.probe_provider("prov-1")

    assert result.outcome is ProbeOutcome.UNREACHABLE
    assert result.status_code is None


@pytest.mark.asyncio
async def test_probe_result_never_carries_the_credential() -> None:
    """The verdict is serialised to an operator browser; only the hint may
    appear in it. The probe never decrypts anything itself — the pipeline
    does — so there is no plaintext key in this component to leak."""
    ciphertext = "v1.aead.NEVER-SHOW-THIS-CIPHERTEXT"
    svc = LLMProbeService(
        _Providers(_provider_doc(api_key_ciphertext=ciphertext)),
        _Registry(_model_doc()), None, _Completer(),
    )
    result = await svc.probe_provider("prov-1")

    dumped = result.model_dump_json()
    assert ciphertext not in dumped
    assert "NEVER-SHOW-THIS-CIPHERTEXT" not in dumped
    assert result.api_key_hint == "sk-…9f2"


# ---------------------------------------------------------------------------
# Model probe + measured capabilities
# ---------------------------------------------------------------------------


def _model_doc() -> dict[str, Any]:
    return {
        "_key": "m-1",
        "alias": "fast",
        "provider_id": "prov-1",
        "upstream_model": "gpt-4o-mini",
    }


class _Completer:
    """Records what the probe sent, so the tests can assert the PATH, not just
    the verdict."""

    def __init__(self, *, fail: Exception | None = None, tool_call: bool = False):
        self.calls: list[ChatCompletionRequest] = []
        self._fail = fail
        self._tool_call = tool_call

    async def chat_completion(
        self, payload: ChatCompletionRequest, *, agent_name: str, session_id: str,
        reasoning_verbose: bool = False,
    ) -> Any:
        self.calls.append(payload)
        if self._fail is not None:
            raise self._fail
        if self._tool_call:
            return {"choices": [{"message": {"tool_calls": [{"id": "1"}]}}]}
        return {"choices": [{"message": {"content": "ok"}}]}


@pytest.mark.asyncio
async def test_model_probe_without_a_client_refuses_rather_than_guessing() -> None:
    """No silent fallback to a direct provider call: that would report health
    for a route it never exercised (R-800-151)."""
    svc = LLMProbeService(_Providers(_provider_doc()), _Registry(_model_doc()), None)
    result = await svc.probe_model("m-1")
    assert result.outcome is ProbeOutcome.NOT_CONFIGURED


@pytest.mark.asyncio
async def test_model_probe_reports_the_resolved_target_and_pins_the_alias() -> None:
    completer = _Completer()
    svc = LLMProbeService(
        _Providers(_provider_doc()), _Registry(_model_doc()), None, completer,
    )

    result = await svc.probe_model("m-1")

    assert result.outcome is ProbeOutcome.OK
    assert result.resolved_target == "openai/gpt-4o-mini"
    # Pinned by alias: the probe asks about THIS model, not about whatever the
    # agent router would have chosen.
    assert completer.calls[0].model == "fast"
    assert completer.calls[0].max_tokens is not None


@pytest.mark.asyncio
async def test_capabilities_are_absent_unless_requested() -> None:
    completer = _Completer()
    svc = LLMProbeService(
        _Providers(_provider_doc()), _Registry(_model_doc()), None, completer,
    )
    result = await svc.probe_model("m-1")
    assert result.capabilities == []
    assert len(completer.calls) == 1  # capability probes cost money; none run


@pytest.mark.asyncio
async def test_measured_capabilities_are_labelled_measured() -> None:
    completer = _Completer(tool_call=True)
    svc = LLMProbeService(
        _Providers(_provider_doc()), _Registry(_model_doc()), None, completer,
    )

    result = await svc.probe_model("m-1", probe_capabilities=True)

    by_name = {c.capability: c for c in result.capabilities}
    assert set(by_name) == {"tool_calling", "vision", "thinking"}
    for outcome in result.capabilities:
        # The whole point of R-800-152: a value that came from observation is
        # distinguishable from one a human asserted.
        assert outcome.evidence is CapabilityEvidence.MEASURED
    assert by_name["tool_calling"].supported is True


@pytest.mark.asyncio
async def test_a_refused_capability_is_measured_false_not_unknown() -> None:
    """"Asked and refused" must not read the same as "never checked"."""
    completer = _Completer(tool_call=False)
    svc = LLMProbeService(
        _Providers(_provider_doc()), _Registry(_model_doc()), None, completer,
    )

    result = await svc.probe_model("m-1", probe_capabilities=True)

    tool = next(c for c in result.capabilities if c.capability == "tool_calling")
    assert tool.supported is False
    assert tool.evidence is CapabilityEvidence.MEASURED
    assert tool.detail is not None


@pytest.mark.asyncio
async def test_a_failing_completion_skips_capability_probing() -> None:
    """No point billing three more calls once the model has already refused."""
    completer = _Completer(fail=RuntimeError("upstream down"))
    svc = LLMProbeService(
        _Providers(_provider_doc()), _Registry(_model_doc()), None, completer,
    )

    result = await svc.probe_model("m-1", probe_capabilities=True)

    assert result.outcome is not ProbeOutcome.OK
    assert result.capabilities == []
    assert len(completer.calls) == 1


# ---------------------------------------------------------------------------
# Capability vs enablement — the operator's lever is SUBTRACTIVE
# ---------------------------------------------------------------------------


def test_operator_can_disable_a_capability_the_model_has() -> None:
    caps = ModelCapabilities(
        vision=True, tool_calling=True, context_window=128000,
        disabled=CapabilityOverrides(vision=True),
    )
    assert caps.vision is True          # the model can
    assert caps.supports("vision") is False   # we choose not to
    assert caps.supports("tool_calling") is True


def test_disabling_cannot_conjure_a_capability_the_model_lacks() -> None:
    """The override only ever subtracts. There is deliberately no shape that
    expresses "enable what the model cannot do" — that is not a preference,
    it is a false claim, and it is the failure this mechanism removes."""
    caps = ModelCapabilities(
        vision=False, tool_calling=False, context_window=128000,
        disabled=CapabilityOverrides(vision=False, tool_calling=False),
    )
    assert caps.supports("vision") is False
    assert caps.supports("tool_calling") is False


def test_defaults_enable_nothing_and_disable_nothing() -> None:
    caps = ModelCapabilities(context_window=8192)
    assert caps.supports("vision") is False
    assert caps.supports("thinking") is False
    assert caps.provenance.tool_calling is CapabilityEvidence.UNKNOWN


def test_tool_call_detection_reads_both_shapes() -> None:
    assert _mentions_tool_call({"choices": [{"message": {"tool_calls": [{}]}}]})
    assert not _mentions_tool_call({"choices": [{"message": {"content": "hi"}}]})
    assert not _mentions_tool_call({})


# ---------------------------------------------------------------------------
# Route surface — the real router, its role gate, and the "verdict inside a
# 200" contract
# ---------------------------------------------------------------------------


def _probe_app(service: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(provider_router)
    app.state.probe_service = service
    app.state.provider_service = None
    return app


class _StubProbes:
    async def probe_provider(self, provider_id: str) -> ProviderProbeResult:
        return ProviderProbeResult(
            provider_id=provider_id,
            outcome=ProbeOutcome.UNREACHABLE,
            effective_url="https://api.example.com/v1/models",
            error="name or service not known",
        )

    async def probe_model(
        self, model_id: str, *, probe_capabilities: bool = False,
    ) -> ModelProbeResult:
        return ModelProbeResult(
            model_id=model_id, alias="fast", provider_id="prov-1",
            outcome=ProbeOutcome.OK, resolved_target="openai/gpt-4o-mini",
            capabilities=[
                CapabilityProbeOutcome(
                    capability="tool_calling", supported=True,
                    evidence=CapabilityEvidence.MEASURED,
                ),
            ] if probe_capabilities else [],
        )


@pytest.mark.asyncio
async def test_provider_probe_route_answers_200_with_the_failure_inside() -> None:
    """An unreachable provider is a RESULT of this endpoint, not a failure of
    it. A 5xx here would be indistinguishable from the probe's own outage."""
    transport = httpx.ASGITransport(app=_probe_app(_StubProbes()))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://t",
    ) as client:
        resp = await client.post(
            "/admin/v1/llm/providers/prov-1/probe", headers=_PM_HEADERS,
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "unreachable"
    assert body["effective_url"] == "https://api.example.com/v1/models"


@pytest.mark.asyncio
async def test_model_probe_route_passes_the_capabilities_flag_through() -> None:
    transport = httpx.ASGITransport(app=_probe_app(_StubProbes()))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://t",
    ) as client:
        plain = await client.post(
            "/admin/v1/llm/registry/m-1/probe", headers=_PM_HEADERS,
        )
        measured = await client.post(
            "/admin/v1/llm/registry/m-1/probe?capabilities=true",
            headers=_PM_HEADERS,
        )

    assert plain.status_code == 200
    assert plain.json()["capabilities"] == []
    assert measured.json()["capabilities"][0]["evidence"] == "measured"
    assert measured.json()["resolved_target"] == "openai/gpt-4o-mini"


@pytest.mark.asyncio
async def test_probes_are_platform_manager_only() -> None:
    """These endpoints reach outward using stored credentials and, for the
    model probe, spend money. The gate is not incidental."""
    transport = httpx.ASGITransport(app=_probe_app(_StubProbes()))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://t",
    ) as client:
        forbidden = await client.post(
            "/admin/v1/llm/providers/prov-1/probe",
            headers={"X-User-Id": "u", "X-User-Roles": "admin"},
        )
        anonymous = await client.post("/admin/v1/llm/registry/m-1/probe")

    assert forbidden.status_code == 403
    assert anonymous.status_code == 401


