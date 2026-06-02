# =============================================================================
# File: test_source_runs_artifacts.py
# Version: 1
# Path: ay_platform_core/tests/unit/c7_memory/test_source_runs_artifacts.py
# Description: Unit tests for the run/artifact browsing + chunk content + zip
#              download surface (R-400-221 transparency):
#                - list_source_runs (parser/extractor version, active marking,
#                  missing manifest, 404/503)
#                - list_run_artifacts (sorting, content-type, 404/503)
#                - get_run_artifact (bytes/type/filename, 404, 400 traversal)
#                - build_run_artifacts_zip (bundle, 404)
#                - get_chunk_content (content/context, 404)
#                - build_chunks_zip (one JSON per chunk, 404)
# =============================================================================

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import pytest
from fastapi import HTTPException

from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.embedding.deterministic import (
    DeterministicHashEmbedder,
)
from ay_platform_core.c7_memory.models import ParseStatus
from ay_platform_core.c7_memory.service import MemoryService
from ay_platform_core.c7_memory.storage.minio_storage import ObjectEntry

_BUCKET = "c13-extractor-artifacts"
_T, _P, _S = "t1", "p1", "src-1"


class _FakeRepo:
    def __init__(
        self,
        *,
        source: dict[str, Any] | None,
        chunks: list[dict[str, Any]],
    ) -> None:
        self._source = source
        self._chunks = chunks

    async def get_source(self, t: str, p: str, s: str) -> dict[str, Any] | None:
        return self._source

    async def list_chunks_for_source(
        self, t: str, p: str, s: str
    ) -> list[dict[str, Any]]:
        return self._chunks

    async def get_chunk(
        self, t: str, p: str, s: str, chunk_id: str
    ) -> dict[str, Any] | None:
        for c in self._chunks:
            if c.get("chunk_id") == chunk_id:
                return c
        return None


