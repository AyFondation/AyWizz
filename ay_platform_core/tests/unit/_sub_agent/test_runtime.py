# =============================================================================
# File: test_runtime.py
# Version: 2
# Path: ay_platform_core/tests/unit/_sub_agent/test_runtime.py
# Description: Unit tests for the sub-agent runtime (P2.1.a) — exercises
#              bundle-load + LLM-call + completion-writeback against a
#              fake MinIO client (in-memory dict) and a fake C8 ASGI app
#              (the same pattern the in-process dispatcher uses).
#
# @relation validates:R-200-030
# @relation validates:R-200-033
# =============================================================================

from __future__ import annotations

import io
import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException, Request

import ay_platform_core._sub_agent.runtime as _rt
from ay_platform_core._sub_agent.config import SubAgentConfig
from ay_platform_core._sub_agent.models import (
    ContextBundleEntry,
    SubAgentRunReport,
    TaskEnvelope,
)
from ay_platform_core._sub_agent.runtime import (
    _build_user_prompt_with_context,
    _invoke_llm,
    run_sub_agent,
)
from ay_platform_core.c4_orchestrator.models import (
    AgentRole,
    EscalationStatus,
    Phase,
)
from ay_platform_core.c8_llm.client import LLMGatewayClient as RealLLMClient

# ---------------------------------------------------------------------------
# In-memory fake MinIO client — covers the narrow surface the runtime uses.
# ---------------------------------------------------------------------------


class _FakeMinioResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        pass

    def release_conn(self) -> None:
        pass


class _FakeMinio:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], bytes] = {}

    def put(self, bucket: str, key: str, data: bytes) -> None:
        self.store[(bucket, key)] = data

    def get_object(self, bucket: str, key: str) -> _FakeMinioResponse:
        if (bucket, key) not in self.store:
            raise FileNotFoundError(f"{bucket}/{key}")
        return _FakeMinioResponse(self.store[(bucket, key)])

    def put_object(
        self,
        bucket: str,
        key: str,
        data: io.BytesIO,
        length: int,
        content_type: str = "application/octet-stream",
    ) -> None:
        self.store[(bucket, key)] = data.read()


# ---------------------------------------------------------------------------
# Scripted LLM ASGI app — returns the configured envelope verbatim.
# ---------------------------------------------------------------------------


def _make_scripted_llm_app(envelope_json: str) -> FastAPI:
    """`/v1/chat/completions` because LLMGatewayClient hits
    `/chat/completions` relative to `base_url="…/v1"` ; httpx appends
    the relative path under the base path. Same shape as
    `tests/integration/c8_llm/_build_mock_app`."""
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def completions(  # type: ignore[no-untyped-def]
        request: Request,
        x_agent_name: str | None = Header(default=None),
        x_session_id: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ):
        if not authorization:
            raise HTTPException(status_code=401, detail="bearer required")
        if not x_agent_name or not x_session_id:
            raise HTTPException(status_code=400, detail="missing tags")
        body = await request.json()
        return {
            "id": "mock-1",
            "object": "chat.completion",
            "created": 1_700_000_000,
            "model": body.get("model") or "mock-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": envelope_json},
                    "finish_reason": "stop",
                },
            ],
            "usage": {
                "prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8,
            },
        }

    return app


def _patch_llm_client_with_transport(
    monkeypatch: pytest.MonkeyPatch, http_client: httpx.AsyncClient,
) -> None:
    """Monkey-patch `_rt.LLMGatewayClient` to inject our ASGI-backed
    httpx client. Done as a tiny subclass that always forces the
    closure-captured `http_client` through to the real constructor."""

    class _Patched(RealLLMClient):
        def __init__(self, settings: Any, **kwargs: Any) -> None:
            kwargs.pop("http_client", None)
            super().__init__(settings, http_client=http_client, **kwargs)

    monkeypatch.setattr(_rt, "LLMGatewayClient", _Patched)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_minio() -> _FakeMinio:
    return _FakeMinio()


