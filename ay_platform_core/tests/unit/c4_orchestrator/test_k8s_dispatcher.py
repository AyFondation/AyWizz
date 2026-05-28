# =============================================================================
# File: test_k8s_dispatcher.py
# Version: 1
# Path: ay_platform_core/tests/unit/c4_orchestrator/test_k8s_dispatcher.py
# Description: Unit tests for the K8sDispatcher (P2.1.c, R-200-030..033).
#              These don't touch a real cluster — they cover :
#                - pod manifest rendering shape (security context,
#                  env vars, resource limits, labels) ;
#                - DNS-1123 pod-name sanitisation ;
#                - completion assembly from a SubAgentRunReport.
#              The end-to-end pod lifecycle (create pod / watch / read
#              completion / cleanup) is exercised in
#              `tests/integration/c4_orchestrator/test_k8s_dispatcher.py`
#              against Docker Desktop K8s, skipped when no cluster is
#              available.
#
# @relation validates:R-200-030
# @relation validates:R-200-031
# @relation validates:R-200-032
# @relation validates:R-200-033
# =============================================================================

from __future__ import annotations

import io
import json
import time
from typing import Any

import pytest

from ay_platform_core._sub_agent.models import (
    SubAgentRunReport,
    TaskEnvelope,
)
from ay_platform_core.c4_orchestrator.dispatch_storage import DispatchStorage
from ay_platform_core.c4_orchestrator.dispatcher.base import DispatchRequest
from ay_platform_core.c4_orchestrator.dispatcher.k8s import (
    K8sDispatcher,
    K8sDispatcherConfig,
    K8sDispatchOutcome,
    _completion_from_report,
    _pod_name,
)
from ay_platform_core.c4_orchestrator.models import (
    AgentRole,
    EscalationStatus,
    Phase,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeMinio:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], bytes] = {}

    def put_object(
        self, bucket: str, key: str, data: io.BytesIO,
        length: int, content_type: str = "application/octet-stream",
    ) -> None:
        self.store[(bucket, key)] = data.read()

    def get_object(self, bucket: str, key: str) -> _FakeMinioReadResp:
        if (bucket, key) not in self.store:
            raise FileNotFoundError(key)
        return _FakeMinioReadResp(self.store[(bucket, key)])

    def list_objects(self, bucket: str, prefix: str, recursive: bool) -> Any:
        # Iterator over namespaced keys that start with `prefix`.
        # Returns objects with `.object_name` to match the real client.
        for (b, k), _ in list(self.store.items()):
            if b == bucket and k.startswith(prefix):
                yield _FakeMinioObj(k)

    def remove_object(self, bucket: str, key: str) -> None:
        self.store.pop((bucket, key), None)


class _FakeMinioObj:
    def __init__(self, name: str) -> None:
        self.object_name = name


def _cfg(**overrides: Any) -> K8sDispatcherConfig:
    base: dict[str, Any] = {
        "namespace": "c4-workers",
        "image": "ay-api:local",
        "sub_agent_timeout_seconds": 60,
        "watch_grace_seconds": 5,
        "pod_view_minio_endpoint": "host.docker.internal:9000",
        "pod_view_minio_access_key": "ay_app",
        "pod_view_minio_secret_key": "changeme",
        "pod_view_c8_gateway_url": "http://host.docker.internal:8000/v1",
        "c8_gateway_api_key": "bearer-x",
    }
    base.update(overrides)
    return K8sDispatcherConfig(**base)


def _request() -> DispatchRequest:
    return DispatchRequest(
        run_id="run-42",
        phase=Phase.GENERATE,
        agent=AgentRole.IMPLEMENTER,
        session_id="ses-1",
        tenant_id="t-1",
        user_id="u-1",
        project_id="p-1",
        prompt="Generate.",
        context_bundle={},
    )


def _envelope() -> TaskEnvelope:
    return TaskEnvelope(
        run_id="run-42",
        sub_agent_id="sub-abc123",
        project_id="p-1",
        tenant_id="t-1",
        session_id="ses-1",
        user_id="u-1",
        phase=Phase.GENERATE,
        agent=AgentRole.IMPLEMENTER,
        user_prompt="Generate.",
    )


# ---------------------------------------------------------------------------
# Pod name sanitisation (DNS-1123)
# ---------------------------------------------------------------------------


