# =============================================================================
# File: conftest.py
# Version: 4
# Path: ay_platform_core/tests/conftest.py
# Description: Root pytest configuration for the ay_platform_core sub-project.
#              Imports fixture modules so that session-scoped testcontainers
#              fixtures are discoverable from any test file.
#
#              v4 (R-100-124): the workflow adapter integration tests
#              also need Loki + Elasticsearch fixtures, registered
#              alongside the existing ArangoDB / MinIO / Ollama set.
# =============================================================================

from __future__ import annotations

import os

# In VS Code devcontainers (Docker-in-Docker), testcontainers publishes ports
# on 172.17.0.1 which is unreachable from inside the container. Override with
# host.docker.internal which the devcontainer CAN reach.
if os.environ.get("REMOTE_CONTAINERS") == "true":
    os.environ.setdefault("TESTCONTAINERS_HOST_OVERRIDE", "host.docker.internal")

pytest_plugins = [
    "tests.fixtures.containers",
    "tests.fixtures.observability_containers",
]
