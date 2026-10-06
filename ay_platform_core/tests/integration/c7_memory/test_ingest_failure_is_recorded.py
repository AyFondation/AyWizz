# =============================================================================
# File: test_ingest_failure_is_recorded.py
# Version: 1
# Path: ay_platform_core/tests/integration/c7_memory/test_ingest_failure_is_recorded.py
# Description: A rejected chunk hand-off SHALL be recorded on the source row,
#              not only raised to the caller.
#
#              WHY THIS EXISTS. The upload path answers the user `202
#              Accepted` and records a `pending` source; the chunks arrive
#              later through `ingest_chunks_from_extractor`, called by C12
#              (n8n) — a machine. When C7 refused that hand-off it raised a
#              422 and nothing else: n8n logged `AxiosError: Request failed
#              with status code 422` into a container log and gave up, and
#              the source sat at `pending` FOREVER with nothing to explain
#              it. Observed exactly that way in the compose stack, where
#              C13's embedding provider 404s and its best-effort pass emits
#              `Embedding pass failed — chunks will carry no vectors` while
#              still reporting the run as a success.
#
#              So the failure was loud at the HTTP boundary and silent to
#              every human. These tests pin the fix: the source becomes
#              FAILED and `parse_error` names the cause, which is what the
#              sources list renders.
#
# @relation validates:R-400-020
# @relation validates:R-400-222
# =============================================================================

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from ay_platform_core.c7_memory.models import (
    ChunkIngestRequest,
    ChunkRich,
    ParseStatus,
)
from ay_platform_core.c7_memory.service import MemoryService
from ay_platform_core.c7_memory.storage.minio_storage import MemorySourceStorage

pytestmark = pytest.mark.integration

_TENANT = "t-ingest-fail"
_PROJECT = "p-ingest-fail"


async def _seed_pending_source(
    service: MemoryService, source_id: str, *, project_id: str = _PROJECT
) -> None:
    """Record the `pending` row the upload path would have written.

    The failure being tested only makes sense against an existing source:
    that row is what the user is looking at while they wait.
    """
    assert service._repo is not None
    await service._repo.upsert_source({
        "_key": f"{_TENANT}:{project_id}:{source_id}",
        "tenant_id": _TENANT,
        "project_id": project_id,
        "source_id": source_id,
        "minio_raw_path": None,
        "minio_parsed_path": None,
        "minio_chunks_path": None,
        "mime_type": "text/plain",
        "size_bytes": 42,
        "uploaded_by": "alice",
        "uploaded_at": datetime.now(UTC).isoformat(),
        "parse_status": ParseStatus.PENDING.value,
        "parse_error": None,
        "chunk_count": 0,
        "model_id": None,
        "processing_version": None,
        "document_summary": None,
    })


async def _stage_c13_artifacts(
    service: MemoryService,
    source_id: str,
    *,
    run_id: str,
    embeddings: list[list[float] | None],
    manifest_dimension: int,
) -> None:
    """Write a C13 run's manifest + chunks.jsonl, with the given vectors.

    `embeddings=[None, None]` reproduces the real-world artifact set a C13
    run produces when its embedding provider is unreachable: complete
    chunks, no vectors, run reported successful.
    """
    assert service._storage is not None
    bucket = service._config.c13_artifacts_bucket
    chunks = [
        ChunkRich(
            chunk_id=f"{source_id}:{idx:04d}",
            seq=idx,
            text=f"chunk {idx} body text",
            original_text=f"chunk {idx} body text",
            section_path=[],
            char_start=0,
            char_end=20,
            token_count=4,
            references=[],
            images=[],
            tables=[],
            extraction_run_id=run_id,
            embedding=vector,
        )
        for idx, vector in enumerate(embeddings)
    ]
    await service._storage.put_to_bucket(
        bucket=bucket,
        key=MemorySourceStorage.c13_artifact_key(
            _TENANT, _PROJECT, source_id, run_id, "00_metadata/run_manifest.json"
        ),
        data=json.dumps({
            "embedding_model": "deterministic-hash-v1",
            "embedding_model_version": "test-v1",
            "embedding_dimension": manifest_dimension,
        }).encode("utf-8"),
        content_type="application/json",
    )
    await service._storage.put_to_bucket(
        bucket=bucket,
        key=MemorySourceStorage.c13_artifact_key(
            _TENANT, _PROJECT, source_id, run_id, "02_chunks/chunks.jsonl"
        ),
        data="\n".join(json.dumps(c.model_dump()) for c in chunks).encode("utf-8"),
        content_type="application/x-ndjson",
    )


