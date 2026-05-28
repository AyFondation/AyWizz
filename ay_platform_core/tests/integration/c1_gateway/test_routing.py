# =============================================================================
# File: test_routing.py
# Version: 3
# Path: ay_platform_core/tests/integration/c1_gateway/test_routing.py
# Description: Integration tests — Traefik gateway routing and middleware.
#              Spins up a real Traefik v3 container and injects config files
#              via put_archive (not bind mounts): devcontainer paths are not
#              addressable by the host Docker daemon on Docker Desktop, so
#              bind-mounting /workspace/... fails with "mounts denied".
#              Verifies:
#                - Routed requests reach the backend (routing correctness)
#                - /auth/login rate-limit triggers after threshold
#                - Forward-auth middleware is on the chain for /api/* routes
#
#              NOTE: These tests require Docker. They are skipped automatically
#              if SKIP_INTEGRATION_TESTS=1 or Docker is unavailable.
#              Sync fixtures only — do NOT make this class async.
# @relation R-100-039 R-100-042
# =============================================================================

from __future__ import annotations

import contextlib
import io
import os
import socket
import tarfile
import threading
import time
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import requests
import yaml

try:
    import docker as _docker
except ImportError:
    _docker = None

INFRA_ROOT = Path(__file__).parent.parent.parent.parent.parent / "infra" / "c1_gateway"

pytestmark = pytest.mark.integration

_SKIP_REASON = "Docker unavailable or SKIP_INTEGRATION_TESTS=1"


def _docker_available() -> bool:
    if os.environ.get("SKIP_INTEGRATION_TESTS") == "1":
        return False
    if _docker is None:
        return False
    try:
        client = _docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Minimal mock backend — responds 200 to everything (acts as C2/C3/… stub)
# ---------------------------------------------------------------------------


class _AlwaysOKHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self._respond()

    def do_POST(self) -> None:
        self._respond()

    def _respond(self) -> None:
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # Traefik forward-auth needs these headers to propagate downstream
        self.send_header("X-User-Id", "test-user")
        self.send_header("X-User-Roles", "user")
        self.send_header("X-Platform-Auth-Mode", "none")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # silence HTTP server logs
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return int(s.getsockname()[1])


def _mock_backend_ip() -> str:
    """Return an IP reachable from inside a Traefik container launched by the
    same Docker daemon.

    - On a plain Linux host: `host.docker.internal` (mapped to host-gateway).
    - Inside a devcontainer (REMOTE_CONTAINERS=true): the mock HTTP server
      listens on the devcontainer itself, not on the Docker host. Return the
      devcontainer's own IP on the Docker bridge network so Traefik reaches
      us directly, not via the host.
    """
    if os.environ.get("REMOTE_CONTAINERS") != "true" or _docker is None:
        return "host.docker.internal"
    try:
        client = _docker.from_env()
        hostname = socket.gethostname()
        self_containers = [c for c in client.containers.list() if c.id.startswith(hostname)]
        if self_containers:
            nets = self_containers[0].attrs["NetworkSettings"]["Networks"]
            # Prefer the default 'bridge' network; any network with an IP will do
            for name in ("bridge", *nets.keys()):
                info = nets.get(name)
                if info and info.get("IPAddress"):
                    ip: str = info["IPAddress"]
                    return ip
    except Exception:  # pragma: no cover — degraded probe
        pass
    return "host.docker.internal"


_REQUIRED_ROUTERS = frozenset({
    "c2-auth-login@file",
    "c2-auth-token@file",
    "c2-auth@file",
    "c3-conversations@file",
})


def _wait_for_routers_enabled(
    dashboard_url: str,
    required: frozenset[str],
    timeout_s: float,
) -> None:
    """Poll Traefik's dashboard API until every required router is enabled.

    TCP-accept on the web entrypoint happens before the file provider
    finishes parsing config — asserting on router status avoids a flaky
    race where tests fire before routes are active.
    """
    deadline = time.time() + timeout_s
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            resp = requests.get(f"{dashboard_url}/api/http/routers", timeout=1)
            if resp.status_code == 200:
                enabled = {r["name"] for r in resp.json() if r.get("status") == "enabled"}
                if required.issubset(enabled):
                    return
        except requests.exceptions.RequestException as e:
            last_err = e
        time.sleep(0.3)
    raise RuntimeError(
        f"Traefik did not enable required routers within {timeout_s}s. "
        f"Last error: {last_err}. Required: {sorted(required)}"
    )


