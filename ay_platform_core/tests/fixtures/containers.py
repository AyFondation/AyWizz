# =============================================================================
# File: containers.py
# Version: 4
# Path: ay_platform_core/tests/fixtures/containers.py
# Description: Testcontainers fixtures for ArangoDB, MinIO, and Ollama.
#              Session-scoped by default; function-scoped variants available
#              for tests requiring a pristine container state.
#
#              v4: "really complete" cleanup between tests.
#                  - Session-start wipe of orphan test DBs/buckets (from a
#                    prior crashed run) inside the `arango_container` and
#                    `minio_container` fixtures.
#                  - Public helpers `cleanup_arango_database()` and
#                    `cleanup_minio_bucket()` with retry + post-drop
#                    verification, for use in per-component conftests'
#                    teardown.
#                  - Ollama session-scoped fixture + model pre-pull
#                    (qwen2.5:0.5b) for real-LLM integration tests.
# =============================================================================

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, cast

import httpx
import pytest
from arango import ArangoClient  # type: ignore[attr-defined]
from minio import Minio
from testcontainers.arangodb import ArangoDbContainer
from testcontainers.core.container import DockerContainer
from testcontainers.minio import MinioContainer

ARANGO_IMAGE = "arangodb/arangodb:3.12"
MINIO_IMAGE = "minio/minio:RELEASE.2025-01-20T14-49-07Z"
OLLAMA_IMAGE = "ollama/ollama:0.5.4"
OLLAMA_MODEL_ID = "qwen2.5:0.5b"
# Small 384-dim embedding model (~46 MB). Chosen to keep the pull time
# bounded; OllamaEmbedder consumers can override via fixture params if
# they need a higher-dimension model for their test.
OLLAMA_EMBED_MODEL_ID = "all-minilm"

ARANGO_ROOT_PASSWORD = "testpassword"

# Orphan detector: any DB or bucket whose name matches these patterns is
# presumed to belong to a test that crashed before cleanup. The session-start
# wipe removes them so each session starts clean.
_TEST_DB_PATTERN = re.compile(r"^(c[0-9]+|e2e)[a-z0-9_]*_test_", re.IGNORECASE)
_TEST_BUCKET_PATTERN = re.compile(
    r"^(c[0-9]+|e2e|c9)[a-z0-9-]*-test-", re.IGNORECASE
)


@dataclass(frozen=True)
class ArangoEndpoint:
    host: str
    port: int
    url: str
    username: str
    password: str


@dataclass(frozen=True)
class MinioEndpoint:
    host: str
    port: int
    endpoint: str
    access_key: str
    secret_key: str


@dataclass(frozen=True)
class OllamaEndpoint:
    """Connection details for a running Ollama test container.

    ``api_v1_url`` targets Ollama's OpenAI-compatible endpoint — drop-in
    compatible with ``LLMGatewayClient(ClientSettings(gateway_url=...))``.
    """

    host: str
    port: int
    base_url: str
    api_v1_url: str
    model_id: str
    embed_model_id: str


# ---------------------------------------------------------------------------
# Endpoint extraction
# ---------------------------------------------------------------------------


def _arango_endpoint_from_container(container: ArangoDbContainer) -> ArangoEndpoint:
    host = container.get_container_host_ip()
    port = int(container.get_exposed_port(8529))
    return ArangoEndpoint(
        host=host,
        port=port,
        url=f"http://{host}:{port}",
        username="root",
        password=ARANGO_ROOT_PASSWORD,
    )


def _minio_endpoint_from_container(container: MinioContainer) -> MinioEndpoint:
    config = container.get_config()
    endpoint = config["endpoint"]
    host, port_str = endpoint.split(":")
    return MinioEndpoint(
        host=host,
        port=int(port_str),
        endpoint=endpoint,
        access_key=config["access_key"],
        secret_key=config["secret_key"],
    )


# ---------------------------------------------------------------------------
# Orphan wipes — invoked once per session at container startup
# ---------------------------------------------------------------------------


