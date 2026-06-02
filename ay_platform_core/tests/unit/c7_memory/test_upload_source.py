# =============================================================================
# File: test_upload_source.py
# Version: 1
# Path: ay_platform_core/tests/unit/c7_memory/test_upload_source.py
# Description: Unit tests for the C7 multipart upload edge
#              `store_raw_upload_and_trigger` (R-100-081 v3): C7 writes the
#              raw bytes to the C13 input bucket, records a `pending`
#              source row, and triggers the C12 webhook with METADATA ONLY.
#              C12/n8n never touches the bytes.
# =============================================================================

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from ay_platform_core.c7_memory.c12_client import C12WebhookError
from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.embedding.deterministic import (
    DeterministicHashEmbedder,
)
from ay_platform_core.c7_memory.models import EnrichmentConfig, ParseStatus
from ay_platform_core.c7_memory.service import MemoryService


class _FakeRepo:
    def __init__(self) -> None:
        self.sources: dict[str, dict[str, Any]] = {}
        self.project_configs: dict[str, dict[str, Any]] = {}
        self.quota_used = 0

    async def upsert_source(self, row: dict[str, Any]) -> None:
        self.sources[row["_key"]] = row

    async def quota_totals(self, tenant_id: str, project_id: str) -> dict[str, int]:
        return {"bytes_used": self.quota_used, "source_count": len(self.sources)}

    async def get_project_config(
        self, tenant_id: str, project_id: str
    ) -> dict[str, Any] | None:
        return self.project_configs.get(f"{tenant_id}:{project_id}")

    async def upsert_project_config(
        self, tenant_id: str, project_id: str, config: dict[str, Any]
    ) -> None:
        self.project_configs[f"{tenant_id}:{project_id}"] = {
            "_key": f"{tenant_id}:{project_id}",
            "tenant_id": tenant_id,
            "project_id": project_id,
            **config,
        }


class _FakeStorage:
    def __init__(self) -> None:
        self.bucket_puts: list[dict[str, Any]] = []
        self.blob_puts: list[dict[str, Any]] = []

    async def put_to_bucket(
        self, *, bucket: str, key: str, data: bytes, content_type: str = "x"
    ) -> Any:
        self.bucket_puts.append({"bucket": bucket, "key": key, "size": len(data)})

    async def put_source_blob(self, **kwargs: Any) -> Any:
        self.blob_puts.append(kwargs)


class _FakeC12:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def trigger_ingestion(self, payload: dict[str, Any]) -> None:
        self.calls.append(payload)
        if self.fail:
            raise C12WebhookError("boom")