def _seed_bundle(
    fake: _FakeMinio,
    *,
    bucket: str = "orchestrator",
    prefix: str = "c4-dispatch/run-1/sub-1/",
    envelope: TaskEnvelope | None = None,
    context_files: dict[str, str] | None = None,
) -> TaskEnvelope:
    env = envelope or TaskEnvelope(
        run_id="run-1",
        sub_agent_id="sub-1",
        project_id="p-1",
        tenant_id="t-1",
        session_id="s-1",
        user_id="u-1",
        phase=Phase.PLAN,
        agent=AgentRole.PLANNER,
        user_prompt="Plan the refactor.",
        context_bundle={"hint": "split modules"},
        context_entries=[
            ContextBundleEntry(relative_path=k, purpose=f"sample-{k}")
            for k in (context_files or {})
        ],
    )
    fake.put(bucket, f"{prefix}manifest.json", env.model_dump_json().encode("utf-8"))
    for rel_path, body in (context_files or {}).items():
        fake.put(bucket, f"{prefix}context/{rel_path}", body.encode("utf-8"))
    return env


def _cfg(
    prefix: str = "c4-dispatch/run-1/sub-1/",
) -> SubAgentConfig:
    return SubAgentConfig(
        bundle_bucket="orchestrator",
        bundle_prefix=prefix,
        minio_endpoint="minio:9000",
        minio_access_key="ay_app",
        minio_secret_key="changeme",
    )


def _llm_settings(gateway: str = "http://mock/v1") -> tuple[str, str]:
    """Return `(env_C8_GATEWAY_URL, env_C8_DEFAULT_MODEL)` to inject
    via monkeypatch so the runtime's `ClientSettings()` picks them up."""
    return gateway, "mock-model"


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


class TestBuildUserPromptWithContext:
    def test_includes_context_files_section(self) -> None:
        env = TaskEnvelope(
            run_id="r1", sub_agent_id="s1", project_id="p", tenant_id="t",
            session_id="ses", user_id="u", phase=Phase.SPEC,
            agent=AgentRole.ARCHITECT, user_prompt="hi",
            context_entries=[ContextBundleEntry(relative_path="notes.md")],
        )
        out = _build_user_prompt_with_context(env, {"notes.md": "alpha beta"})
        assert "notes.md" in out
        assert "alpha beta" in out
        assert "Context files" in out

    def test_no_files_omits_section(self) -> None:
        env = TaskEnvelope(
            run_id="r1", sub_agent_id="s1", project_id="p", tenant_id="t",
            session_id="ses", user_id="u", phase=Phase.SPEC,
            agent=AgentRole.ARCHITECT, user_prompt="hi",
        )
        out = _build_user_prompt_with_context(env, {})
        assert "Context files" not in out