def _wipe_arango_orphans(endpoint: ArangoEndpoint) -> None:
    """Drop every DB matching the test-prefix pattern. Runs at session start
    so a prior crashed run's state does not leak into the current one.
    """
    client = ArangoClient(hosts=endpoint.url)
    sys_db = client.db("_system", username=endpoint.username, password=endpoint.password)
    # python-arango types databases() as list[str] | AsyncJob | BatchJob; in
    # sync mode the return is a plain list — narrow for mypy.
    db_names = cast(list[str], sys_db.databases())
    for db_name in db_names:
        if _TEST_DB_PATTERN.match(db_name):
            try:
                sys_db.delete_database(db_name)
            except Exception as exc:  # pragma: no cover
                print(
                    f"WARNING: failed to wipe orphan Arango DB {db_name!r}: {exc}"
                )


def _wipe_minio_orphans(endpoint: MinioEndpoint) -> None:
    client = Minio(
        endpoint.endpoint,
        access_key=endpoint.access_key,
        secret_key=endpoint.secret_key,
        secure=False,
    )
    for bucket in client.list_buckets():
        name = bucket.name
        if _TEST_BUCKET_PATTERN.match(name):
            try:
                for obj in client.list_objects(name, recursive=True):
                    client.remove_object(name, cast(str, obj.object_name))
                client.remove_bucket(name)
            except Exception as exc:  # pragma: no cover
                print(
                    f"WARNING: failed to wipe orphan MinIO bucket {name!r}: {exc}"
                )


# ---------------------------------------------------------------------------
# Public cleanup helpers (use these in per-component conftest `finally:`)
# ---------------------------------------------------------------------------


def cleanup_arango_database(
    endpoint: ArangoEndpoint, db_name: str, *, attempts: int = 3, sleep_s: float = 0.2
) -> None:
    """Drop ``db_name`` with retry + post-drop verification.

    Idempotent: absent DBs are a no-op. Retries on transient Arango errors
    (connection glitches during parallel teardown). Raises RuntimeError on
    final failure — callers SHOULD wrap in their own ``contextlib.suppress``
    if a failure in teardown must not mask a test failure.
    """
    client = ArangoClient(hosts=endpoint.url)
    sys_db = client.db(
        "_system", username=endpoint.username, password=endpoint.password
    )
    last_err: Exception | None = None
    for attempt in range(attempts):
        try:
            if sys_db.has_database(db_name):
                sys_db.delete_database(db_name)
            if not sys_db.has_database(db_name):
                return
        except Exception as exc:
            last_err = exc
        time.sleep(sleep_s * (attempt + 1))
    raise RuntimeError(
        f"cleanup_arango_database({db_name!r}) failed after {attempts} "
        f"attempts: {last_err}"
    )


def cleanup_minio_bucket(
    endpoint: MinioEndpoint,
    bucket: str,
    *,
    attempts: int = 3,
    sleep_s: float = 0.2,
) -> None:
    """Empty + drop ``bucket`` with retry + post-drop verification."""
    client = Minio(
        endpoint.endpoint,
        access_key=endpoint.access_key,
        secret_key=endpoint.secret_key,
        secure=False,
    )
    last_err: Exception | None = None
    for attempt in range(attempts):
        try:
            if client.bucket_exists(bucket):
                for obj in client.list_objects(bucket, recursive=True):
                    client.remove_object(bucket, cast(str, obj.object_name))
                client.remove_bucket(bucket)
            if not client.bucket_exists(bucket):
                return
        except Exception as exc:
            last_err = exc
        time.sleep(sleep_s * (attempt + 1))
    raise RuntimeError(
        f"cleanup_minio_bucket({bucket!r}) failed after {attempts} "
        f"attempts: {last_err}"
    )


# ---------------------------------------------------------------------------
# Container fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def arango_container() -> Iterator[ArangoEndpoint]:
    """Session-scoped ArangoDB container.

    On startup, wipes any orphan test DBs left behind by a prior crashed
    session (pattern: ``c<n>*_test_*``, ``e2e*_test_*``). Per-test DB
    isolation is provided by component conftests via unique UUID names.
    """
    with ArangoDbContainer(
        image=ARANGO_IMAGE, arango_root_password=ARANGO_ROOT_PASSWORD
    ) as container:
        endpoint = _arango_endpoint_from_container(container)
        _wipe_arango_orphans(endpoint)
        yield endpoint