def _make_service(
    repo: _FakeRepo, storage: _FakeStorage, c12: _FakeC12
) -> MemoryService:
    embedder = DeterministicHashEmbedder(dimension=128)
    config = MemoryConfig(default_quota_bytes=10 * 1024 * 1024)
    return MemoryService(
        config=config,
        repo=repo,  # type: ignore[arg-type]
        embedder=embedder,
        storage=storage,  # type: ignore[arg-type]
        c12_client=c12,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_upload_stores_raw_and_triggers_c12_metadata_only() -> None:
    repo, storage, c12 = _FakeRepo(), _FakeStorage(), _FakeC12()
    service = _make_service(repo, storage, c12)

    result = await service.store_raw_upload_and_trigger(
        tenant_id="t1",
        project_id="p1",
        source_id="src-1",
        filename="report.pdf",
        mime_type="application/pdf",
        source_format="pdf",
        data=b"%PDF-1.7 raw bytes",
        uploaded_by="user-alice",
    )

    # Pending row returned.
    assert result.parse_status == ParseStatus.PENDING
    assert result.source_id == "src-1"
    # Raw written into the C13 input bucket under the sources/ prefix.
    assert len(storage.bucket_puts) == 1
    put = storage.bucket_puts[0]
    assert put["bucket"] == service._config.c13_artifacts_bucket
    assert put["key"] == "sources/t1/p1/src-1/raw.pdf"
    # C12 triggered with METADATA ONLY — no file bytes in the payload.
    assert len(c12.calls) == 1
    meta = c12.calls[0]
    assert meta["raw_object_key"] == "sources/t1/p1/src-1/raw.pdf"
    assert meta["tenant_id"] == "t1"
    assert meta["format"] == "pdf"
    assert "content_b64" not in meta
    assert "data" not in meta
    # No project config set → default tier, no overrides forwarded.
    assert meta["quality_tier"] == "minimal"
    assert "config_overrides" not in meta


@pytest.mark.asyncio
async def test_upload_forwards_project_enrichment_config() -> None:
    """R-400-224: the per-project enrichment config drives the run — its tier +
    config_overrides (incl. the independent image-analyzer model) are forwarded
    to C12/C13 in the webhook payload."""
    repo, storage, c12 = _FakeRepo(), _FakeStorage(), _FakeC12()
    service = _make_service(repo, storage, c12)
    await service.set_project_enrichment_config(
        "t1",
        "p1",
        EnrichmentConfig(
            quality_tier="high",
            image_vision_enabled=False,
            chain_of_density_iterations=3,
            image_analyzer_model="openai:gpt-4o-mini",
        ),
    )

    await service.store_raw_upload_and_trigger(
        tenant_id="t1",
        project_id="p1",
        source_id="src-2",
        filename="r.pdf",
        mime_type="application/pdf",
        source_format="pdf",
        data=b"%PDF bytes",
        uploaded_by="user-alice",
    )

    meta = c12.calls[0]
    assert meta["quality_tier"] == "high"
    overrides = meta["config_overrides"]
    assert overrides["image_vision_enabled"] is False
    assert overrides["chain_of_density_iterations"] == 3
    # The image model is independent of the text agents (its own llm_assignment).
    assert overrides["llm_assignments"] == {"image_analyzer": "openai:gpt-4o-mini"}


def test_enrichment_config_to_overrides_only_set_fields() -> None:
    # Default config → no overrides (the tier preset alone drives C13).
    assert EnrichmentConfig().to_config_overrides() == {}
    # Only the explicitly-set fields are emitted; the image model maps to an
    # `image_analyzer` llm-assignment, separate from the text agents.
    overrides = EnrichmentConfig(
        quality_tier="standard",
        decontextualization_enabled=True,
        image_analyzer_model="ollama:llava",
    ).to_config_overrides()
    assert overrides == {
        "decontextualization_enabled": True,
        "llm_assignments": {"image_analyzer": "ollama:llava"},
    }


@pytest.mark.asyncio
async def test_get_set_project_enrichment_config_roundtrip() -> None:
    repo, storage, c12 = _FakeRepo(), _FakeStorage(), _FakeC12()
    service = _make_service(repo, storage, c12)

    # Unset → default (minimal).
    default = await service.get_project_enrichment_config("t1", "p1")
    assert default.quality_tier == "minimal"

    await service.set_project_enrichment_config(
        "t1", "p1", EnrichmentConfig(quality_tier="high", summarization_enabled=True)
    )
    got = await service.get_project_enrichment_config("t1", "p1")
    assert got.quality_tier == "high"
    assert got.summarization_enabled is True


@pytest.mark.asyncio
async def test_upload_webhook_failure_marks_failed_and_502() -> None:
    repo, storage, c12 = _FakeRepo(), _FakeStorage(), _FakeC12(fail=True)
    service = _make_service(repo, storage, c12)

    with pytest.raises(HTTPException) as exc_info:
        await service.store_raw_upload_and_trigger(
            tenant_id="t1",
            project_id="p1",
            source_id="src-2",
            filename="a.md",
            mime_type="text/markdown",
            source_format="md",
            data=b"# hi",
            uploaded_by="user-alice",
        )
    assert exc_info.value.status_code == 502
    # The source row was marked failed (observable for the UI).
    row = next(iter(repo.sources.values()))
    assert row["parse_status"] == ParseStatus.FAILED.value
    assert "trigger failed" in row["parse_error"]


@pytest.mark.asyncio
async def test_upload_empty_data_400() -> None:
    repo, storage, c12 = _FakeRepo(), _FakeStorage(), _FakeC12()
    service = _make_service(repo, storage, c12)
    with pytest.raises(HTTPException) as exc_info:
        await service.store_raw_upload_and_trigger(
            tenant_id="t1",
            project_id="p1",
            source_id="src-3",
            filename="empty.txt",
            mime_type="text/plain",
            source_format="txt",
            data=b"",
            uploaded_by="user-alice",
        )
    assert exc_info.value.status_code == 400