# ---------------------------------------------------------------------------
# End-to-end runtime (in-process via ASGI transport)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_llm_returns_done_completion(
    fake_minio: _FakeMinio, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct `_invoke_llm` smoke test : a DONE envelope from the
    scripted LLM yields a DONE AgentCompletion ; no MinIO involved."""
    env = _seed_bundle(fake_minio)
    cfg = _cfg()
    llm_app = _make_scripted_llm_app(
        json.dumps({"status": "DONE", "output": {"items": ["step-1"]}}),
    )
    transport = httpx.ASGITransport(app=llm_app)
    gateway, model = _llm_settings()
    monkeypatch.setenv("C8_GATEWAY_URL", gateway)
    monkeypatch.setenv("C8_DEFAULT_MODEL", model)
    http_client = httpx.AsyncClient(transport=transport, base_url=gateway)
    _patch_llm_client_with_transport(monkeypatch, http_client)
    try:
        completion = await _invoke_llm(env, "user-prompt", cfg)
    finally:
        await http_client.aclose()

    assert completion.status is EscalationStatus.DONE
    assert completion.phase is Phase.PLAN


@pytest.mark.asyncio
async def test_run_sub_agent_full_lifecycle(
    fake_minio: _FakeMinio, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bundle load → LLM call → completion writeback. Reads back from
    MinIO and validates the SubAgentRunReport shape."""
    _seed_bundle(fake_minio)
    cfg = _cfg()
    llm_app = _make_scripted_llm_app(
        json.dumps({"status": "DONE", "output": {"items": ["step-1"]}}),
    )
    transport = httpx.ASGITransport(app=llm_app)
    gateway, model = _llm_settings()
    monkeypatch.setenv("C8_GATEWAY_URL", gateway)
    monkeypatch.setenv("C8_DEFAULT_MODEL", model)
    http_client = httpx.AsyncClient(transport=transport, base_url=gateway)
    _patch_llm_client_with_transport(monkeypatch, http_client)
    try:
        rc = await run_sub_agent(
            cfg, minio_client_factory=lambda _cfg: fake_minio,
        )
    finally:
        await http_client.aclose()

    assert rc == 0
    out_key = f"{cfg.bundle_prefix}output/completion.json"
    raw = fake_minio.store[(cfg.bundle_bucket, out_key)].decode("utf-8")
    report = SubAgentRunReport.model_validate_json(raw)
    assert report.completion["status"] == "DONE"
    assert report.completion["phase"] == "plan"
    assert report.started_at_iso <= report.finished_at_iso


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_sub_agent_returns_1_on_missing_env() -> None:
    """No required env → fail fast with rc=1 ; nothing written to MinIO."""
    bad_cfg = SubAgentConfig()  # everything empty
    rc = await run_sub_agent(
        bad_cfg, minio_client_factory=lambda _: _FakeMinio(),
    )
    assert rc == 1


@pytest.mark.asyncio
async def test_run_sub_agent_returns_1_when_manifest_missing(
    fake_minio: _FakeMinio,
) -> None:
    """Manifest absent → infra failure ; pod exits non-zero so the
    orchestrator distinguishes "pod crashed" from "completion=BLOCKED"."""
    cfg = _cfg(prefix="c4-dispatch/run-X/sub-X/")  # never seeded
    rc = await run_sub_agent(
        cfg, minio_client_factory=lambda _: fake_minio,
    )
    assert rc == 1


@pytest.mark.asyncio
async def test_llm_failure_lands_as_blocked_completion(
    fake_minio: _FakeMinio, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM returns 500 → completion.json STILL written with status
    BLOCKED so the orchestrator's three-fix-rule accounts for it."""
    _seed_bundle(fake_minio)
    cfg = _cfg()

    failing_app = FastAPI()

    @failing_app.post("/v1/chat/completions")
    async def boom() -> Any:
        raise HTTPException(status_code=500, detail="provider down")

    transport = httpx.ASGITransport(app=failing_app)
    gateway, model = _llm_settings()
    monkeypatch.setenv("C8_GATEWAY_URL", gateway)
    monkeypatch.setenv("C8_DEFAULT_MODEL", model)
    http_client = httpx.AsyncClient(transport=transport, base_url=gateway)
    _patch_llm_client_with_transport(monkeypatch, http_client)
    try:
        rc = await run_sub_agent(
            cfg, minio_client_factory=lambda _: fake_minio,
        )
    finally:
        await http_client.aclose()

    assert rc == 0  # SUCCESS — completion was written, even if BLOCKED inside.
    raw = fake_minio.store[
        (cfg.bundle_bucket, f"{cfg.bundle_prefix}output/completion.json")
    ].decode("utf-8")
    report = SubAgentRunReport.model_validate_json(raw)
    assert report.completion["status"] == "BLOCKED"
    blocker = report.completion.get("blocker") or {}
    assert "LLM gateway error" in str(blocker.get("reason", ""))