@pytest.fixture(scope="function")
def arango_container_fresh() -> Iterator[ArangoEndpoint]:
    """Function-scoped ArangoDB container. Use sparingly — expensive."""
    with ArangoDbContainer(
        image=ARANGO_IMAGE, arango_root_password=ARANGO_ROOT_PASSWORD
    ) as container:
        yield _arango_endpoint_from_container(container)


@pytest.fixture(scope="session")
def minio_container() -> Iterator[MinioEndpoint]:
    """Session-scoped MinIO container.

    Wipes orphan test buckets on startup (pattern: ``c<n>*-test-*``,
    ``c9-c<n>*``, ``e2e*-test-*``).
    """
    with MinioContainer(image=MINIO_IMAGE) as container:
        endpoint = _minio_endpoint_from_container(container)
        _wipe_minio_orphans(endpoint)
        yield endpoint


@pytest.fixture(scope="function")
def minio_container_fresh() -> Iterator[MinioEndpoint]:
    """Function-scoped MinIO container. Use sparingly — expensive."""
    with MinioContainer(image=MINIO_IMAGE) as container:
        yield _minio_endpoint_from_container(container)


# ---------------------------------------------------------------------------
# Ollama fixture (session-scoped, with model pre-pull)
# ---------------------------------------------------------------------------


def _wait_for_http(url: str, *, timeout_s: float) -> None:
    """Poll ``url`` until HTTP 200 or timeout."""
    deadline = time.monotonic() + timeout_s
    last_err: str | None = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=2.0)
            if resp.status_code == 200:
                return
            last_err = f"HTTP {resp.status_code}"
        except httpx.RequestError as exc:
            last_err = f"{type(exc).__name__}: {exc}"
        time.sleep(1.0)
    raise RuntimeError(f"{url} not ready after {timeout_s}s: {last_err}")


def _pull_ollama_model(base_url: str, model: str, *, timeout_s: float = 600.0) -> None:
    """Trigger a blocking pull of the model into the Ollama container.

    Uses `/api/pull` with `stream: false` so httpx sees a single response
    when the pull completes. 600 s timeout covers the first pull of a
    ~400 MB model on a slow network.
    """
    with httpx.Client(timeout=timeout_s) as client:
        resp = client.post(
            f"{base_url}/api/pull",
            json={"name": model, "stream": False},
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Ollama pull {model!r} failed: HTTP {resp.status_code} {resp.text}"
        )


@pytest.fixture(scope="session")
def ollama_container() -> Iterator[OllamaEndpoint]:
    """Session-scoped Ollama container serving pre-pulled small models.

    Models:
      - ``qwen2.5:0.5b`` (~400 MB, OpenAI-compatible via /v1 endpoint)
        for chat completions.
      - ``all-minilm`` (~46 MB, 384-dim embeddings via /api/embeddings)
        for real-embedder integration tests.
    Both pulls run once at session start; subsequent test runs re-pull
    because the container state isn't persisted between sessions.
    """
    container = DockerContainer(OLLAMA_IMAGE)
    container.with_exposed_ports(11434)
    with container as started:
        host = cast(Any, started).get_container_host_ip()
        port = int(cast(Any, started).get_exposed_port(11434))
        base_url = f"http://{host}:{port}"
        _wait_for_http(base_url, timeout_s=60.0)
        _pull_ollama_model(base_url, OLLAMA_MODEL_ID)
        _pull_ollama_model(base_url, OLLAMA_EMBED_MODEL_ID)
        yield OllamaEndpoint(
            host=host,
            port=port,
            base_url=base_url,
            api_v1_url=f"{base_url}/v1",
            model_id=OLLAMA_MODEL_ID,
            embed_model_id=OLLAMA_EMBED_MODEL_ID,
        )
