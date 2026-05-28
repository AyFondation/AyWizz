# =============================================================================
# File: test_dispatch_storage.py
# Version: 1
# Path: ay_platform_core/tests/unit/c4_orchestrator/test_dispatch_storage.py
# Description: Unit tests for the sub-agent dispatch storage surface
#              (P2.1.b — R-200-033) and the bundle-builder helper. Use
#              an in-memory fake MinIO client (same shape as the
#              `tests/unit/_sub_agent/_FakeMinio`) — pure logic, no
#              real MinIO call.
#
# @relation validates:R-200-033
# =============================================================================

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from ay_platform_core._sub_agent.models import (
    ContextBundleEntry,
    SubAgentRunReport,
    TaskEnvelope,
)
from ay_platform_core.c4_orchestrator.bundle_builder import build_sub_agent_bundle
from ay_platform_core.c4_orchestrator.dispatch_storage import (
    DispatchStorage,
    _bundle_prefix,
)
from ay_platform_core.c4_orchestrator.dispatcher.base import DispatchRequest
from ay_platform_core.c4_orchestrator.models import AgentRole, Phase

# ---------------------------------------------------------------------------
# In-memory fake MinIO — mirrors the narrow surface DispatchStorage uses.
# ---------------------------------------------------------------------------


class _FakeMinio:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], bytes] = {}

    def put_object(
        self, bucket: str, key: str, data: io.BytesIO,
        length: int, content_type: str = "application/octet-stream",
    ) -> None:
        self.store[(bucket, key)] = data.read()

    def get_object(self, bucket: str, key: str) -> _FakeResp:
        if (bucket, key) not in self.store:
            raise FileNotFoundError(key)
        return _FakeResp(self.store[(bucket, key)])

    def list_objects(self, bucket: str, prefix: str, recursive: bool) -> Any:
        for (b, k), _ in list(self.store.items()):
            if b == bucket and k.startswith(prefix):
                yield _FakeObj(k)

    def remove_object(self, bucket: str, key: str) -> None:
        self.store.pop((bucket, key), None)


class _FakeResp:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        pass

    def release_conn(self) -> None:
        pass


class _FakeObj:
    def __init__(self, name: str) -> None:
        self.object_name = name


def _request() -> DispatchRequest:
    return DispatchRequest(
        run_id="run-42",
        phase=Phase.PLAN,
        agent=AgentRole.PLANNER,
        session_id="ses-1",
        tenant_id="t-1",
        user_id="u-1",
        project_id="p-1",
        prompt="Plan the refactor.",
        context_bundle={"hint": "split modules"},
    )


# ---------------------------------------------------------------------------
# Prefix helper
# ---------------------------------------------------------------------------


def test_bundle_prefix_is_namespace_run_sub_slash() -> None:
    assert _bundle_prefix("run-1", "sub-1") == "c4-dispatch/run-1/sub-1/"


# ---------------------------------------------------------------------------
# DispatchStorage CRUD round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_manifest_round_trip() -> None:
    minio = _FakeMinio()
    store = DispatchStorage(minio, "orchestrator")
    env = TaskEnvelope(
        run_id="run-1", sub_agent_id="sub-1", project_id="p", tenant_id="t",
        session_id="ses", user_id="u", phase=Phase.PLAN,
        agent=AgentRole.PLANNER, user_prompt="hi",
    )
    prefix = await store.put_manifest(env)
    assert prefix == "c4-dispatch/run-1/sub-1/"
    key = f"{prefix}manifest.json"
    assert ("orchestrator", key) in minio.store
    decoded = json.loads(minio.store[("orchestrator", key)].decode("utf-8"))
    assert decoded["run_id"] == "run-1"
    assert decoded["agent"] == "planner"


@pytest.mark.asyncio
async def test_put_context_entry_writes_under_context_dir() -> None:
    minio = _FakeMinio()
    store = DispatchStorage(minio, "orchestrator")
    entry = ContextBundleEntry(relative_path="notes/spec.md", content_type="text/markdown")
    await store.put_context_entry(
        run_id="run-1", sub_agent_id="sub-1", entry=entry,
        data=b"# Spec\n",
    )
    expected_key = "c4-dispatch/run-1/sub-1/context/notes/spec.md"
    assert ("orchestrator", expected_key) in minio.store
    assert minio.store[("orchestrator", expected_key)] == b"# Spec\n"


@pytest.mark.asyncio
async def test_get_completion_report_returns_none_when_missing() -> None:
    minio = _FakeMinio()
    store = DispatchStorage(minio, "orchestrator")
    report = await store.get_completion_report(run_id="run-x", sub_agent_id="sub-x")
    assert report is None