def _make_tar(files: dict[str, bytes]) -> bytes:
    """Build an in-memory uncompressed tar archive.

    Automatically emits directory entries for every path prefix so that
    `docker put_archive` can extract files into directories that do not yet
    exist in the target container (e.g. /etc/traefik/dynamic/).
    """
    buf = io.BytesIO()
    seen_dirs: set[str] = set()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, content in files.items():
            # Emit parent dir entries first
            parts = name.split("/")
            for i in range(1, len(parts)):
                dir_path = "/".join(parts[:i]) + "/"
                if dir_path in seen_dirs:
                    continue
                seen_dirs.add(dir_path)
                dir_info = tarfile.TarInfo(dir_path)
                dir_info.type = tarfile.DIRTYPE
                dir_info.mode = 0o755
                tar.addfile(dir_info)
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


@pytest.fixture(scope="module")
def traefik_url() -> Generator[str]:
    """Launch Traefik v3 with our config injected via put_archive.

    We do NOT bind-mount infra/c1_gateway/: on Docker Desktop the daemon runs
    on the host and /workspace/... (a devcontainer-only path) fails with
    "mounts denied". Instead we `create` the container, `put_archive` the
    config files into /etc/traefik/, then `start`. This is host-agnostic.
    """
    if not _docker_available():
        pytest.skip(_SKIP_REASON)
    assert _docker is not None  # mypy narrowing — _docker_available() guarantees this

    mock_port = _free_port()
    server = HTTPServer(("0.0.0.0", mock_port), _AlwaysOKHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Services config rewritten to point at the mock backend. See
    # _mock_backend_ip() for how we resolve a reachable host from within a
    # Traefik container spawned by the same Docker daemon.
    mock_url = f"http://{_mock_backend_ip()}:{mock_port}"
    services_content = "http:\n  services:\n"
    for svc in ("c2", "c3", "c4", "c5", "c6", "c12"):
        services_content += (
            f"    {svc}:\n      loadBalancer:\n        servers:\n"
            f"          - url: \"{mock_url}\"\n"
        )

    # Read real config files from infra/ (Python reads them directly — bind
    # mounts would be what breaks, not reads).
    # The real traefik.yml enables the Docker provider, which needs
    # /var/run/docker.sock mounted; for this end-to-end routing test we only
    # exercise the file provider, so strip providers.docker before injecting.
    traefik_cfg = yaml.safe_load((INFRA_ROOT / "traefik.yml").read_text(encoding="utf-8"))
    traefik_cfg.get("providers", {}).pop("docker", None)
    traefik_yml = yaml.safe_dump(traefik_cfg).encode("utf-8")
    middlewares_yml = (INFRA_ROOT / "dynamic" / "middlewares.yml").read_bytes()
    routers_yml = (INFRA_ROOT / "dynamic" / "routers.yml").read_bytes()

    client = _docker.from_env()
    # Pull image explicitly so create() does not race on first run
    client.images.pull("traefik:v3.3")

    container = client.containers.create(
        "traefik:v3.3",
        ports={"80/tcp": None, "8080/tcp": None},
        extra_hosts={"host.docker.internal": "host-gateway"},
        detach=True,
    )
    try:
        # Inject all config files in one archive extracted at /. The helper
        # emits directory entries for /etc/traefik and /etc/traefik/dynamic
        # because the Traefik image does not ship them.
        container.put_archive(
            "/",
            _make_tar({
                "etc/traefik/traefik.yml": traefik_yml,
                "etc/traefik/dynamic/middlewares.yml": middlewares_yml,
                "etc/traefik/dynamic/routers.yml": routers_yml,
                "etc/traefik/dynamic/services.yml": services_content.encode("utf-8"),
            }),
        )

        container.start()

        # Resolve mapped ports once the container is running. Ports published
        # by the Docker daemon land on the host's localhost; from inside a
        # devcontainer, that's reachable via host.docker.internal (matches
        # TESTCONTAINERS_HOST_OVERRIDE set in tests/conftest.py).
        container.reload()
        host_port = container.ports["80/tcp"][0]["HostPort"]
        dash_port = container.ports["8080/tcp"][0]["HostPort"]
        test_host = (
            "host.docker.internal"
            if os.environ.get("REMOTE_CONTAINERS") == "true"
            else "localhost"
        )
        url = f"http://{test_host}:{host_port}"
        dashboard_url = f"http://{test_host}:{dash_port}"

        try:
            _wait_for_routers_enabled(dashboard_url, _REQUIRED_ROUTERS, timeout_s=20)
        except RuntimeError as exc:
            raise RuntimeError(
                f"{exc}\nLogs:\n{container.logs().decode('utf-8', errors='replace')}"
            ) from exc
        yield url
    finally:
        with contextlib.suppress(Exception):
            container.stop(timeout=2)
        with contextlib.suppress(Exception):
            container.remove(force=True)
        server.shutdown()
        # shutdown() stops the serve_forever loop but does not release the
        # listening socket; server_close() is required to avoid pytest
        # unraisable-exception warnings about the unclosed socket.
        server.server_close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _docker_available(), reason=_SKIP_REASON)
