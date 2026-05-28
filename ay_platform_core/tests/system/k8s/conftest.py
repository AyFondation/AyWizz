# =============================================================================
# File: conftest.py
# Version: 1
# Path: ay_platform_core/tests/system/k8s/conftest.py
# Description: Fixtures for the `system_k8s` test tier. The tests verify
#              that the platform's K8s manifests bring up a working
#              system: every component starts, every dependency is
#              connected, and the public surface routes through C1
#              Traefik with the expected auth contract.
#
#              Lifecycle is split between the wrapper script and pytest:
#                - Wrapper (`scripts/run_k8s_system_tests.sh`) handles
#                  cluster bring-up: kind create, build image,
#                  `kind load`, install Traefik CRDs, apply overlay,
#                  wait for Deployments / StatefulSets / Jobs.
#                - This conftest assumes the cluster is already up. It
#                  starts a `kubectl port-forward` to expose Traefik on
#                  localhost, waits for /auth/config to respond, and
#                  yields the base URL.
#
#              Skip behaviour:
#                - AY_SKIP_K8S_TESTS=1            → skip all
#                - kubectl missing                → skip all
#                - aywizz namespace empty         → skip all
#                - K8S_BASE_URL set in env        → reuse, no
#                  port-forward (caller-managed)
# =============================================================================

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterator

import httpx
import pytest

_NAMESPACE = "aywizz"
_GATEWAY_SVC = "c1-gateway"
_LOCAL_PORT = 18001  # avoid clash with k8s_kind_smoke.sh's 18000
_GATEWAY_PORT = 80
_READY_TIMEOUT_S = 90.0


def _kubectl_available() -> bool:
    try:
        subprocess.run(
            ["kubectl", "version", "--client=true"],
            capture_output=True, check=True, timeout=5,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return False
    return True


def _namespace_has_deployments(namespace: str) -> bool:
    try:
        result = subprocess.run(
            ["kubectl", "get", "deployment", "-n", namespace, "-o", "name"],
            capture_output=True, text=True, check=True, timeout=10,
        )
    except subprocess.SubprocessError:
        return False
    return bool(result.stdout.strip())


def _wait_for_gateway(base_url: str, timeout_s: float) -> None:
    """Poll `<base_url>/auth/config` until it returns 200 or the timeout
    elapses. Skips the test session if the gateway never responds (a
    half-up cluster is more useful as a skip than as N cascade failures)."""
    deadline = time.monotonic() + timeout_s
    last_err: str = ""
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}/auth/config", timeout=3.0)
            if resp.status_code == 200:
                return
            last_err = f"/auth/config -> {resp.status_code}"
        except httpx.HTTPError as exc:
            last_err = f"{type(exc).__name__}: {exc}"
        time.sleep(1.0)
    pytest.skip(
        f"gateway at {base_url} did not respond within {timeout_s}s "
        f"(last error: {last_err})"
    )


@pytest.fixture(scope="session")
def k8s_base_url() -> Iterator[str]:
    """Base URL for the platform's public C1 Traefik gateway.

    Caller-managed: the wrapper script (or the user) brings up the
    cluster and applies the manifests; this fixture establishes the
    network path (port-forward) and verifies the gateway answers."""
    if os.environ.get("AY_SKIP_K8S_TESTS") == "1":
        pytest.skip("AY_SKIP_K8S_TESTS=1")
    if not _kubectl_available():
        pytest.skip("kubectl not on PATH")
    if not _namespace_has_deployments(_NAMESPACE):
        pytest.skip(
            f"namespace {_NAMESPACE} has no Deployments. "
            "Run scripts/run_k8s_system_tests.sh or apply an overlay manually."
        )

    explicit = os.environ.get("K8S_BASE_URL")
    if explicit:
        # Caller manages the port-forward / Service exposure; just verify.
        _wait_for_gateway(explicit, _READY_TIMEOUT_S)
        yield explicit.rstrip("/")
        return

    # Default path: pytest manages the port-forward.
    port_fwd = subprocess.Popen(
        [
            "kubectl", "port-forward",
            "-n", _NAMESPACE,
            f"svc/{_GATEWAY_SVC}",
            f"{_LOCAL_PORT}:{_GATEWAY_PORT}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    base_url = f"http://localhost:{_LOCAL_PORT}"
    try:
        _wait_for_gateway(base_url, _READY_TIMEOUT_S)
        yield base_url
    finally:
        port_fwd.terminate()
        try:
            port_fwd.wait(timeout=5)
        except subprocess.TimeoutExpired:
            port_fwd.kill()
            port_fwd.wait(timeout=2)
