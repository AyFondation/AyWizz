# =============================================================================
# File: k8s.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/dispatcher/k8s.py
# Description: Kubernetes sub-agent dispatcher (R-200-030..033). For each
#              `DispatchRequest` :
#                1. Builds a sub-agent bundle in MinIO via `build_sub_agent_bundle`
#                   (manifest.json + context files at
#                   `c4-dispatch/<run_id>/<sub_agent_id>/...`).
#                2. Creates an ephemeral Pod in the `c4-workers` namespace
#                   running the `ay-api:local` image with
#                   `COMPONENT_MODULE=_sub_agent`. SecurityContext :
#                   non-root, read-only rootfs, scratch emptyDir.
#                   `activeDeadlineSeconds = sub_agent_timeout_seconds`
#                   bounds runaway pods (R-200-032).
#                3. Watches the Pod via `watch.Watch().stream(list_namespaced_pod)`
#                   until phase ∈ {Succeeded, Failed} OR the deadline fires.
#                4. Reads `output/completion.json` back from MinIO via
#                   `DispatchStorage.get_completion_report`.
#                5. Cleans up : deletes the Pod + the bundle (best-effort).
#                6. Returns the `AgentCompletion` envelope to the orchestrator.
#
#              Failure modes :
#                - Pod never reaches Succeeded → BLOCKED with reason.
#                - Pod succeeds but no completion file → BLOCKED with reason.
#                - Network / API failures → BLOCKED with the error reason.
#
#              `kubernetes_asyncio` import is LAZY so unit tests that
#              use `InProcessDispatcher` don't need the package installed.
#              Integration test against a real cluster lives at
#              `tests/integration/c4_orchestrator/test_k8s_dispatcher.py`.
#
# @relation implements:R-200-030
# @relation implements:R-200-031
# @relation implements:R-200-032
# @relation implements:R-200-033
# =============================================================================

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ay_platform_core._sub_agent.models import SubAgentRunReport
from ay_platform_core.c4_orchestrator.bundle_builder import build_sub_agent_bundle
from ay_platform_core.c4_orchestrator.dispatch_storage import DispatchStorage
from ay_platform_core.c4_orchestrator.dispatcher.base import DispatchRequest
from ay_platform_core.c4_orchestrator.models import (
    AgentBlocker,
    AgentCompletion,
    EscalationStatus,
)

if TYPE_CHECKING:  # pragma: no cover — typing-only import
    from kubernetes_asyncio.client import V1Pod

_log = logging.getLogger("c4_orchestrator.dispatcher.k8s")


@dataclass(frozen=True, slots=True)
class K8sDispatcherConfig:
    """Static configuration for the K8s dispatcher.

    `pod_view_*` is what THE POD sees ; `dispatcher_*` is what THIS
    process (the orchestrator) sees. The two differ on Docker Desktop
    K8s because the cluster is its own network — the orchestrator uses
    docker-compose DNS (`minio:9000`) while pods use `host.docker.
    internal:9000`. In prod both sides see the cluster Service DNS
    and the two URLs collapse."""

    namespace: str = "c4-workers"
    # `ay-api:local` shared image per R-100-114. Tier image, not per-component.
    image: str = "ay-api:local"
    image_pull_policy: str = "IfNotPresent"
    service_account_name: str = "c4-sub-agent"
    # Hard timeout (R-200-032). Pod gets activeDeadlineSeconds = this.
    # Orchestrator's watch loop also enforces it as a safety net.
    sub_agent_timeout_seconds: int = 900
    # Wall-clock margin we add to the watch timeout over the pod's
    # activeDeadlineSeconds so the watch survives the kill grace
    # period (terminationGracePeriodSeconds default = 30 s).
    watch_grace_seconds: int = 60
    # MinIO endpoint AS THE POD SEES IT — set differently in dev
    # (`host.docker.internal:9000`) vs prod (`minio.minio.svc:9000`).
    pod_view_minio_endpoint: str = "minio:9000"
    pod_view_minio_access_key: str = "ay_app"
    pod_view_minio_secret_key: str = "changeme"
    pod_view_minio_secure: bool = False
    # C8 endpoint AS THE POD SEES IT. Same dev/prod split.
    pod_view_c8_gateway_url: str = "http://c8:8000/v1"
    pod_view_c8_default_model: str = ""
    pod_view_c8_agent_routes_inline: str = ""
    pod_view_c8_agent_routes_yaml_path: str = ""
    # The single shared C8 gateway credential, injected into the pod as
    # `C8_GATEWAY_API_KEY` — same value the orchestrator itself uses.
    c8_gateway_api_key: str = ""
    # When set, `load_kube_config(config_file=…)` reads this path ;
    # otherwise `load_incluster_config()` is used (production pods).
    kubeconfig_path: str = ""
    # Resource limits applied to every sub-agent pod. Conservative
    # defaults — small enough to allow many concurrent sub-agents on a
    # laptop cluster, large enough for an LLM client + JSON parse.
    cpu_request: str = "100m"
    cpu_limit: str = "500m"
    memory_request: str = "256Mi"
    memory_limit: str = "512Mi"