class TestPodName:
    def test_basic_lowercase_with_dashes(self) -> None:
        assert _pod_name("run-1", "sub-1") == "sub-run-1-sub-1"

    def test_caps_lowered(self) -> None:
        assert _pod_name("RUN-XX", "SUB-1") == "sub-run-xx-sub-1"

    def test_non_alnum_replaced_with_dash(self) -> None:
        out = _pod_name("run/42", "sub.x")
        assert "/" not in out
        assert "." not in out

    def test_length_capped_at_63(self) -> None:
        out = _pod_name("a" * 100, "b" * 100)
        assert len(out) <= 63

    def test_no_trailing_dash(self) -> None:
        out = _pod_name("a" * 40, "b" * 40)
        assert not out.endswith("-")


# ---------------------------------------------------------------------------
# Pod manifest rendering
# ---------------------------------------------------------------------------


class TestPodManifest:
    def test_manifest_baseline_shape(self) -> None:
        dispatcher = K8sDispatcher(_cfg(), DispatchStorage(_FakeMinio(), "orchestrator"))
        manifest = dispatcher._render_pod_manifest(_envelope(), "sub-run-42-sub-abc123")
        assert manifest["apiVersion"] == "v1"
        assert manifest["kind"] == "Pod"
        assert manifest["metadata"]["name"] == "sub-run-42-sub-abc123"
        assert manifest["metadata"]["namespace"] == "c4-workers"

    def test_security_context_non_root_readonly(self) -> None:
        dispatcher = K8sDispatcher(_cfg(), DispatchStorage(_FakeMinio(), "orchestrator"))
        manifest = dispatcher._render_pod_manifest(_envelope(), "test-pod")
        pod_sec = manifest["spec"]["securityContext"]
        assert pod_sec["runAsNonRoot"] is True
        assert pod_sec["runAsUser"] == 1000
        container_sec = manifest["spec"]["containers"][0]["securityContext"]
        assert container_sec["allowPrivilegeEscalation"] is False
        assert container_sec["readOnlyRootFilesystem"] is True
        assert container_sec["capabilities"] == {"drop": ["ALL"]}

    def test_active_deadline_seconds_matches_config(self) -> None:
        dispatcher = K8sDispatcher(
            _cfg(sub_agent_timeout_seconds=42),
            DispatchStorage(_FakeMinio(), "orchestrator"),
        )
        manifest = dispatcher._render_pod_manifest(_envelope(), "test-pod")
        assert manifest["spec"]["activeDeadlineSeconds"] == 42

    def test_restart_policy_never(self) -> None:
        dispatcher = K8sDispatcher(_cfg(), DispatchStorage(_FakeMinio(), "orchestrator"))
        manifest = dispatcher._render_pod_manifest(_envelope(), "test-pod")
        assert manifest["spec"]["restartPolicy"] == "Never"

    def test_env_carries_bundle_prefix_and_c8_creds(self) -> None:
        dispatcher = K8sDispatcher(_cfg(), DispatchStorage(_FakeMinio(), "orchestrator"))
        manifest = dispatcher._render_pod_manifest(_envelope(), "test-pod")
        env = {e["name"]: e["value"] for e in manifest["spec"]["containers"][0]["env"]}
        assert env["COMPONENT_MODULE"] == "_sub_agent"
        assert env["SUB_AGENT_BUNDLE_PREFIX"] == "c4-dispatch/run-42/sub-abc123/"
        assert env["MINIO_ENDPOINT"] == "host.docker.internal:9000"
        assert env["C8_GATEWAY_URL"] == "http://host.docker.internal:8000/v1"
        assert env["C8_GATEWAY_API_KEY"] == "bearer-x"

    def test_command_is_python_module_invocation(self) -> None:
        dispatcher = K8sDispatcher(_cfg(), DispatchStorage(_FakeMinio(), "orchestrator"))
        manifest = dispatcher._render_pod_manifest(_envelope(), "test-pod")
        cmd = manifest["spec"]["containers"][0]["command"]
        assert cmd == ["python", "-m", "ay_platform_core._sub_agent"]

    def test_labels_include_identity_for_kubectl_filtering(self) -> None:
        dispatcher = K8sDispatcher(_cfg(), DispatchStorage(_FakeMinio(), "orchestrator"))
        manifest = dispatcher._render_pod_manifest(_envelope(), "test-pod")
        labels = manifest["metadata"]["labels"]
        assert labels["ay/run-id"] == "run-42"
        assert labels["ay/sub-agent-id"] == "sub-abc123"
        assert labels["ay/phase"] == "generate"
        assert labels["ay/agent-role"] == "implementer"

    def test_no_service_account_token_mount(self) -> None:
        """Sub-agents have no business hitting the K8s API — drop the
        default token mount to harden the pod (R-200-031 spirit)."""
        dispatcher = K8sDispatcher(_cfg(), DispatchStorage(_FakeMinio(), "orchestrator"))
        manifest = dispatcher._render_pod_manifest(_envelope(), "test-pod")
        assert manifest["spec"]["automountServiceAccountToken"] is False


