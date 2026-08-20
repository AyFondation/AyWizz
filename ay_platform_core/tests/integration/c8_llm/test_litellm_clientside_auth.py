# =============================================================================
# File: test_litellm_clientside_auth.py
# Version: 2
# Path: ay_platform_core/tests/integration/c8_llm/test_litellm_clientside_auth.py
# Description: PROOF (testcontainers) that the real LiteLLM proxy honours the
#              PER-CALL `api_base` + `api_key` the C8 client injects from the
#              in-app encrypted registry — the mechanism that makes the platform
#              provider-INDEPENDENT (D-011). Without it, `ANTHROPIC_API_KEY` + a
#              static Anthropic model_list would be load-bearing; with it, the
#              proxy routes ANY provider whose endpoint + key arrive at call
#              time, so no provider is hardcoded.
#
#              Topology (Docker-out-of-Docker safe — no bind mounts, no host
#              threads): a shared network holds (1) a mock OpenAI-compatible
#              upstream (`python:slim`, alias `mockupstream`) that records the
#              Authorization it receives, and (2) a real `litellm` proxy carrying
#              the shipped `model_name: "*"` +
#              `configurable_clientside_auth_params` wildcard. Both configs are
#              written INSIDE their container from a base64 env var. The test
#              drives litellm and reads the mock's record over their mapped
#              ports; litellm reaches the mock by network alias.
#
# @relation validates:R-800-146
# =============================================================================

from __future__ import annotations

import base64
import os
import time
from collections.abc import Iterator
from typing import Any, cast

import httpx
import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network

pytestmark = [pytest.mark.integration]

# Pinned to the deployment image (litellm-deployment.yaml). Overridable so a
# .env.test / CI can pin a digest without touching the test.
LITELLM_IMAGE = os.getenv("LITELLM_TEST_IMAGE", "ghcr.io/berriai/litellm:main-stable")
MOCK_IMAGE = os.getenv("LITELLM_TEST_MOCK_IMAGE", "python:3.13-slim")
MASTER_KEY = "sk-aywizz-test-master"
INJECTED_KEY = "sk-upstream-injected-9f3a"
MOCK_ALIAS = "mockupstream"
MOCK_PORT = 8080

# The wildcard entry under test — same shape as the shipped
# infra/c8_gateway/config/litellm-config.yaml `model_name: "*"`. The allowlist
# is narrowed to the mock alias (proving the regex both PERMITS the legit base
# and REJECTS anything else).
_LITELLM_CONFIG = f"""
model_list:
  - model_name: "*"
    litellm_params:
      model: "*"
      configurable_clientside_auth_params:
        - "api_key"
        - api_base: "^http://{MOCK_ALIAS}(:[0-9]+)?(/.*)?$"
general_settings:
  master_key: {MASTER_KEY}
litellm_settings:
  drop_params: true
"""

# Mock OpenAI-compatible upstream: POST /chat/completions records the auth +
# returns a valid completion; GET /_last returns the recorded auth so the test
# (on another host) can read it.
_MOCK_SERVER = """
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
_last = {"authorization": None, "path": None}
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        return
    def do_POST(self):
        _last["authorization"] = self.headers.get("Authorization")
        _last["path"] = self.path
        n = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(n)
        body = json.dumps({
            "id": "chatcmpl-mock", "object": "chat.completion", "created": 0,
            "model": "mock-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        body = json.dumps(_last).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
HTTPServer(("0.0.0.0", 8080), H).serve_forever()
"""


def _write_and_run(env_var: str, dest: str, then: str) -> str:
    """A shell command that materialises a base64 env var to `dest` (via the
    container's own python — no bind mount, DooD-safe) then execs `then`."""
    decode = (
        f"python3 -c \"import base64,os;"
        f"open('{dest}','wb').write(base64.b64decode(os.environ['{env_var}']))\""
    )
    return f"{decode} && exec {then}"


def _poll(url: str, *, timeout_s: float, container: Any = None) -> None:
    deadline = time.monotonic() + timeout_s
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=3.0).status_code == 200:
                return
        except Exception as exc:  # retry until ready
            last = exc
        time.sleep(1.0)
    if container is not None:
        out, err = cast(Any, container).get_logs()
        print("\n=== container stdout ===\n", (out or b"").decode(errors="replace")[-4000:])
        print("\n=== container stderr ===\n", (err or b"").decode(errors="replace")[-3000:])
    raise RuntimeError(f"not ready after {timeout_s}s at {url}: {last}")