class _FakeStorage:
    """In-memory MinIO double keyed by full object key."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = objects

    async def list_run_ids(self, *, bucket: str, runs_prefix: str) -> list[str]:
        run_ids: set[str] = set()
        for key in self._objects:
            if key.startswith(runs_prefix):
                tail = key[len(runs_prefix):].strip("/")
                if tail:
                    run_ids.add(tail.split("/", 1)[0])
        return list(run_ids)

    async def list_artifacts(
        self, *, bucket: str, prefix: str
    ) -> list[ObjectEntry]:
        return [
            ObjectEntry(key=k, size=len(v))
            for k, v in self._objects.items()
            if k.startswith(prefix)
        ]

    async def get_extraction_artifact(self, *, bucket: str, key: str) -> bytes:
        if key not in self._objects:
            raise FileNotFoundError(f"{bucket}/{key}")
        return self._objects[key]


def _service(repo: _FakeRepo, storage: _FakeStorage | None) -> MemoryService:
    return MemoryService(
        config=MemoryConfig(c13_artifacts_bucket=_BUCKET),
        repo=repo,  # type: ignore[arg-type]
        embedder=DeterministicHashEmbedder(dimension=64),
        storage=storage,  # type: ignore[arg-type]
    )


def _source_row() -> dict[str, Any]:
    return {
        "source_id": _S,
        "project_id": _P,
        "mime_type": "text/markdown",
        "size_bytes": 1024,
        "uploaded_by": "alice",
        "uploaded_at": "2026-06-01T00:00:00+00:00",
        "parse_status": ParseStatus.INDEXED.value,
        "parse_error": None,
        "chunk_count": 2,
        "model_id": "all-minilm",
        "processing_version": "chunk=512/64;embed=all-minilm",
        "minio_raw_path": "sources/t1/p1/src-1/raw.md",
    }


def _chunk_row(chunk_id: str, seq: int, run_id: str) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "chunk_index": seq,
        "content": f"chunk {seq} body",
        "context": f"ctx {seq}",
        "vector": [0.1] * 64,
        "metadata": {
            "extraction_run_id": run_id,
            "token_count": 5,
            "char_start": seq * 10,
            "char_end": seq * 10 + 8,
            "original_text": f"original {seq}",
            "section_path": ["Intro"],
        },
    }


def _run_prefix(run_id: str) -> str:
    return f"{_T}/{_P}/{_S}/runs/{run_id}/"


def _manifest(run_id: str, version: str) -> bytes:
    return json.dumps(
        {
            "run_id": run_id,
            "ayextractor_version": version,
            "monorepo_git_sha": "abc1234",
            "status": "completed",
            "created_at": "2026-06-01T10:00:00+00:00",
            "completed_at": "2026-06-01T10:00:05+00:00",
        }
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# list_source_runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_runs_surfaces_versions_and_marks_active() -> None:
    """Two runs ; the indexed one (run-new) is active and carries chunk_count,
    the other is inactive with chunk_count None. Both expose their parser
    version from the manifest."""
    objects = {
        f"{_run_prefix('run-old')}00_metadata/run_manifest.json": _manifest(
            "run-old", "0.9.0"
        ),
        f"{_run_prefix('run-new')}00_metadata/run_manifest.json": _manifest(
            "run-new", "1.2.0"
        ),
    }
    repo = _FakeRepo(
        source=_source_row(),
        chunks=[_chunk_row("c0", 0, "run-new"), _chunk_row("c1", 1, "run-new")],
    )
    svc = _service(repo, _FakeStorage(objects))

    listing = await svc.list_source_runs(_T, _P, _S)

    assert listing.active_run_id == "run-new"
    by_id = {r.run_id: r for r in listing.runs}
    assert set(by_id) == {"run-old", "run-new"}
    assert by_id["run-new"].is_active is True
    assert by_id["run-new"].ayextractor_version == "1.2.0"
    assert by_id["run-new"].git_sha == "abc1234"
    assert by_id["run-new"].chunk_count == 2
    assert by_id["run-old"].is_active is False
    assert by_id["run-old"].ayextractor_version == "0.9.0"
    assert by_id["run-old"].chunk_count is None
    assert by_id["run-new"].created_at is not None


@pytest.mark.asyncio
async def test_list_runs_tolerates_missing_manifest() -> None:
    """A run whose manifest is absent still lists, with version None."""
    objects = {
        f"{_run_prefix('run-x')}02_chunks/chunks.jsonl": b"{}",
    }
    repo = _FakeRepo(source=_source_row(), chunks=[])
    svc = _service(repo, _FakeStorage(objects))

    listing = await svc.list_source_runs(_T, _P, _S)
    assert [r.run_id for r in listing.runs] == ["run-x"]
    assert listing.runs[0].ayextractor_version is None
    assert listing.active_run_id is None


@pytest.mark.asyncio
async def test_list_runs_404_when_source_absent() -> None:
    svc = _service(_FakeRepo(source=None, chunks=[]), _FakeStorage({}))
    with pytest.raises(HTTPException) as exc:
        await svc.list_source_runs(_T, _P, _S)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_list_runs_503_without_storage() -> None:
    svc = _service(_FakeRepo(source=_source_row(), chunks=[]), None)
    with pytest.raises(HTTPException) as exc:
        await svc.list_source_runs(_T, _P, _S)
    assert exc.value.status_code == 503


# ---------------------------------------------------------------------------
# list_run_artifacts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_artifacts_sorted_with_content_type() -> None:
    rp = _run_prefix("run-1")
    objects = {
        f"{rp}02_chunks/chunks.jsonl": b"line",
        f"{rp}00_metadata/run_manifest.json": _manifest("run-1", "1.0.0"),
    }
    svc = _service(_FakeRepo(source=_source_row(), chunks=[]), _FakeStorage(objects))

    listing = await svc.list_run_artifacts(_T, _P, _S, "run-1")
    paths = [e.path for e in listing.entries]
    assert paths == ["00_metadata/run_manifest.json", "02_chunks/chunks.jsonl"]
    manifest_entry = listing.entries[0]
    assert manifest_entry.content_type == "application/json"
    assert manifest_entry.size_bytes > 0
    assert listing.prefix == rp


@pytest.mark.asyncio
async def test_list_artifacts_404_when_run_empty() -> None:
    svc = _service(_FakeRepo(source=_source_row(), chunks=[]), _FakeStorage({}))
    with pytest.raises(HTTPException) as exc:
        await svc.list_run_artifacts(_T, _P, _S, "ghost")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_list_artifacts_503_without_storage() -> None:
    svc = _service(_FakeRepo(source=_source_row(), chunks=[]), None)
    with pytest.raises(HTTPException) as exc:
        await svc.list_run_artifacts(_T, _P, _S, "run-1")
    assert exc.value.status_code == 503


# ---------------------------------------------------------------------------
# get_run_artifact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_artifact_returns_bytes_type_filename() -> None:
    rp = _run_prefix("run-1")
    objects = {f"{rp}00_metadata/run_manifest.json": _manifest("run-1", "1.0.0")}
    svc = _service(_FakeRepo(source=_source_row(), chunks=[]), _FakeStorage(objects))

    data, ctype, filename = await svc.get_run_artifact(
        _T, _P, _S, "run-1", "00_metadata/run_manifest.json"
    )
    assert json.loads(data)["ayextractor_version"] == "1.0.0"
    assert ctype == "application/json"
    assert filename == "run_manifest.json"


@pytest.mark.asyncio
async def test_get_artifact_404_when_missing() -> None:
    svc = _service(_FakeRepo(source=_source_row(), chunks=[]), _FakeStorage({}))
    with pytest.raises(HTTPException) as exc:
        await svc.get_run_artifact(_T, _P, _S, "run-1", "nope.json")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("evil", ["../secret", "a/../../etc/passwd", "", "/"])
async def test_get_artifact_400_on_path_traversal(evil: str) -> None:
    svc = _service(_FakeRepo(source=_source_row(), chunks=[]), _FakeStorage({}))
    with pytest.raises(HTTPException) as exc:
        await svc.get_run_artifact(_T, _P, _S, "run-1", evil)
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# build_run_artifacts_zip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_run_zip_bundles_every_artifact() -> None:
    rp = _run_prefix("run-1")
    objects = {
        f"{rp}00_metadata/run_manifest.json": _manifest("run-1", "1.0.0"),
        f"{rp}02_chunks/chunks.jsonl": b'{"chunk_id":"c0"}',
    }
    svc = _service(_FakeRepo(source=_source_row(), chunks=[]), _FakeStorage(objects))

    blob = await svc.build_run_artifacts_zip(_T, _P, _S, "run-1")
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = sorted(zf.namelist())
        assert names == ["00_metadata/run_manifest.json", "02_chunks/chunks.jsonl"]
        assert zf.read("02_chunks/chunks.jsonl") == b'{"chunk_id":"c0"}'


@pytest.mark.asyncio
async def test_build_run_zip_404_when_empty() -> None:
    svc = _service(_FakeRepo(source=_source_row(), chunks=[]), _FakeStorage({}))
    with pytest.raises(HTTPException) as exc:
        await svc.build_run_artifacts_zip(_T, _P, _S, "ghost")
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# get_chunk_content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_chunk_content_returns_body_and_context() -> None:
    repo = _FakeRepo(
        source=_source_row(), chunks=[_chunk_row("c0", 0, "run-new")]
    )
    svc = _service(repo, _FakeStorage({}))

    content = await svc.get_chunk_content(_T, _P, _S, "c0")
    assert content.chunk_id == "c0"
    assert content.content == "chunk 0 body"
    assert content.context == "ctx 0"
    assert content.original_text == "original 0"
    assert content.section_path == ["Intro"]
    assert content.token_count == 5


@pytest.mark.asyncio
async def test_get_chunk_content_404_when_absent() -> None:
    repo = _FakeRepo(source=_source_row(), chunks=[])
    svc = _service(repo, _FakeStorage({}))
    with pytest.raises(HTTPException) as exc:
        await svc.get_chunk_content(_T, _P, _S, "nope")
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# build_chunks_zip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_chunks_zip_one_json_per_chunk() -> None:
    repo = _FakeRepo(
        source=_source_row(),
        chunks=[_chunk_row("a:0", 0, "run-new"), _chunk_row("a:1", 1, "run-new")],
    )
    svc = _service(repo, _FakeStorage({}))

    blob = await svc.build_chunks_zip(_T, _P, _S)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = sorted(zf.namelist())
        # chunk_id `:` sanitised to `_`.
        assert names == ["0000_a_0.json", "0001_a_1.json"]
        first = json.loads(zf.read("0000_a_0.json"))
        assert first["content"] == "chunk 0 body"
        assert first["chunk_id"] == "a:0"


@pytest.mark.asyncio
async def test_build_chunks_zip_404_when_no_chunks() -> None:
    svc = _service(_FakeRepo(source=_source_row(), chunks=[]), _FakeStorage({}))
    with pytest.raises(HTTPException) as exc:
        await svc.build_chunks_zip(_T, _P, _S)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_build_chunks_zip_404_when_source_absent() -> None:
    svc = _service(_FakeRepo(source=None, chunks=[]), _FakeStorage({}))
    with pytest.raises(HTTPException) as exc:
        await svc.build_chunks_zip(_T, _P, _S)
    assert exc.value.status_code == 404
