# =============================================================================
# File: conftest.py
# Version: 1
# Path: ay_platform_core/tests/integration/c3_conversation/conftest.py
# Description: Fixtures for C3 Conversation Service integration tests.
#              Uses the session-scoped ArangoDB testcontainer fixture and
#              creates an isolated database per test function.
#
# NOTE: fixtures are sync (arango_container is sync). Async test functions
#       (asyncio_mode=auto) can use sync fixtures without wrapping.
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c3_conversation.db.repository import ConversationRepository
from ay_platform_core.c3_conversation.router import router
from ay_platform_core.c3_conversation.service import ConversationService
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database


@pytest.fixture(scope="function")
def conv_repo(arango_container: ArangoEndpoint) -> Iterator[ConversationRepository]:
    """Isolated ConversationRepository backed by a fresh DB within the shared container."""
    db_name = f"c3_test_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)

    db = client.db(db_name, username="root", password=arango_container.password)
    repo = ConversationRepository(db)
    repo._ensure_collections_sync()

    try:
        yield repo
    finally:
        cleanup_arango_database(arango_container, db_name)


@pytest.fixture(scope="function")
def conv_app(conv_repo: ConversationRepository) -> FastAPI:
    """FastAPI app wired with the isolated ConversationRepository."""
    app = FastAPI()
    app.include_router(router)
    svc = ConversationService(conv_repo)
    app.state.conversation_service = svc
    return app
