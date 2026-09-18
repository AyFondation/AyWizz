# =============================================================================
# File: test_config.py
# Version: 1
# Path: ay_platform_core/tests/unit/c4_orchestrator/test_config.py
# Description: Unit tests for OrchestratorConfig's `{namespace}` substitution
#              in the two `C4_K8S_POD_VIEW_*` endpoints.
#
#              WHY THIS EXISTS. Those endpoints describe the platform as a
#              SANDBOX pod sees it, so they must be fully qualified — and a
#              fully qualified name necessarily carries a namespace. Written
#              literally it pins the operator's config file to one
#              installation and, worse, keeps resolving to the OLD namespace
#              after a move instead of failing. The placeholder is resolved
#              from the orchestrator's own service-account projection.
#
# @relation validates:R-200-030
# =============================================================================

from __future__ import annotations

from pathlib import Path

import pytest

from ay_platform_core.c4_orchestrator import config as config_module
from ay_platform_core.c4_orchestrator.config import OrchestratorConfig

POD_VIEW_ENV = {
    "C4_K8S_POD_VIEW_MINIO_ENDPOINT": "minio.{namespace}.svc.cluster.local:9000",
    "C4_K8S_POD_VIEW_C8_GATEWAY_URL": (
        "http://litellm.{namespace}.svc.cluster.local:4000/v1"
    ),
}


def _projected_namespace(tmp_path: Path, value: str) -> Path:
    """Write a stand-in for the kubelet's namespace projection."""
    path = tmp_path / "namespace"
    # The real projection has no trailing newline, but tolerating surrounding
    # whitespace is what keeps a hand-made test fixture from diverging from
    # the cluster; assert the tolerance rather than assume it.
    path.write_text(f"  {value}\n", encoding="utf-8")
    return path


def test_placeholder_resolves_to_the_pods_own_namespace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """In-cluster, `{namespace}` becomes the namespace C4 itself runs in."""
    monkeypatch.setattr(
        config_module, "_NAMESPACE_FILE", _projected_namespace(tmp_path, "tenant-b")
    )
    for key, value in POD_VIEW_ENV.items():
        monkeypatch.setenv(key, value)

    cfg = OrchestratorConfig()

    assert cfg.k8s_pod_view_minio_endpoint == "minio.tenant-b.svc.cluster.local:9000"
    assert cfg.k8s_pod_view_c8_gateway_url == (
        "http://litellm.tenant-b.svc.cluster.local:4000/v1"
    )


def test_a_literal_namespace_is_passed_through_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An operator who writes a namespace out keeps exactly what they wrote.

    This is the backward-compatibility guarantee: existing deployments and
    the `host.docker.internal` compose defaults carry no placeholder, so the
    validator must not touch them — not even when a projection IS readable
    and would offer a different namespace.
    """
    monkeypatch.setattr(
        config_module, "_NAMESPACE_FILE", _projected_namespace(tmp_path, "tenant-b")
    )
    monkeypatch.setenv(
        "C4_K8S_POD_VIEW_MINIO_ENDPOINT", "minio.pinned.svc.cluster.local:9000"
    )

    cfg = OrchestratorConfig()

    assert cfg.k8s_pod_view_minio_endpoint == "minio.pinned.svc.cluster.local:9000"
    # The default, which no operator set, is equally untouched.
    assert cfg.k8s_pod_view_c8_gateway_url == "http://host.docker.internal:4000/v1"


def test_placeholder_without_a_cluster_fails_at_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Out of cluster, the placeholder is a configuration error, not a no-op.

    Substituting nothing would hand a sandbox pod an unroutable host and
    surface the mistake several layers away as a DNS failure naming a
    hostname nobody wrote. It has to fail where it is configured.
    """
    monkeypatch.setattr(config_module, "_NAMESPACE_FILE", tmp_path / "absent")
    for key, value in POD_VIEW_ENV.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(ValueError, match="not running in Kubernetes"):
        OrchestratorConfig()


def test_own_namespace_is_empty_outside_a_cluster(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unreadable projection is reported as absence, never as a crash."""
    monkeypatch.setattr(config_module, "_NAMESPACE_FILE", tmp_path / "absent")
    assert config_module._own_namespace() == ""

    # A directory at that path is the other way the read fails (IsADirectory,
    # not FileNotFound) — both are OSError, both mean "not in a cluster".
    monkeypatch.setattr(config_module, "_NAMESPACE_FILE", tmp_path)
    assert config_module._own_namespace() == ""