@pytest.mark.asyncio
async def test_missing_embeddings_mark_the_source_failed_with_a_reason(
    c7_upload_service: MemoryService,
) -> None:
    """The exact production failure: C13 artifacts with no vectors."""
    service = c7_upload_service
    source_id = f"src-{uuid.uuid4().hex[:8]}"
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    await _seed_pending_source(service, source_id)
    await _stage_c13_artifacts(
        service,
        source_id,
        run_id=run_id,
        embeddings=[None, None],
        manifest_dimension=service._embedder.dimension,
    )

    with pytest.raises(HTTPException) as excinfo:
        await service.ingest_chunks_from_extractor(
            tenant_id=_TENANT,
            project_id=_PROJECT,
            source_id=source_id,
            payload=ChunkIngestRequest(
                extraction_run_id=run_id,
                uploaded_by="alice",
                mime_type="text/plain",
                tenant_id=_TENANT,
            ),
        )
    assert excinfo.value.status_code == 422

    row = await service._repo.get_source(_TENANT, _PROJECT, source_id)
    assert row is not None
    assert row["parse_status"] == ParseStatus.FAILED.value, (
        "the source stayed at "
        f"{row['parse_status']!r} after a refused hand-off — which is the "
        "defect: the user sees `pending` forever and the only trace of the "
        "real failure is an AxiosError in the n8n container log."
    )
    # The reason must be actionable, not just present: it names the count,
    # the run, and where to look. A bare "ingest failed" would satisfy a
    # weaker assertion while leaving the operator exactly as stuck.
    error = row["parse_error"]
    assert error, "parse_error is empty, so the status says nothing useful"
    assert "missing embedding" in error
    assert run_id in error
    assert "Embedding pass failed" in error


@pytest.mark.asyncio
async def test_dimension_mismatch_marks_the_source_failed(
    c7_upload_service: MemoryService,
) -> None:
    """The other refusal on that path, recorded the same way.

    Pins that the fix covers BOTH rejections rather than just the one that
    was observed in production — the 400 branch is the same silent-failure
    shape.
    """
    service = c7_upload_service
    source_id = f"src-{uuid.uuid4().hex[:8]}"
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    await _seed_pending_source(service, source_id)
    await _stage_c13_artifacts(
        service,
        source_id,
        run_id=run_id,
        # Vectors present but 3-dimensional, manifest claims the embedder's
        # real width.
        embeddings=[[0.1, 0.2, 0.3]],
        manifest_dimension=service._embedder.dimension,
    )

    with pytest.raises(HTTPException) as excinfo:
        await service.ingest_chunks_from_extractor(
            tenant_id=_TENANT,
            project_id=_PROJECT,
            source_id=source_id,
            payload=ChunkIngestRequest(
                extraction_run_id=run_id,
                uploaded_by="alice",
                mime_type="text/plain",
                tenant_id=_TENANT,
            ),
        )
    assert excinfo.value.status_code == 400

    row = await service._repo.get_source(_TENANT, _PROJECT, source_id)
    assert row is not None
    assert row["parse_status"] == ParseStatus.FAILED.value
    assert "embedding_dimension mismatch" in row["parse_error"]


@pytest.mark.asyncio
async def test_a_successful_hand_off_leaves_the_source_indexed(
    c7_upload_service: MemoryService,
) -> None:
    """The discriminating half.

    Without it, both tests above would also pass against an implementation
    that marked every source FAILED unconditionally.
    """
    service = c7_upload_service
    source_id = f"src-{uuid.uuid4().hex[:8]}"
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    await _seed_pending_source(service, source_id)
    vectors = await service._embedder.embed_batch(["chunk 0 body text"])
    await _stage_c13_artifacts(
        service,
        source_id,
        run_id=run_id,
        embeddings=[vectors[0]],
        manifest_dimension=service._embedder.dimension,
    )

    public = await service.ingest_chunks_from_extractor(
        tenant_id=_TENANT,
        project_id=_PROJECT,
        source_id=source_id,
        payload=ChunkIngestRequest(
            extraction_run_id=run_id,
            uploaded_by="alice",
            mime_type="text/plain",
            tenant_id=_TENANT,
        ),
    )
    assert public.parse_status == ParseStatus.INDEXED
    assert public.chunk_count == 1

    row = await service._repo.get_source(_TENANT, _PROJECT, source_id)
    assert row is not None
    assert row["parse_status"] == ParseStatus.INDEXED.value
    assert row["parse_error"] is None
