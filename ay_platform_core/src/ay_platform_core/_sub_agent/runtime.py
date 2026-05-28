# =============================================================================
# File: runtime.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/_sub_agent/runtime.py
# Description: Sub-agent runtime — the body of the `python -m
#              ay_platform_core._sub_agent` entrypoint that runs INSIDE an
#              ephemeral K8s pod scheduled by the C4 K8sDispatcher
#              (R-200-030..033).
#
#              Lifecycle :
#                1. Load `SubAgentConfig` from env (bundle prefix, MinIO
#                   creds, C8 endpoint + bearer).
#                2. Download `manifest.json` from MinIO → TaskEnvelope.
#                3. Download each `context_entries[*]` blob ; assemble
#                   the user prompt with the bundle JSON + the context
#                   files inlined.
#                4. Invoke C8 chat completion via `LLMGatewayClient`
#                   (per-agent route resolver kicks in if configured).
#                5. Parse the response with the SAME tolerant envelope
#                   extractor as the in-process dispatcher (reuse keeps
#                   parity — same model output, same parse).
#                6. Serialise the resulting `AgentCompletion` + run-time
#                   metadata into `output/completion.json` on MinIO so
#                   the orchestrator's K8sDispatcher reads it back after
#                   the pod terminates.
#                7. Exit 0 ; non-zero exit on infrastructure failures
#                   (MinIO unreachable, manifest malformed, …) — the
#                   orchestrator's pod watcher distinguishes "pod
#                   crashed before producing completion" from "pod
#                   completed normally with a BLOCKED envelope".
#
# @relation implements:R-200-030
# @relation implements:R-200-031
# @relation implements:R-200-032
# @relation implements:R-200-033
# =============================================================================

from __future__ import annotations

import asyncio
import io
import json
import logging
import sys
import time
from datetime import UTC, datetime
from typing import Any

from ay_platform_core._sub_agent.config import SubAgentConfig
from ay_platform_core._sub_agent.models import (
    ContextBundleEntry,
    SubAgentRunReport,
    TaskEnvelope,
)
from ay_platform_core.c4_orchestrator.dispatcher.base import DispatchRequest
from ay_platform_core.c4_orchestrator.dispatcher.in_process import (
    _SYSTEM_PROMPTS,
    _blocked_completion,
    _build_user_prompt,
    _extract_assistant_text,
    _extract_envelope,
    _parse_completion,
)
from ay_platform_core.c4_orchestrator.models import (
    AgentBlocker,
    AgentCompletion,
    EscalationStatus,
)
from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.config import ClientSettings
from ay_platform_core.c8_llm.models import (
    ChatCompletionRequest,
    ChatMessage,
    ChatRole,
)

_log = logging.getLogger("sub_agent.runtime")


# ---------------------------------------------------------------------------
# MinIO I/O helpers — narrow surface, easy to mock in unit tests
# ---------------------------------------------------------------------------


def _make_minio_client(cfg: SubAgentConfig) -> Any:
    """Lazy `minio` import keeps the test path mock-friendly. The K8s
    pod has `minio` in its deps because the same `ay-api:local` image
    is shared with the orchestrator (`R-100-114`)."""
    from minio import Minio  # noqa: PLC0415

    return Minio(
        cfg.minio_endpoint,
        access_key=cfg.minio_access_key,
        secret_key=cfg.minio_secret_key,
        secure=cfg.minio_secure,
    )


def _download_object_text(minio_client: Any, bucket: str, key: str) -> str:
    """Read a MinIO object's bytes and decode as UTF-8. Wraps minio's
    response-close ceremony so callers don't leak connections."""
    response = minio_client.get_object(bucket, key)
    try:
        raw: bytes = response.read()
        return raw.decode("utf-8")
    finally:
        response.close()
        response.release_conn()


def _upload_object_text(
    minio_client: Any, bucket: str, key: str, data: str,
) -> None:
    payload = data.encode("utf-8")
    minio_client.put_object(
        bucket,
        key,
        io.BytesIO(payload),
        length=len(payload),
        content_type="application/json",
    )


# ---------------------------------------------------------------------------
# Prompt assembly + LLM call
# ---------------------------------------------------------------------------


def _load_envelope_from_bundle(
    minio_client: Any, cfg: SubAgentConfig,
) -> TaskEnvelope:
    raw = _download_object_text(
        minio_client, cfg.bundle_bucket, f"{cfg.bundle_prefix}manifest.json",
    )
    return TaskEnvelope.model_validate_json(raw)


def _load_context_entries(
    minio_client: Any, cfg: SubAgentConfig, entries: list[ContextBundleEntry],
) -> dict[str, str]:
    """Returns `{ relative_path: text_content }`. Read errors raise so
    the runtime can BLOCKED-fail the pod with a clear cause — partial
    context is not better than no context for an audit-grade sub-agent."""
    out: dict[str, str] = {}
    for entry in entries:
        key = f"{cfg.bundle_prefix}context/{entry.relative_path}"
        out[entry.relative_path] = _download_object_text(
            minio_client, cfg.bundle_bucket, key,
        )
    return out


def _build_user_prompt_with_context(
    envelope: TaskEnvelope, files: dict[str, str],
) -> str:
    """Same shape as in_process `_build_user_prompt` PLUS a `Context
    files` section so the sub-agent has explicit access to the bundle
    contents (R-200-033 audit trail)."""
    request = DispatchRequest(
        run_id=envelope.run_id,
        phase=envelope.phase,
        agent=envelope.agent,
        session_id=envelope.session_id,
        tenant_id=envelope.tenant_id,
        user_id=envelope.user_id,
        project_id=envelope.project_id,
        prompt=envelope.user_prompt,
        context_bundle=envelope.context_bundle,
    )
    base = _build_user_prompt(request)
    if not files:
        return base
    parts = [base, "\n\nContext files (read-only, audit-tracked) :"]
    for rel_path, body in files.items():
        parts.append(f"\n--- {rel_path} ---\n{body}\n")
    return "".join(parts)