@dataclass(slots=True)
class K8sDispatchOutcome:
    """Internal result of the watch loop. The dispatcher converts it
    into an `AgentCompletion` envelope after consulting MinIO."""

    pod_name: str
    phase: str
    reason: str = ""
    deadline_exceeded: bool = False
    duration_ms: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


class K8sDispatcher:
    """`AgentDispatcher` implementation that runs sub-agents as
    ephemeral Kubernetes pods. The orchestrator selects this dispatcher
    by setting `C4_DISPATCHER_BACKEND=k8s` (R-200-030 baseline)."""

    def __init__(
        self,
        config: K8sDispatcherConfig,
        dispatch_storage: DispatchStorage,
    ) -> None:
        self._cfg = config
        self._storage = dispatch_storage
        self._kube_loaded = False

    async def _load_kube_config(self) -> None:
        if self._kube_loaded:
            return
        try:
            from kubernetes_asyncio import config as kube_config  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover — installed in the pod image
            raise ImportError(
                "kubernetes_asyncio is required for K8sDispatcher ; "
                "install ay_platform_core[all]",
            ) from exc
        if self._cfg.kubeconfig_path:
            await kube_config.load_kube_config(
                config_file=self._cfg.kubeconfig_path,
            )
        else:
            try:
                kube_config.load_incluster_config()
            except Exception:
                await kube_config.load_kube_config()
        self._kube_loaded = True

    # ------------------------------------------------------------------
    # AgentDispatcher protocol
    # ------------------------------------------------------------------

    async def dispatch(self, request: DispatchRequest) -> AgentCompletion:
        started = time.monotonic()
        sub_agent_id = uuid.uuid4().hex[:16]
        try:
            await self._load_kube_config()
        except Exception as exc:
            return _blocked(request, started, f"K8s config load failed: {exc}")

        try:
            _, envelope = await build_sub_agent_bundle(
                self._storage, request, sub_agent_id=sub_agent_id,
            )
        except Exception as exc:
            return _blocked(request, started, f"bundle build failed: {exc}")

        pod_name = _pod_name(envelope.run_id, sub_agent_id)
        outcome: K8sDispatchOutcome | None = None
        try:
            outcome = await self._run_pod_lifecycle(envelope, pod_name)
            completion = await self._compose_completion(
                request, envelope.sub_agent_id, outcome, started,
            )
        except Exception as exc:
            completion = _blocked(
                request, started,
                f"K8s dispatch failed for pod {pod_name!r}: {exc}",
            )
        finally:
            # Best-effort cleanup ; never raises out of the dispatcher.
            await self._cleanup_pod(pod_name)
            await self._storage.delete_bundle(
                run_id=envelope.run_id, sub_agent_id=envelope.sub_agent_id,
            )
        return completion

    # ------------------------------------------------------------------
    # Pod lifecycle
    # ------------------------------------------------------------------

    async def _run_pod_lifecycle(
        self, envelope: Any, pod_name: str,
    ) -> K8sDispatchOutcome:
        """Create the pod and watch it to terminal phase. Returns an
        outcome the caller turns into an AgentCompletion."""
        from kubernetes_asyncio import client, watch  # noqa: PLC0415

        api = client.CoreV1Api()
        manifest = self._render_pod_manifest(envelope, pod_name)
        started = time.monotonic()
        await api.create_namespaced_pod(
            namespace=self._cfg.namespace, body=manifest,
        )
        timeout = (
            self._cfg.sub_agent_timeout_seconds + self._cfg.watch_grace_seconds
        )
        outcome = K8sDispatchOutcome(pod_name=pod_name, phase="Pending")
        # `watch.Watch().stream(...)` yields events ; we read until the
        # pod reaches a terminal phase or our deadline fires.
        async with watch.Watch() as w:
            stream = w.stream(
                api.list_namespaced_pod,
                namespace=self._cfg.namespace,
                field_selector=f"metadata.name={pod_name}",
                timeout_seconds=timeout,
            )
            try:
                async for event in stream:
                    pod: V1Pod = event["object"]
                    phase = (pod.status.phase or "Pending") if pod.status else "Pending"
                    outcome.phase = phase
                    if phase in ("Succeeded", "Failed"):
                        # Capture container exit info for diagnostics.
                        cs = (pod.status.container_statuses or []) if pod.status else []
                        if cs:
                            terminated = getattr(cs[0].state, "terminated", None)
                            if terminated is not None:
                                outcome.extra["exit_code"] = getattr(terminated, "exit_code", None)
                                outcome.extra["terminated_reason"] = (
                                    getattr(terminated, "reason", None)
                                )
                        break
            finally:
                outcome.duration_ms = int((time.monotonic() - started) * 1000)
        if outcome.phase not in ("Succeeded", "Failed"):
            outcome.deadline_exceeded = True
            outcome.reason = (
                f"watch timeout ({timeout}s) ; pod stuck in {outcome.phase!r}"
            )
        elif outcome.phase == "Failed":
            outcome.reason = (
                f"pod terminated with exit_code="
                f"{outcome.extra.get('exit_code')} "
                f"reason={outcome.extra.get('terminated_reason')}"
            )
        return outcome

    async def _cleanup_pod(self, pod_name: str) -> None:
        """Best-effort pod delete. We don't propagate failures — the
        bundle cleanup runs regardless ; orphan pods are visible in
        `kubectl get pods` for operator-driven cleanup."""
        try:
            from kubernetes_asyncio import client  # noqa: PLC0415

            api = client.CoreV1Api()
            await api.delete_namespaced_pod(
                name=pod_name, namespace=self._cfg.namespace,
                grace_period_seconds=10,
            )
        except Exception as exc:
            _log.warning("pod delete %s failed: %s", pod_name, exc)

    # ------------------------------------------------------------------
    # Completion assembly
    # ------------------------------------------------------------------

    async def _compose_completion(
        self,
        request: DispatchRequest,
        sub_agent_id: str,
        outcome: K8sDispatchOutcome,
        started_monotonic: float,
    ) -> AgentCompletion:
        """Map pod outcome + completion.json (if present) into the
        orchestrator's AgentCompletion."""
        wall_ms = int((time.monotonic() - started_monotonic) * 1000)
        if outcome.deadline_exceeded:
            return AgentCompletion(
                agent=request.agent,
                run_id=request.run_id,
                phase=request.phase,
                status=EscalationStatus.BLOCKED,
                blocker=AgentBlocker(
                    reason=f"sub-agent pod {outcome.pod_name!r} exceeded "
                    f"activeDeadlineSeconds — {outcome.reason}"[:500],
                ),
                duration_ms=wall_ms,
            )
        report = await self._storage.get_completion_report(
            run_id=request.run_id, sub_agent_id=sub_agent_id,
        )
        if report is None:
            return AgentCompletion(
                agent=request.agent,
                run_id=request.run_id,
                phase=request.phase,
                status=EscalationStatus.BLOCKED,
                blocker=AgentBlocker(
                    reason=(
                        f"sub-agent pod {outcome.pod_name!r} terminated "
                        f"(phase={outcome.phase}) WITHOUT writing "
                        "completion.json — likely crashed before reaching "
                        f"the runtime ({outcome.reason})"
                    )[:500],
                ),
                duration_ms=wall_ms,
            )
        return _completion_from_report(report, request, wall_ms)

    # ------------------------------------------------------------------
    # Pod manifest rendering — R-200-031 (security context + egress)
    # ------------------------------------------------------------------

    def _render_pod_manifest(self, envelope: Any, pod_name: str) -> dict[str, Any]:
        bundle_prefix = f"c4-dispatch/{envelope.run_id}/{envelope.sub_agent_id}/"
        env: list[dict[str, Any]] = [
            {"name": "COMPONENT_MODULE", "value": "_sub_agent"},
            {"name": "SUB_AGENT_BUNDLE_BUCKET", "value": "orchestrator"},
            {"name": "SUB_AGENT_BUNDLE_PREFIX", "value": bundle_prefix},
            {"name": "MINIO_ENDPOINT", "value": self._cfg.pod_view_minio_endpoint},
            {"name": "MINIO_ACCESS_KEY", "value": self._cfg.pod_view_minio_access_key},
            {"name": "MINIO_SECRET_KEY", "value": self._cfg.pod_view_minio_secret_key},
            {"name": "MINIO_SECURE", "value": str(self._cfg.pod_view_minio_secure).lower()},
            {"name": "C8_GATEWAY_URL", "value": self._cfg.pod_view_c8_gateway_url},
            {"name": "C8_DEFAULT_MODEL", "value": self._cfg.pod_view_c8_default_model},
            {
                "name": "C8_AGENT_ROUTES_INLINE",
                "value": self._cfg.pod_view_c8_agent_routes_inline,
            },
            {
                "name": "C8_AGENT_ROUTES_YAML_PATH",
                "value": self._cfg.pod_view_c8_agent_routes_yaml_path,
            },
            {
                "name": "C8_GATEWAY_API_KEY",
                "value": self._cfg.c8_gateway_api_key,
            },
            # Identity for trace/audit ; the pod-side runtime doesn't
            # consume them, the K8s side does (labels + annotations).
            {"name": "RUN_ID", "value": envelope.run_id},
            {"name": "SUB_AGENT_ID", "value": envelope.sub_agent_id},
        ]
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": pod_name,
                "namespace": self._cfg.namespace,
                "labels": {
                    "app.kubernetes.io/name": "c4-sub-agent",
                    "ay/run-id": envelope.run_id,
                    "ay/sub-agent-id": envelope.sub_agent_id,
                    "ay/phase": envelope.phase.value,
                    "ay/agent-role": envelope.agent.value,
                },
            },
            "spec": {
                "restartPolicy": "Never",
                "activeDeadlineSeconds": self._cfg.sub_agent_timeout_seconds,
                "serviceAccountName": self._cfg.service_account_name,
                "automountServiceAccountToken": False,
                "securityContext": {
                    "runAsNonRoot": True,
                    "runAsUser": 1000,
                    "fsGroup": 1000,
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "containers": [
                    {
                        "name": "sub-agent",
                        "image": self._cfg.image,
                        "imagePullPolicy": self._cfg.image_pull_policy,
                        "command": ["python", "-m", "ay_platform_core._sub_agent"],
                        "env": env,
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "readOnlyRootFilesystem": True,
                            "capabilities": {"drop": ["ALL"]},
                        },
                        "resources": {
                            "requests": {
                                "cpu": self._cfg.cpu_request,
                                "memory": self._cfg.memory_request,
                            },
                            "limits": {
                                "cpu": self._cfg.cpu_limit,
                                "memory": self._cfg.memory_limit,
                            },
                        },
                        "volumeMounts": [
                            {"name": "scratch", "mountPath": "/tmp"},
                        ],
                    },
                ],
                "volumes": [
                    {"name": "scratch", "emptyDir": {"sizeLimit": "64Mi"}},
                ],
            },
        }


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _pod_name(run_id: str, sub_agent_id: str) -> str:
    """K8s DNS-1123 friendly pod name. Strips chars outside [a-z0-9-]
    and caps at 63 chars per K8s spec."""
    raw = f"sub-{run_id}-{sub_agent_id}".lower()
    cleaned = "".join(c if c.isalnum() or c == "-" else "-" for c in raw)
    return cleaned[:63].rstrip("-")


def _blocked(request: DispatchRequest, started_monotonic: float, reason: str) -> AgentCompletion:
    duration_ms = int((time.monotonic() - started_monotonic) * 1000)
    return AgentCompletion(
        agent=request.agent,
        run_id=request.run_id,
        phase=request.phase,
        status=EscalationStatus.BLOCKED,
        blocker=AgentBlocker(reason=reason[:500]),
        duration_ms=duration_ms,
    )


def _completion_from_report(
    report: SubAgentRunReport, request: DispatchRequest, duration_ms: int,
) -> AgentCompletion:
    """Validate the sub-agent's report.completion blob back into the
    orchestrator's AgentCompletion model. The blob was serialised by
    `_parse_completion` (in-process dispatcher's helper) so the shape
    matches — Pydantic re-validates here to catch any drift."""
    payload = dict(report.completion)
    # Override duration with the orchestrator's wall-clock — includes
    # pod creation + watch overhead which the pod doesn't see.
    payload["duration_ms"] = duration_ms
    return AgentCompletion.model_validate(payload)
