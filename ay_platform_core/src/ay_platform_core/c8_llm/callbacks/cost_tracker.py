# =============================================================================
# File: cost_tracker.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c8_llm/callbacks/cost_tracker.py
# Description: Cost-record construction + persistence for the `llm_calls`
#              ledger (E-800-002 / R-800-070). Two entry points share the
#              same pure helpers (tag extraction, provider inference, cost
#              formula) :
#                - `CostTrackerCallback.handle_post_call` : the LiteLLM
#                  native post-call hook shape (kept for a future
#                  proxy-direct wiring).
#                - `build_call_record` : builds a CallRecord from the
#                  `CostCallEnvelope` the cost forwarder POSTs to the C8
#                  cost receiver (the deployed path, §4.5 — the
#                  off-the-shelf proxy stays unmodified).
#              Storage is an injectable `CallRecordSink` (ArangoDB in
#              prod, in-memory in tests).
#
#              v2 (2026-05-22): `build_call_record(envelope, catalog)` for
#              the forwarder→receiver path.
#
# @relation implements:R-800-070
# @relation implements:R-800-071
# =============================================================================

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable

from ay_platform_core.c8_llm.config import ModelInfo
from ay_platform_core.c8_llm.cost import compute_cost
from ay_platform_core.c8_llm.models import CallRecord, CallTags, CostCallEnvelope


@runtime_checkable
class CallRecordSink(Protocol):
    """Destination for persisted call records.

    Production implementation is an ArangoDB repository wrapper; tests use
    an in-memory list. Kept deliberately minimal so LiteLLM's container
    doesn't need to import heavyweight platform dependencies.
    """

    async def insert(self, record: CallRecord) -> None: ...


class CostTrackerCallback:
    """Wires a LiteLLM post-call hook to a `CallRecordSink`.

    Instantiation receives:
      - `sink`: where the record goes
      - `model_catalog`: map of resolved model name → ModelInfo, used to
        apply the normative cost formula (Appendix 8.2).

    Exposed lifecycle hook is `handle_post_call()` matching LiteLLM's
    `post_call_hook` signature (positional args: request_data, response,
    start_time, end_time). Any additional kwargs LiteLLM passes are
    accepted to preserve forward compatibility.
    """

    def __init__(
        self,
        sink: CallRecordSink,
        model_catalog: dict[str, ModelInfo],
    ) -> None:
        self._sink = sink
        self._catalog = model_catalog

    async def handle_post_call(
        self,
        request_data: dict[str, Any],
        response: dict[str, Any],
        start_time: datetime,
        end_time: datetime,
        **_extra: Any,
    ) -> None:
        tags = _extract_tags(request_data)
        model_name = str(response.get("model") or request_data.get("model") or "unknown")
        provider = _provider_of(model_name)

        usage = response.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens", 0))
        output_tokens = int(usage.get("completion_tokens", 0))
        cached_tokens = int(usage.get("cached_tokens", 0))

        model_info = self._catalog.get(model_name)
        cost_usd = 0.0
        if model_info is not None:
            cost_usd = compute_cost(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                model_info=model_info,
            ).total_usd

        record = CallRecord(
            call_id=str(uuid.uuid4()),
            timestamp_start=start_time.astimezone(UTC),
            timestamp_end=end_time.astimezone(UTC),
            provider=provider,
            model=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            cost_usd=cost_usd,
            latency_ms=int((end_time - start_time).total_seconds() * 1000),
            status="success",
            tags=tags,
            request_fingerprint=_fingerprint(request_data),
        )
        await self._sink.insert(record)

    async def handle_failure(
        self,
        request_data: dict[str, Any],
        error: Exception,
        start_time: datetime,
        end_time: datetime,
        **_extra: Any,
    ) -> None:
        """Record a failed call so cost analytics see the full picture
        (failures often correlate with retries and degraded modes)."""
        tags = _extract_tags(request_data)
        model_name = str(request_data.get("model") or "unknown")
        provider = _provider_of(model_name)
        record = CallRecord(
            call_id=str(uuid.uuid4()),
            timestamp_start=start_time.astimezone(UTC),
            timestamp_end=end_time.astimezone(UTC),
            provider=provider,
            model=model_name,
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            cost_usd=0.0,
            latency_ms=int((end_time - start_time).total_seconds() * 1000),
            status="failure",
            error_code=type(error).__name__,
            error_message=str(error)[:500],
            tags=tags,
            request_fingerprint=_fingerprint(request_data),
        )
        await self._sink.insert(record)