# ---------------------------------------------------------------------------
# Completion assembly from a sub-agent report
# ---------------------------------------------------------------------------


class TestCompletionAssembly:
    def test_done_completion_from_report(self) -> None:
        report = SubAgentRunReport(
            completion={
                "agent": "implementer",
                "run_id": "run-42",
                "phase": "generate",
                "status": "DONE",
                "output": {"files": [{"path": "a.py", "content": "x = 1\n"}]},
                "concerns": [],
                "duration_ms": 100,  # pod-side
                "llm_call_ids": ["mock-1"],
            },
            started_at_iso="2026-05-20T10:00:00+00:00",
            finished_at_iso="2026-05-20T10:00:05+00:00",
            llm_call_id="mock-1",
        )
        completion = _completion_from_report(report, _request(), duration_ms=300)
        assert completion.status is EscalationStatus.DONE
        assert completion.duration_ms == 300  # orchestrator wall-clock, not pod's
        assert completion.llm_call_ids == ["mock-1"]


# ---------------------------------------------------------------------------
# Outcome → AgentCompletion transitions
# ---------------------------------------------------------------------------


class TestComposeCompletion:
    @pytest.mark.asyncio
    async def test_deadline_exceeded_yields_blocked(self) -> None:
        dispatcher = K8sDispatcher(
            _cfg(), DispatchStorage(_FakeMinio(), "orchestrator"),
        )
        outcome = K8sDispatchOutcome(
            pod_name="p1", phase="Pending", deadline_exceeded=True,
            reason="watch timeout",
        )
        completion = await dispatcher._compose_completion(
            _request(), "sub-x", outcome, started_monotonic=time.monotonic(),
        )
        assert completion.status is EscalationStatus.BLOCKED
        assert completion.blocker is not None
        assert "exceeded activeDeadlineSeconds" in completion.blocker.reason

    @pytest.mark.asyncio
    async def test_pod_succeeded_without_completion_file_yields_blocked(self) -> None:
        # MinIO empty → DispatchStorage.get_completion_report returns None.
        dispatcher = K8sDispatcher(
            _cfg(), DispatchStorage(_FakeMinio(), "orchestrator"),
        )
        outcome = K8sDispatchOutcome(pod_name="p1", phase="Succeeded")
        completion = await dispatcher._compose_completion(
            _request(), "sub-x", outcome, started_monotonic=time.monotonic(),
        )
        assert completion.status is EscalationStatus.BLOCKED
        assert completion.blocker is not None
        assert "WITHOUT writing" in completion.blocker.reason

    @pytest.mark.asyncio
    async def test_pod_succeeded_with_completion_returns_envelope(self) -> None:
        minio = _FakeMinio()
        report_payload = {
            "completion": {
                "agent": "implementer",
                "run_id": "run-42",
                "phase": "generate",
                "status": "DONE_WITH_CONCERNS",
                "output": {"items": ["a"]},
                "concerns": [{"severity": "low", "message": "trivial"}],
                "duration_ms": 50,
                "llm_call_ids": [],
            },
            "started_at_iso": "2026-05-20T10:00:00+00:00",
            "finished_at_iso": "2026-05-20T10:00:01+00:00",
            "llm_call_id": None,
        }
        minio.store[(
            "orchestrator", "c4-dispatch/run-42/sub-X/output/completion.json",
        )] = json.dumps(report_payload).encode("utf-8")

        dispatcher = K8sDispatcher(
            _cfg(), DispatchStorage(minio, "orchestrator"),
        )
        outcome = K8sDispatchOutcome(pod_name="p1", phase="Succeeded")
        completion = await dispatcher._compose_completion(
            _request(), "sub-X", outcome, started_monotonic=time.monotonic(),
        )
        assert completion.status is EscalationStatus.DONE_WITH_CONCERNS
        assert completion.concerns[0].severity == "low"


class _FakeMinioReadResp:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        pass

    def release_conn(self) -> None:
        pass
