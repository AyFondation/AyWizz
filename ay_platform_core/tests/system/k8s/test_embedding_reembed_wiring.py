# =============================================================================
# File: test_embedding_reembed_wiring.py
# Path: ay_platform_core/tests/system/k8s/test_embedding_reembed_wiring.py
# Description: K8s wiring guards for the embedding auto-reembed chain
#              (c8_admin -> C12/n8n -> C7). These pin the two cross-component
#              bugs a live smoke found — which unit/CI + compose-system tests
#              CANNOT catch because they are K8s-manifest specific:
#
#                1. The n8n workflows + platform code address C7/C12 by the
#                   short names `http://c7:8000` / `http://c12:5678` (the
#                   compose service names), but the K8s Services are
#                   `c7-memory` / `c12-workflow`. Without the `c7` + `c12`
#                   alias Services those calls NXDOMAIN in-cluster, silently
#                   breaking the n8n -> C7 integration (upload AND reembed).
#
#                2. c8_admin only fires the reembed webhook when
#                   `C8_ADMIN_REEMBED_WEBHOOK_URL` is configured; if the
#                   manifest omits it the auto-trigger is silently disabled.
#
#              test_short_name_service_aliases_exist  -> guards (1)
#              test_c8_admin_reembed_webhook_env      -> guards (2)
#              test_reembed_webhook_reachable         -> proves the C12 webhook
#                is active + reachable (the c8_admin -> C12 hop end-to-end).
#
# @relation validates:R-400-229
# =============================================================================

from __future__ import annotations

import subprocess
import time

import httpx
import pytest

pytestmark = pytest.mark.system_k8s

_NS = "aywizz"


def _kubectl(*args: str) -> str:
    try:
        r = subprocess.run(
            ["kubectl", *args],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        pytest.skip(f"kubectl unavailable: {exc}")
    if r.returncode != 0:
        # Distinguish "no cluster / no namespace" (skip) from a real failure.
        if "not found" in r.stderr.lower() or "no such host" in r.stderr.lower():
            return ""
        pytest.skip(f"kubectl {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


@pytest.fixture(scope="module", autouse=True)
def _require_cluster() -> None:
    """Skip the module unless the aywizz namespace has Deployments."""
    out = _kubectl("get", "deployment", "-n", _NS, "-o", "name")
    if not out:
        pytest.skip(f"namespace {_NS} has no Deployments — not a live cluster")


def test_short_name_service_aliases_exist() -> None:
    """The `c7` + `c12` short-name alias Services MUST exist so the n8n
    workflows' `http://c7:8000` / `http://c12:5678` resolve in-cluster."""
    for svc in ("c7", "c12"):
        out = _kubectl("get", "svc", svc, "-n", _NS, "-o", "name")
        assert out, (
            f"Service `{svc}` is missing — the n8n workflow calls "
            f"http://{svc}:... will NXDOMAIN in-cluster (add the alias Service)."
        )


def test_c8_admin_reembed_webhook_env() -> None:
    """c8-admin MUST carry `C8_ADMIN_REEMBED_WEBHOOK_URL` pointing at the C12
    reembed webhook, else the auto-reembed trigger is silently disabled."""
    raw = _kubectl(
        "get", "deploy", "c8-admin", "-n", _NS,
        "-o", "jsonpath={range .spec.template.spec.containers[0].env[*]}"
        "{.name}={.value}\n{end}",
    )
    env = dict(
        line.split("=", 1) for line in raw.splitlines() if "=" in line
    )
    url = env.get("C8_ADMIN_REEMBED_WEBHOOK_URL", "")
    assert url, (
        "C8_ADMIN_REEMBED_WEBHOOK_URL is not set on c8-admin — the auto-reembed "
        "trigger (R-400-229) is disabled. Set it in admin-deployment.yaml."
    )
    assert "reembed" in url, f"unexpected reembed webhook URL: {url!r}"


def test_reembed_webhook_reachable() -> None:
    """POST the C12 reembed webhook (via a port-forward) and expect n8n to
    ACCEPT it (2xx) — proving the `reembed_project` workflow is imported +
    ACTIVE + its webhook is registered. A bogus project makes the downstream
    C7 reembed a harmless no-op. This is the c8_admin -> C12 hop end-to-end."""
    port = 18056
    pf = subprocess.Popen(
        ["kubectl", "port-forward", "-n", _NS, "svc/c12", f"{port}:5678"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 20.0
        last = ""
        resp = None
        while time.monotonic() < deadline:
            try:
                resp = httpx.post(
                    f"{base}/uploads/reembed-project",
                    json={"job": "reembed", "tenant_id": "wiring-probe",
                          "project_id": "wiring-probe", "model_id": "probe"},
                    timeout=5.0,
                )
                break
            except httpx.HTTPError as exc:
                last = f"{type(exc).__name__}: {exc}"
                time.sleep(1.0)
        if resp is None:
            pytest.skip(f"c12 webhook port-forward never came up: {last}")
        # n8n `onReceived` acks with 2xx (empty or JSON body) as soon as the
        # active workflow's webhook matches — that is the contract we pin.
        assert resp.status_code < 400, (
            f"reembed webhook returned {resp.status_code} — the "
            f"reembed_project workflow is not active/registered: {resp.text[:200]}"
        )
    finally:
        pf.terminate()
        try:
            pf.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pf.kill()
            pf.wait(timeout=2)
        if pf.stderr is not None:
            pf.stderr.close()
