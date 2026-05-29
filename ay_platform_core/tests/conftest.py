# =============================================================================
# File: conftest.py
# Version: 5
# Path: ay_platform_core/tests/conftest.py
# Description: Root pytest configuration for the ay_platform_core sub-project.
#              Imports fixture modules so that session-scoped testcontainers
#              fixtures are discoverable from any test file.
#
#              v5 (2026-05-29): register the testcontainers fixture plugins
#              ONLY when `testcontainers` is importable. The L4 system_k8s CI
#              job (`run_k8s_system_tests.sh`) installs a minimal dep set
#              (pytest + httpx) and exercises a live kind cluster, so it
#              neither has nor needs python-arango / minio / testcontainers.
#              Unconditionally listing these plugins made pytest fail at
#              collection with `No module named 'arango'`. Gating keeps the
#              full test tiers (which install `[all]`) unchanged.
#
#              v4 (R-100-124): the workflow adapter integration tests
#              also need Loki + Elasticsearch fixtures, registered
#              alongside the existing ArangoDB / MinIO / Ollama set.
# =============================================================================

from __future__ import annotations

import importlib.util
import os

# In VS Code devcontainers (Docker-in-Docker), testcontainers publishes ports
# on 172.17.0.1 which is unreachable from inside the container. Override with
# host.docker.internal which the devcontainer CAN reach.
if os.environ.get("REMOTE_CONTAINERS") == "true":
    os.environ.setdefault("TESTCONTAINERS_HOST_OVERRIDE", "host.docker.internal")

# The fixture plugins below import python-arango / minio / testcontainers at
# module load. Only register them when those deps are present (the testcontainers
# package is the representative gate — the extras install it alongside the rest).
# The system_k8s smoke tier runs without them and must still collect.
pytest_plugins = (
    ["tests.fixtures.containers", "tests.fixtures.observability_containers"]
    if importlib.util.find_spec("testcontainers") is not None
    else []
)