class TestRouting:
    def test_auth_login_reachable(self, traefik_url: str) -> None:
        resp = requests.post(f"{traefik_url}/auth/login", json={}, timeout=5)
        # Mock backend returns 200; Traefik should forward the request
        assert resp.status_code == 200

    def test_auth_token_reachable(self, traefik_url: str) -> None:
        resp = requests.post(f"{traefik_url}/auth/token", data={}, timeout=5)
        assert resp.status_code == 200

    def test_api_route_is_gated_by_forward_auth(self, traefik_url: str) -> None:
        """forward-auth-c2 is on the chain for /api/* routes.

        In this test harness the auth service address (c2:8000/auth/verify)
        does not resolve — Traefik's forward-auth therefore fails and returns
        500, which is itself proof that the middleware is being invoked. If
        forward-auth were missing, the request would hit the always-OK mock
        backend and return 200.

        We assert `status_code != 200` to detect both:
          - 500 (auth unreachable — current test env)
          - 401 (auth reachable and rejecting — future env with real C2)
        """
        # C5 surface lives under /api/v1/projects/* — hit a representative
        # C5 route to confirm the forward-auth chain intercepts it.
        resp = requests.get(
            f"{traefik_url}/api/v1/projects/demo/requirements/documents",
            headers={"Authorization": "Bearer fake-token"},
            timeout=5,
        )
        assert resp.status_code != 200, (
            f"Expected forward-auth to intercept /api/v1/projects/..., got "
            f"{resp.status_code} (200 means the mock backend was reached — "
            "forward-auth is NOT on the chain)"
        )
        assert resp.status_code in (401, 500, 502, 503), (
            f"Unexpected status from forward-auth chain: {resp.status_code}"
        )

    def test_uploads_route_is_gated_by_forward_auth(self, traefik_url: str) -> None:
        """Same guarantee as /api/* — forward-auth-c2 protects /uploads/*."""
        resp = requests.post(
            f"{traefik_url}/uploads/test",
            headers={"Authorization": "Bearer fake-token"},
            timeout=5,
        )
        assert resp.status_code != 200, (
            f"Expected forward-auth to intercept /uploads/test, got "
            f"{resp.status_code}"
        )
        assert resp.status_code in (401, 500, 502, 503)


@pytest.mark.skipif(not _docker_available(), reason=_SKIP_REASON)
class TestRateLimiting:
    def test_rate_limit_triggers_on_login(self, traefik_url: str) -> None:
        """After > average (10) requests, rate limit should kick in (429)."""
        statuses = []
        for _ in range(15):
            resp = requests.post(f"{traefik_url}/auth/login", json={}, timeout=5)
            statuses.append(resp.status_code)

        has_429 = 429 in statuses
        # Rate limit may not trigger immediately in all environments due to
        # burst allowance; assert we got at least one limited response.
        assert has_429, (
            f"Expected at least one 429 after 15 rapid /auth/login requests. "
            f"Got: {statuses}"
        )
