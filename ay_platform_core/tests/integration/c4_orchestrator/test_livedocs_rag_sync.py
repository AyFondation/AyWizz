# =============================================================================
# File: test_livedocs_rag_sync.py
# Version: 2
# Path: ay_platform_core/tests/integration/c4_orchestrator/test_livedocs_rag_sync.py
# Description: Integration tests for the C4 -> C7 live-docs RAG index SYNC
#              wiring (D-021 / R-400-232) against a real MinIO + Arango: a
#              document write/delete/rename/move drives the expected
#              index/remove calls on the C7 live-docs indexer. Uses a recording
#              fake indexer (the client's own HTTP chain is unit-tested
#              separately) so the wiring — not the transport — is what's pinned.
#              v2 adds the source-file meta `kg_indexed` wiring (R-200-173).
#
# @relation validates:R-400-232
# @relation validates:R-200-173
# =============================================================================

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from minio import Minio

from ay_platform_core.c4_orchestrator.artifacts_service import ArtifactsService
from ay_platform_core.c4_orchestrator.artifacts_storage import ArtifactStorage
from ay_platform_core.c4_orchestrator.db.repository import OrchestratorRepository
from tests.fixtures.containers import MinioEndpoint

pytestmark = pytest.mark.integration

_T = "tenant-a"
_P = "proj-1"


class _FakeIndexer:
    def __init__(self, kg_answer: bool | None = None) -> None:
        self.indexed: list[tuple[str, str]] = []
        self.removed: list[str] = []
        self.kg_queried: list[str] = []
        self._kg_answer = kg_answer

    async def index(
        self, *, tenant_id: str, project_id: str, path: str, content: str,
        actor: str = "system",
    ) -> None:
        self.indexed.append((path, content))

    async def remove(
        self, *, tenant_id: str, project_id: str, path: str, actor: str = "system",
    ) -> None:
        self.removed.append(path)

    async def kg_indexed(
        self, *, tenant_id: str, project_id: str, path: str, actor: str = "system",
    ) -> bool | None:
        self.kg_queried.append(path)
        return self._kg_answer


@pytest_asyncio.fixture(scope="function")
async def svc(
    c4_repo: OrchestratorRepository, minio_container: MinioEndpoint,
) -> tuple[ArtifactsService, _FakeIndexer]:
    client = Minio(
        minio_container.endpoint,
        access_key=minio_container.access_key,
        secret_key=minio_container.secret_key,
        secure=False,
    )
    storage = ArtifactStorage(client, f"livedoc-sync-{uuid.uuid4().hex[:8]}")
    await storage.ensure_bucket()
    indexer = _FakeIndexer()
    service = ArtifactsService(
        repo=c4_repo, storage=storage, gitea=None, livedocs_indexer=indexer,
    )
    return service, indexer


@pytest.mark.asyncio
async def test_write_syncs_index(svc: tuple[ArtifactsService, Any]) -> None:
    service, indexer = svc
    await service.write_document(
        project_id=_P, tenant_id=_T, path="docs/a.md", content="hello world"
    )
    assert indexer.indexed == [("docs/a.md", "hello world")]


@pytest.mark.asyncio
async def test_overwrite_reindexes(svc: tuple[ArtifactsService, Any]) -> None:
    service, indexer = svc
    await service.write_document(
        project_id=_P, tenant_id=_T, path="a.md", content="v1"
    )
    await service.write_document(
        project_id=_P, tenant_id=_T, path="a.md", content="v2"
    )
    assert indexer.indexed == [("a.md", "v1"), ("a.md", "v2")]


@pytest.mark.asyncio
async def test_delete_syncs_removal(svc: tuple[ArtifactsService, Any]) -> None:
    service, indexer = svc
    await service.write_document(
        project_id=_P, tenant_id=_T, path="a.md", content="hello"
    )
    await service.delete_document(project_id=_P, tenant_id=_T, path="a.md")
    assert indexer.removed == ["a.md"]


@pytest.mark.asyncio
async def test_rename_removes_old_and_indexes_new(
    svc: tuple[ArtifactsService, Any],
) -> None:
    service, indexer = svc
    await service.write_document(
        project_id=_P, tenant_id=_T, path="old.md", content="the moved content"
    )
    indexer.indexed.clear()
    await service.rename_document(
        project_id=_P, tenant_id=_T, from_path="old.md", to_path="new.md"
    )
    assert indexer.removed == ["old.md"]
    # The moved doc is re-indexed at its new path with its (unchanged) content.
    assert indexer.indexed == [("new.md", "the moved content")]


@pytest.mark.asyncio
async def test_move_removes_old_and_indexes_new(
    svc: tuple[ArtifactsService, Any],
) -> None:
    service, indexer = svc
    await service.write_document(
        project_id=_P, tenant_id=_T, path="a.md", content="movable"
    )
    indexer.indexed.clear()
    await service.move_document(
        project_id=_P, tenant_id=_T, from_path="a.md", to_dir="sub"
    )
    assert indexer.removed == ["a.md"]
    assert indexer.indexed == [("sub/a.md", "movable")]


@pytest.mark.asyncio
async def test_source_meta_reports_kg_indexed_from_c7(
    c4_repo: OrchestratorRepository, minio_container: MinioEndpoint,
) -> None:
    # R-200-173 / R-400-232 — the meta endpoint surfaces C7's KG-membership
    # answer for the path (resolves Q-200-018).
    client = Minio(
        minio_container.endpoint, access_key=minio_container.access_key,
        secret_key=minio_container.secret_key, secure=False,
    )
    storage = ArtifactStorage(client, f"meta-kg-{uuid.uuid4().hex[:8]}")
    await storage.ensure_bucket()
    indexer = _FakeIndexer(kg_answer=True)
    service = ArtifactsService(
        repo=c4_repo, storage=storage, gitea=None, livedocs_indexer=indexer,
    )
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    await service.create_run(
        project_id=_P, tenant_id=_T, run_id=run_id, label="meta-kg",
    )
    await service.put_file(
        run_id=run_id, project_id=_P, tenant_id=_T,
        relative_path="src/mod.py", data=b"def f():\n    return 1\n",
    )
    meta = await service.get_source_file_meta(
        project_id=_P, tenant_id=_T, run_id=run_id, path="src/mod.py",
    )
    assert meta["kg_indexed"] is True
    assert indexer.kg_queried == ["src/mod.py"]