def build_call_record(
    envelope: CostCallEnvelope,
    catalog: dict[str, ModelInfo],
) -> CallRecord:
    """Build a `CallRecord` from a forwarder envelope + the model catalog
    (R-800-070). Reuses the same tag-extraction, provider-inference and
    cost formula as the LiteLLM hook. Cost is 0 when the model is absent
    from the catalog (graceful — the call is still recorded). `cached`
    is clamped to `input` so a provider over-report can't break the
    cost-formula invariant."""
    tags = _extract_tags({"proxy_server_request": {"headers": dict(envelope.headers)}})
    usage = envelope.usage
    input_tokens = int(usage.get("prompt_tokens", 0))
    output_tokens = int(usage.get("completion_tokens", 0))
    cached_tokens = min(int(usage.get("cached_tokens", 0)), input_tokens)

    model_info = catalog.get(envelope.model)
    cost_usd = (
        compute_cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            model_info=model_info,
        ).total_usd
        if model_info is not None
        else 0.0
    )
    status: Literal["success", "failure"] = (
        "failure" if envelope.status == "failure" else "success"
    )
    return CallRecord(
        call_id=str(uuid.uuid4()),
        timestamp_start=envelope.start_time.astimezone(UTC),
        timestamp_end=envelope.end_time.astimezone(UTC),
        provider=_provider_of(envelope.model),
        model=envelope.model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        cost_usd=cost_usd,
        latency_ms=int(
            (envelope.end_time - envelope.start_time).total_seconds() * 1000,
        ),
        status=status,
        error_code=envelope.error_code,
        error_message=envelope.error_message,
        tags=tags,
        request_fingerprint=envelope.fingerprint or "sha256:unknown",
    )


# ---------------------------------------------------------------------------
# Helpers — pure, no I/O
# ---------------------------------------------------------------------------


def _extract_tags(request_data: dict[str, Any]) -> CallTags:
    """Read `proxy_server_request.headers` (LiteLLM passes headers here) and
    project them onto the CallTags contract."""
    headers_envelope = (
        request_data.get("proxy_server_request", {}).get("headers", {})
        if isinstance(request_data.get("proxy_server_request"), dict)
        else {}
    )
    # Fall back to direct kwargs if LiteLLM surfaces headers differently
    # across versions.
    raw_metadata = request_data.get("metadata", {})
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}

    def _pick(*names: str) -> str | None:
        for name in names:
            for source in (headers_envelope, metadata):
                if name in source:
                    return str(source[name])
                lower = name.lower()
                if lower in source:
                    return str(source[lower])
        return None

    return CallTags(
        tenant_id=_pick("X-Tenant-Id", "tenant_id") or "unknown",
        project_id=_pick("X-Project-Id", "project_id"),
        user_id=_pick("X-User-Id", "user_id"),
        session_id=_pick("X-Session-Id", "session_id") or "unknown",
        agent_name=_pick("X-Agent-Name", "agent_name") or "unknown",
        phase=_pick("X-Phase", "phase"),
        sub_agent_id=_pick("X-Sub-Agent-Id", "sub_agent_id"),
    )


def _provider_of(model_name: str) -> str:
    """Best-effort provider extraction from a LiteLLM `<provider>/<model>` id.

    Falls back to 'unknown' when the model is declared without a provider
    prefix (e.g. short aliases defined in `model_list`).
    """
    if "/" in model_name:
        return model_name.split("/", 1)[0]
    # Heuristics for common proxy aliases (conservative list)
    lowered = model_name.lower()
    if lowered.startswith("claude"):
        return "anthropic"
    if lowered.startswith("gpt") or lowered.startswith("o1") or lowered.startswith("o3"):
        return "openai"
    if lowered.startswith("gemini"):
        return "google"
    return "unknown"


def _fingerprint(request_data: dict[str, Any]) -> str:
    """Deterministic sha256 over the salient request fields (R-800-070).

    Caching-sensitive fields (model, messages, tools, temperature,
    max_tokens, response_format) are included; non-determinism sources
    (stream flag, user tags) are excluded.
    """
    keys_included = ("model", "messages", "tools", "temperature", "max_tokens", "response_format")
    projection = {k: request_data.get(k) for k in keys_included if k in request_data}
    serialised = json.dumps(projection, sort_keys=True, default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(serialised).hexdigest()
