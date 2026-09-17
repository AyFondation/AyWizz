# =============================================================================
# File: probe_models.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/probe_models.py
# Description: Response contracts for the provider and model probes
#              (R-800-150, R-800-151, R-800-152).
#
#              TWO RULES SHAPE EVERY TYPE HERE.
#
#              1. NO SECRET LEAVES. These objects are serialised to an operator
#              browser. They carry the composed URL, the upstream status and
#              the upstream error body — never a key, never an Authorization
#              header. The provider's existing `api_key_hint` is the only
#              credential-adjacent field the HMI ever sees.
#
#              2. SAY WHAT WAS OBSERVED, NOT WHAT IT MEANS. A probe reports the
#              URL it called, the code it got and the body it got back. It does
#              not summarise them into a green tick and discard the evidence:
#              on 2026-09-09 a provider outage surfaced to end users as an
#              EMPTY error message precisely because the upstream body was
#              swallowed somewhere between the provider and the screen.
# =============================================================================

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ay_platform_core.c8_llm.registry.models import CapabilityEvidence


class ProbeOutcome(StrEnum):
    """Why a probe ended the way it did.

    UNREACHABLE and REJECTED are kept apart because they send the operator to
    different places: the first is DNS / TLS / routing / a wrong host, the
    second is a host that answered and said no — credentials, model id,
    entitlement."""

    OK = "ok"
    UNREACHABLE = "unreachable"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    NOT_CONFIGURED = "not_configured"


class ProviderProbeResult(BaseModel):
    """Verdict for `POST /admin/v1/llm/providers/{provider_id}/probe`.

    CONSUMES PROVIDER TOKENS. The probe reaches the provider through the
    platform's pipeline, whose unit of work is a completion — so it runs one
    minimal completion through one of the provider's models."""

    model_config = ConfigDict(extra="forbid")

    provider_id: str
    outcome: ProbeOutcome
    effective_url: str
    """The `api_base` the pipeline resolves for this provider — read from the
    same document `RegistryKeyProvider` reads, so it is the value actually in
    play rather than a second composition of our own.

    The probe no longer builds a URL and predicts the outcome; it runs the
    real call and reports what happened. That is strictly stronger for the
    2026-09-09 failure mode (a stored trailing slash yielding `//v1/messages`):
    the composition now happens where it happens in production, and a broken
    one shows up as a real upstream error instead of a URL we guessed."""
    status_code: int | None = None
    latency_ms: int | None = None
    error: str | None = None
    """Upstream body or transport error, verbatim and untruncated-in-meaning.
    An empty string is itself a finding and SHALL NOT be replaced with a
    friendlier invention."""
    api_key_hint: str = ""
    via_model_id: str | None = None
    via_alias: str | None = None
    """Which model carried the probe. Reported because the verdict is only as
    specific as the model used: a failure here may be the provider OR that one
    model, and the operator needs to know which one was tried."""


class CapabilityProbeOutcome(BaseModel):
    """Result of exercising ONE capability against a model."""

    model_config = ConfigDict(extra="forbid")

    capability: str
    supported: bool | None = None
    """`None` means undetermined — the probe could not establish it either
    way. Distinct from `False`, which means the model was asked and refused."""
    evidence: CapabilityEvidence
    detail: str | None = None
    """What was observed: the upstream refusal, or the shape that proved
    support (a tool_use block came back, the image was accepted)."""


class ModelProbeResult(BaseModel):
    """Verdict for `POST /admin/v1/llm/models/{model_id}/probe`.

    CONSUMES TOKENS. One minimal completion, plus one per capability probed."""

    model_config = ConfigDict(extra="forbid")

    model_id: str
    alias: str
    provider_id: str
    outcome: ProbeOutcome
    resolved_target: str
    """The `<wire_format>/<upstream_model>` actually sent to the proxy. A
    mismatch between what the operator typed and what the platform composed is
    a routing bug the HMI cannot otherwise show."""
    status_code: int | None = None
    latency_ms: int | None = None
    error: str | None = None
    capabilities: list[CapabilityProbeOutcome] = Field(default_factory=list)
    """Empty when capability probing was not requested. Each entry carries its
    own evidence, so a partially-probed model is representable — which it must
    be, since a provider can support tool calling and refuse vision."""
