# =============================================================================
# File: test_embedder_for.py
# Version: 1
# Path: ay_platform_core/tests/unit/c7_memory/test_embedder_for.py
# Description: Unit tests for MemoryService._embedder_for — the seam that picks
#              a project's embedder from the registry resolver, falling back to
#              the global env embedder when no resolver is wired, the project id
#              is blank, or the project has no registry selection (D-011,
#              R-400-227).
#
# @relation validates:R-400-227
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.embedding.base import EmbeddingProvider
from ay_platform_core.c7_memory.embedding.deterministic import DeterministicHashEmbedder
from ay_platform_core.c7_memory.service import MemoryService

pytestmark = pytest.mark.unit


class _FakeResolver:
    def __init__(self, embedder: EmbeddingProvider | None) -> None:
        self._embedder = embedder
        self.calls: list[tuple[str, str]] = []

    async def resolve(
        self, tenant_id: str, project_id: str
    ) -> EmbeddingProvider | None:
        self.calls.append((tenant_id, project_id))
        return self._embedder


def _service(
    global_embedder: EmbeddingProvider, resolver: _FakeResolver | None
) -> MemoryService:
    config = MemoryConfig()
    return MemoryService(
        config=config,
        repo=object(),  # type: ignore[arg-type]
        embedder=global_embedder,
        embedder_resolver=resolver,
    )


async def test_no_resolver_returns_global() -> None:
    g = DeterministicHashEmbedder(model_id="global", dimension=64)
    svc = _service(g, None)
    assert await svc._embedder_for("t1", "proj-A") is g


async def test_blank_project_returns_global() -> None:
    g = DeterministicHashEmbedder(model_id="global", dimension=64)
    resolver = _FakeResolver(DeterministicHashEmbedder(model_id="proj", dimension=64))
    svc = _service(g, resolver)
    assert await svc._embedder_for("t1", "") is g
    # The resolver is not even consulted when there is no project.
    assert resolver.calls == []


async def test_resolver_hit_returns_project_embedder() -> None:
    g = DeterministicHashEmbedder(model_id="global", dimension=64)
    p = DeterministicHashEmbedder(model_id="proj", dimension=128)
    svc = _service(g, _FakeResolver(p))
    resolved = await svc._embedder_for("t1", "proj-A")
    assert resolved is p


async def test_resolver_miss_falls_back_to_global() -> None:
    g = DeterministicHashEmbedder(model_id="global", dimension=64)
    resolver = _FakeResolver(None)
    svc = _service(g, resolver)
    resolved = await svc._embedder_for("t1", "proj-A")
    assert resolved is g
    assert resolver.calls == [("t1", "proj-A")]