async def _invoke_llm(
    envelope: TaskEnvelope,
    user_prompt: str,
    cfg: SubAgentConfig,
) -> AgentCompletion:
    """Make one chat completion against C8 with the same per-phase
    system prompt the in-process dispatcher uses, then parse the
    response with the SHARED helpers. Returns BLOCKED on any LLM error
    so the pod always writes a completion.json (the orchestrator can
    then count it as one fix attempt instead of "pod crashed")."""
    started = time.monotonic()
    # ClientSettings() reads C8_GATEWAY_URL / C8_DEFAULT_MODEL /
    # C8_AGENT_ROUTES_* AND the shared C8_GATEWAY_API_KEY from the pod
    # env — same single-source-of-truth rule the orchestrator uses
    # (R-100-110 v2). No per-sub-agent bearer ; `effective_bearer`
    # carries the "no-auth" placeholder for the mock/Ollama-direct path.
    sub_settings = ClientSettings()
    client = LLMGatewayClient(
        sub_settings, bearer_token=sub_settings.effective_bearer,
    )
    request = DispatchRequest(
        run_id=envelope.run_id,
        phase=envelope.phase,
        agent=envelope.agent,
        session_id=envelope.session_id,
        tenant_id=envelope.tenant_id,
        user_id=envelope.user_id,
        project_id=envelope.project_id,
        prompt=envelope.user_prompt,
        context_bundle=envelope.context_bundle,
    )
    try:
        response = await client.chat_completion(
            ChatCompletionRequest(
                messages=[
                    ChatMessage(
                        role=ChatRole.SYSTEM,
                        content=_SYSTEM_PROMPTS[envelope.phase],
                    ),
                    ChatMessage(role=ChatRole.USER, content=user_prompt),
                ],
                response_format={"type": "json_object"},
            ),
            agent_name=envelope.agent.value,
            session_id=envelope.session_id,
            tenant_id=envelope.tenant_id,
            project_id=envelope.project_id,
            phase=envelope.phase.value,
        )
    except Exception as exc:
        return AgentCompletion(
            agent=envelope.agent,
            run_id=envelope.run_id,
            phase=envelope.phase,
            status=EscalationStatus.BLOCKED,
            blocker=AgentBlocker(
                reason=f"LLM gateway error: {type(exc).__name__}: {exc}"[:500],
            ),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    finally:
        await client.aclose()

    duration_ms = int((time.monotonic() - started) * 1000)
    call_id = response.id or ""
    try:
        content = _extract_assistant_text(response.choices[0].message.content)
    except (IndexError, AttributeError) as exc:
        return _blocked_completion(
            request, duration_ms, call_id,
            f"agent response shape invalid: {exc}",
        )
    parsed = _extract_envelope(content)
    if parsed is None:
        return _blocked_completion(
            request, duration_ms, call_id,
            "agent response did not contain a parseable JSON envelope",
        )
    return _parse_completion(parsed, request, duration_ms, call_id)


# ---------------------------------------------------------------------------
# Public entry — orchestrator-facing surface
# ---------------------------------------------------------------------------


async def run_sub_agent(
    cfg: SubAgentConfig | None = None,
    *,
    minio_client_factory: Any = _make_minio_client,
) -> int:
    """Main runtime. Returns the process exit code : 0 on success
    (completion.json written, regardless of `status` value — BLOCKED
    is a valid completion), 1 on infrastructure failure (MinIO unreachable,
    manifest malformed, env missing)."""
    cfg = cfg or SubAgentConfig()
    try:
        cfg.validate_for_runtime()
    except ValueError as exc:
        _log.error("sub-agent config invalid: %s", exc)
        return 1

    started_at = datetime.now(UTC)
    try:
        minio_client = minio_client_factory(cfg)
        envelope = _load_envelope_from_bundle(minio_client, cfg)
        files = _load_context_entries(minio_client, cfg, envelope.context_entries)
        user_prompt = _build_user_prompt_with_context(envelope, files)
    except Exception as exc:  # bundle read failures = infra-fail-fast
        _log.exception("sub-agent bundle load failed: %s", exc)
        return 1

    completion = await _invoke_llm(envelope, user_prompt, cfg)
    finished_at = datetime.now(UTC)

    report = SubAgentRunReport(
        completion=completion.model_dump(mode="json"),
        started_at_iso=started_at.isoformat(),
        finished_at_iso=finished_at.isoformat(),
        llm_call_id=completion.llm_call_ids[0] if completion.llm_call_ids else None,
    )
    try:
        _upload_object_text(
            minio_client,
            cfg.bundle_bucket,
            f"{cfg.bundle_prefix}output/completion.json",
            json.dumps(report.model_dump(mode="json"), separators=(",", ":")),
        )
    except Exception as exc:
        _log.exception("sub-agent completion writeback failed: %s", exc)
        return 1

    _log.info(
        "sub-agent done : status=%s phase=%s agent=%s duration_ms=%s",
        completion.status.value, envelope.phase.value, envelope.agent.value,
        completion.duration_ms,
    )
    return 0


def main() -> int:
    """Sync wrapper for `python -m ay_platform_core._sub_agent`. Sets up
    basic logging then defers to the async runtime."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    return asyncio.run(run_sub_agent())