@pytest.fixture(scope="module")
def network() -> Iterator[Network]:
    net = Network()
    net.create()
    try:
        yield net
    finally:
        net.remove()


@pytest.fixture(scope="module")
def mock_upstream(network: Network) -> Iterator[str]:
    """Yields the mock's MAPPED base url (for the test to read /_last). Litellm
    reaches it in-network at http://{MOCK_ALIAS}:{MOCK_PORT}."""
    b64 = base64.b64encode(_MOCK_SERVER.encode()).decode()
    c = DockerContainer(MOCK_IMAGE)
    c.with_env("MOCK_B64", b64)
    c.with_exposed_ports(MOCK_PORT)
    c.with_network(network)
    c.with_network_aliases(MOCK_ALIAS)
    # The whole invocation MUST live in the entrypoint list (exec form) — an
    # entrypoint override + a separate `command` string is not reassembled into
    # `sh -c <script>` reliably across images.
    mock_script = _write_and_run("MOCK_B64", "/tmp/mock.py", "python3 /tmp/mock.py")
    c.with_kwargs(entrypoint=["/bin/sh", "-c", mock_script])
    c.with_command([])
    with c as started:
        host = cast(Any, started).get_container_host_ip()
        port = int(cast(Any, started).get_exposed_port(MOCK_PORT))
        base = f"http://{host}:{port}"
        _poll(f"{base}/_last", timeout_s=30.0, container=started)
        yield base


@pytest.fixture(scope="module")
def litellm_base_url(network: Network) -> Iterator[str]:
    b64 = base64.b64encode(_LITELLM_CONFIG.encode()).decode()
    c = DockerContainer(LITELLM_IMAGE)
    c.with_env("CFG_B64", b64)
    c.with_exposed_ports(4000)
    c.with_network(network)
    c.with_kwargs(
        entrypoint=[
            "/bin/sh",
            "-c",
            _write_and_run(
                "CFG_B64",
                "/tmp/cfg.yaml",
                "/app/.venv/bin/litellm --config /tmp/cfg.yaml --port 4000",
            ),
        ]
    )
    c.with_command([])
    with c as started:
        host = cast(Any, started).get_container_host_ip()
        port = int(cast(Any, started).get_exposed_port(4000))
        base = f"http://{host}:{port}"
        _poll(f"{base}/health/liveliness", timeout_s=90.0, container=started)
        yield base


def _chat(base: str, api_base: str) -> httpx.Response:
    return httpx.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {MASTER_KEY}"},
        json={
            "model": "openai/mock-model",
            "messages": [{"role": "user", "content": "hi"}],
            # The two per-call overrides the C8 client injects from the registry:
            "api_base": api_base,
            "api_key": INJECTED_KEY,
        },
        timeout=30.0,
    )


def _last(mock_base: str) -> dict[str, Any]:
    return cast("dict[str, Any]", httpx.get(f"{mock_base}/_last", timeout=5.0).json())


def test_proxy_injects_clientside_key_and_base_into_upstream(
    litellm_base_url: str, mock_upstream: str
) -> None:
    """The real proxy MUST forward the per-call api_key to the api_base target —
    proving provider-independent routing works end to end (D-011)."""
    resp = _chat(litellm_base_url, f"http://{MOCK_ALIAS}:{MOCK_PORT}")
    assert resp.status_code == 200, resp.text

    rec = _last(mock_upstream)
    # api_base honoured → the call reached the mock at /chat/completions.
    assert rec["path"] == "/chat/completions"
    # api_key honoured → the INJECTED key (not the master key) reached upstream.
    assert rec["authorization"] == f"Bearer {INJECTED_KEY}"


def test_proxy_rejects_api_base_outside_allowlist(
    litellm_base_url: str, mock_upstream: str
) -> None:
    """Anti-SSRF: an api_base NOT matching the allowlist regex MUST be refused,
    and the upstream MUST NOT be contacted with it."""
    # Prove the mock is currently reachable + reset its record via a known call.
    _chat(litellm_base_url, f"http://{MOCK_ALIAS}:{MOCK_PORT}")
    assert _last(mock_upstream)["authorization"] == f"Bearer {INJECTED_KEY}"

    resp = _chat(litellm_base_url, "http://evil.internal.example:9999")
    assert resp.status_code >= 400, resp.text
    # The mock's last record is unchanged → the disallowed base never hit it.
    assert _last(mock_upstream)["path"] == "/chat/completions"