@pytest.mark.asyncio
async def test_get_completion_report_parses_written_payload() -> None:
    minio = _FakeMinio()
    store = DispatchStorage(minio, "orchestrator")
    payload = {
        "completion": {
            "agent": "planner", "run_id": "run-1", "phase": "plan",
            "status": "DONE", "output": {}, "concerns": [],
            "duration_ms": 100, "llm_call_ids": [],
        },
        "started_at_iso": "2026-05-20T10:00:00+00:00",
        "finished_at_iso": "2026-05-20T10:00:01+00:00",
        "llm_call_id": "mock-1",
    }
    key = "c4-dispatch/run-1/sub-1/output/completion.json"
    raw = json.dumps(payload).encode("utf-8")
    minio.store[("orchestrator", key)] = raw

    report = await store.get_completion_report(run_id="run-1", sub_agent_id="sub-1")
    assert isinstance(report, SubAgentRunReport)
    assert report.completion["status"] == "DONE"
    assert report.llm_call_id == "mock-1"


@pytest.mark.asyncio
async def test_delete_bundle_purges_every_key_under_prefix() -> None:
    minio = _FakeMinio()
    store = DispatchStorage(minio, "orchestrator")
    # Seed three objects under the bundle + one unrelated.
    minio.store[("orchestrator", "c4-dispatch/run-1/sub-1/manifest.json")] = b"{}"
    minio.store[("orchestrator", "c4-dispatch/run-1/sub-1/context/a.md")] = b"a"
    minio.store[("orchestrator", "c4-dispatch/run-1/sub-1/output/completion.json")] = b"{}"
    minio.store[("orchestrator", "c4-dispatch/run-2/sub-2/manifest.json")] = b"{}"

    await store.delete_bundle(run_id="run-1", sub_agent_id="sub-1")

    remaining_keys = sorted(k for _, k in minio.store)
    assert remaining_keys == ["c4-dispatch/run-2/sub-2/manifest.json"]


# ---------------------------------------------------------------------------
# bundle_builder.build_sub_agent_bundle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_sub_agent_bundle_writes_manifest_and_entries() -> None:
    minio = _FakeMinio()
    store = DispatchStorage(minio, "orchestrator")
    prefix, env = await build_sub_agent_bundle(
        store,
        _request(),
        sub_agent_id="sub-DET",
        context_files={
            "spec.md": b"# Spec\n",
            "code/main.py": b"print('hi')\n",
        },
        content_types={"code/main.py": "text/x-python"},
        purposes={"spec.md": "requirements excerpt"},
    )
    assert prefix == "c4-dispatch/run-42/sub-DET/"
    # Manifest is at the root ; context files under context/.
    manifest_key = f"{prefix}manifest.json"
    assert ("orchestrator", manifest_key) in minio.store
    assert (
        "orchestrator",
        f"{prefix}context/spec.md",
    ) in minio.store
    assert (
        "orchestrator",
        f"{prefix}context/code/main.py",
    ) in minio.store
    # Envelope is properly populated.
    assert env.agent is AgentRole.PLANNER
    assert env.user_prompt == "Plan the refactor."
    rel_paths = {e.relative_path for e in env.context_entries}
    assert rel_paths == {"spec.md", "code/main.py"}
    code_entry = next(e for e in env.context_entries if e.relative_path == "code/main.py")
    assert code_entry.content_type == "text/x-python"
    spec_entry = next(e for e in env.context_entries if e.relative_path == "spec.md")
    assert spec_entry.purpose == "requirements excerpt"


@pytest.mark.asyncio
async def test_build_sub_agent_bundle_generates_id_when_none() -> None:
    minio = _FakeMinio()
    store = DispatchStorage(minio, "orchestrator")
    prefix, env = await build_sub_agent_bundle(store, _request())
    assert env.sub_agent_id  # not empty
    assert prefix == f"c4-dispatch/run-42/{env.sub_agent_id}/"


@pytest.mark.asyncio
async def test_build_sub_agent_bundle_cleans_non_jsonable_context() -> None:
    """Datetime / UUID / etc. in the orchestrator's context bundle get
    stringified — the bundle MUST round-trip through JSON since the
    sub-agent reads `manifest.json` from MinIO."""
    minio = _FakeMinio()
    store = DispatchStorage(minio, "orchestrator")
    request = DispatchRequest(
        run_id="run-clean",
        phase=Phase.SPEC,
        agent=AgentRole.ARCHITECT,
        session_id="s",
        tenant_id="t",
        user_id="u",
        project_id="p",
        prompt="x",
        context_bundle={
            "concerns": ["fine"],
            "when": datetime.now(UTC),  # not JSON-native
        },
    )
    _, env = await build_sub_agent_bundle(store, request, sub_agent_id="sub-1")
    # `when` field stringified ; `concerns` preserved.
    assert isinstance(env.context_bundle["when"], str)
    assert env.context_bundle["concerns"] == ["fine"]
