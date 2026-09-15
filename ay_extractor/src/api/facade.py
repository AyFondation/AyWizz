# src/api/facade.py — v3
"""Public API facade — single entry point for document analysis.

Usage (Python):
    from ayextractor.api.facade import analyze
    result = await analyze(document, metadata)

Usage (HTTP):
    POST /analyze → kicks off `analyze` asynchronously and returns the run_id.
    See `api/http.py` for the C13 HTTP contract (R-100-125 v2 §2).

D-020 v1 scope: this facade drives Phase 1 (extraction) + Phase 2 (chunking
+ optional decontextualisation / Refine summarisation / Chain of Density).
Phase 3 (concept extraction, graph build, community detection, profiles,
synthesis, critic) and Phase 4 (RAG indexing, consolidator linking) are
out of scope — the platform's C7 indexer ingests the chunks AyExtractor
exports per R-400-223 v2.

D-020 session 4: when `metadata.tenant_id` / `project_id` / `source_id`
are set, the facade ALSO writes the full R-400-220 v2 artifact layout to
MinIO via the configured writer:

    {bucket}/{tenant}/{project}/{source}/runs/{run_id}/
      00_metadata/run_manifest.json    # R-400-221 v2 — model + prompt hashes + timing
      00_metadata/input_fingerprint.json
      01_extraction/...                # enriched text, structure, references, images, tables
      02_chunks/chunks.jsonl           # one ChunkRich per line — R-400-222 v2
      02_chunks/embeddings.jsonl       # one {chunk_id, embedding} per line
      02_chunks/chunk_index.json
      02_chunks/dense_summary.md       # only when quality_tier=high
      02_chunks/screener_log.jsonl     # only when quality_tier=high
      status.json                      # {status, urgency, phases_completed, errors}

When `tenant_id` is None (legacy CLI path), the facade falls back to a
filesystem layout under `metadata.output_path` and writes only the local
files relevant to the local-dev workflow.

See spec §2.1 for original documentation; D-020 R-100-125 v2 / R-400-220 v2
for the C13 MinIO contract.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ayextractor.api.models import AnalysisResult, DocumentInput, Metadata
from ayextractor.cache.fingerprint import compute_fingerprint
from ayextractor.config.settings import Settings
from ayextractor.storage.minio_layout import RunPrefix

if TYPE_CHECKING:
    from ayextractor.llm.embeddings_client import EmbeddingsClient
    from ayextractor.pipeline.state import PipelineState
    from ayextractor.storage.base_output_writer import BaseOutputWriter

logger = logging.getLogger(__name__)


async def analyze(
    document: DocumentInput,
    metadata: Metadata,
    settings: Settings | None = None,
    cache_store: Any = None,  # BaseCacheStore | None
    writer: BaseOutputWriter | None = None,
    embeddings_client: EmbeddingsClient | None = None,
    llm_factory: Any = None,  # Callable(agent_name) -> BaseLLMClient | None
) -> AnalysisResult:
    """Analyze a document through Phase 1 + Phase 2.

    Args:
        document: Input document (content + format + filename).
        metadata: Execution metadata. When `tenant_id`/`project_id`/`source_id`
            are set, the facade writes the R-400-220 v2 MinIO artifact layout;
            otherwise it falls back to the local filesystem under
            `metadata.output_path`.
        settings: Global settings. Loaded from .env if None.
        cache_store: Cache backend for dedup. None = no caching.
        writer: Pre-built MinIO writer. None → derived from settings via
            `storage.writer_factory.create_writer(settings)` when the MinIO
            scope fields are set in metadata.
        embeddings_client: Pre-built embeddings client. None → derived from
            settings when the MinIO scope is set (used to populate
            `02_chunks/embeddings.jsonl` per D-020 v2 §B1).

    Returns:
        AnalysisResult with chunks count + MinIO artifact keys (when applicable)
        + per-agent confidence scores.

    Raises:
        ValueError: If document format is unsupported or content is empty.
    """
    settings = settings or Settings()
    settings = _apply_overrides(settings, metadata)

    document_id = metadata.document_id or _generate_document_id()
    # D-020 session 5 fix — honour a caller-pre-minted run_id so the
    # HTTP wrapper's polled id matches the artifact prefix.
    run_id = metadata.run_id or _generate_run_id(document_id)

    use_minio = bool(metadata.tenant_id and metadata.project_id and metadata.source_id)
    run_prefix: RunPrefix | None = None
    if use_minio:
        run_prefix = RunPrefix(
            tenant_id=metadata.tenant_id,
            project_id=metadata.project_id,
            source_id=metadata.source_id,
            run_id=run_id,
        )

    logger.info(
        "Starting analysis: document_id=%s, run_id=%s, format=%s, mode=%s",
        document_id, run_id, document.format, "minio" if use_minio else "local",
    )

    # --- Phase 0: Fingerprint + cache + initial status ----------------------
    raw_bytes = _resolve_content_bytes(document)
    fingerprint = None
    if cache_store is not None:
        fingerprint = compute_fingerprint(
            raw_bytes=raw_bytes,
            extracted_text="",
            source_format=document.format,
        )
        lookup = await cache_store.lookup_fingerprint(fingerprint)
        if lookup.hit:
            logger.info("Cache hit (%s) for %s", lookup.match_level, document_id)

    if use_minio and writer is None:
        from ayextractor.storage.writer_factory import create_writer
        writer = create_writer(settings)

    started_at = datetime.now(UTC).isoformat()
    if use_minio:
        await _write_status(writer, run_prefix, {
            "status": "running",
            "urgency": metadata.urgency,
            "phases_completed": [],
            "errors": [],
            "started_at": started_at,
        })
        await _write_input_fingerprint(writer, run_prefix, document, raw_bytes)

    # --- Phase 1 (extraction) + Phase 2 (chunking) --------------------------
    # D-020 v1 scope: extract text from the document, then chunk it. The
    # optional Phase-2 enrichment agents (decontextualizer / refine / chain-of-
    # density) are LLM-gated and OFF on the default `minimal` quality_tier, so
    # they are skipped here; embeddings are computed downstream in
    # `_write_minio_artifacts`. Enrichment, when enabled, runs via the dedicated
    # `pipeline/enrichment.py` orchestrator (the generic DAG runner was removed).
    from ayextractor.pipeline.enrichment import resolve_enrichment, run_text_enrichment

    resolved = resolve_enrichment(metadata.quality_tier, metadata.config_overrides)
    # Attribution headers → C8 records the per-source ingestion cost on each
    # `llm_calls` row (R-400-226). Only non-empty values are forwarded.
    _llm_headers = {
        k: v
        for k, v in {
            "X-Run-Id": run_id,
            "X-Source-Id": metadata.source_id or "",
            "X-Tenant-Id": metadata.tenant_id or "",
            "X-Project-Id": metadata.project_id or "",
            "X-Agent-Name": "c13-enrichment",
        }.items()
        if v
    }
    factory: Any = None
    error: Exception | None = None
    state: PipelineState | None = None
    try:
        from ayextractor.chunking.structural_chunker import StructuralChunker
        from ayextractor.extraction.extractor_factory import create_extractor
        from ayextractor.pipeline.state import PipelineState

        _t_ext = time.monotonic()
        extractor = create_extractor(document.format)
        extraction = await extractor.extract(raw_bytes)
        text = extraction.enriched_text or extraction.raw_text
        state = PipelineState(
            run_id=run_id,
            document_id=document_id,
            language=extraction.language,
            extraction_result=extraction,
            enriched_text=text,
            structure=extraction.structure,
            chunks=[],
        )
        state.record_phase("extraction", duration_ms=int((time.monotonic() - _t_ext) * 1000))

        # A1: caption embedded images + FOLD their descriptions into the text
        # BEFORE chunking, so BOTH the chunks AND the map-reduce summary
        # incorporate the image-extracted information (captions are also written
        # as per-image artifacts). Best-effort — a failure never drops the text.
        if resolved.image_vision:
            _t_img, _c_img = time.monotonic(), state.total_llm_calls
            try:
                from ayextractor.extraction.image_pipeline import run_image_enrichment
                from ayextractor.pipeline.llm_factory import LLMFactory
                factory = llm_factory or LLMFactory(settings, headers=_llm_headers)
                await run_image_enrichment(
                    raw_bytes=raw_bytes,
                    fmt=document.format,
                    state=state,
                    llm_factory=factory,
                    writer=writer if use_minio else None,
                    run_prefix=run_prefix,
                )
                text = _augment_text_with_images(text, state.extraction_result.images)
                state.enriched_text = text
            except Exception as exc:  # noqa: BLE001 — best-effort
                logger.exception("Image enrichment failed for run_id=%s", run_id)
                state.errors.append(f"image_enrichment: {exc}")
            state.record_phase(
                "image_caption",
                duration_ms=int((time.monotonic() - _t_img) * 1000),
                llm_calls=state.total_llm_calls - _c_img,
            )

        _t_ch = time.monotonic()
        chunker = StructuralChunker(settings=settings)
        state.chunks = await chunker.chunk(
            text, structure=extraction.structure, source_file=document_id,
        )
        state.record_phase("chunking", duration_ms=int((time.monotonic() - _t_ch) * 1000))
    except Exception as exc:  # noqa: BLE001 — surface every failure to status.json
        error = exc
        logger.exception("Extraction/chunking failed for document_id=%s", document_id)

    # --- Phase 2b-d: reference extraction + LLM text enrichment -------------
    # Reference extraction is deterministic (no LLM). The text agents
    # (decontextualizer / summarizer / densifier) are config-gated and run on
    # the image-augmented chunks. All best-effort.
    if state is not None:
        try:
            from ayextractor.extraction.reference_extractor import extract_references
            state.references = extract_references(state.enriched_text, state.structure, "")
        except Exception:  # noqa: BLE001
            logger.exception("Reference extraction failed for document_id=%s", document_id)
        if resolved.any_text:
            try:
                from ayextractor.pipeline.llm_factory import LLMFactory
                factory = factory or llm_factory or LLMFactory(settings, headers=_llm_headers)
                await run_text_enrichment(
                    state=state,
                    llm_factory=factory,
                    document_title=_document_title(state, document),
                    resolved=resolved,
                )
            except Exception as exc:  # noqa: BLE001 — enrichment is best-effort
                logger.exception("Text enrichment failed for run_id=%s", run_id)
                state.errors.append(f"enrichment: {exc}")

    # --- MinIO artifacts ---------------------------------------------------
    if use_minio and state is not None:
        if embeddings_client is None and settings.embedding_model:
            from ayextractor.llm.embeddings_client import EmbeddingsClient
            embeddings_client = EmbeddingsClient(
                model=settings.embedding_model,
                # Embeddings egress is independent of the chat egress (C8 carries
                # no embeddings model) — use the dedicated endpoint when set.
                api_key=settings.embedding_api_key or settings.openai_api_key,
                base_url=(settings.embedding_base_url or settings.openai_base_url) or None,
                batch_size=settings.embedding_batch_size,
            )
        try:
            await _write_minio_artifacts(
                writer=writer,
                run_prefix=run_prefix,
                state=state,
                embeddings_client=embeddings_client,
                settings=settings,
                metadata=metadata,
                started_at=started_at,
                resolved=resolved,
            )
        except Exception as exc:  # noqa: BLE001
            error = error or exc
            logger.exception("Artifact write failed for run_id=%s", run_id)

    # --- Final status -------------------------------------------------------
    completed_at = datetime.now(UTC).isoformat()
    if use_minio:
        final_status = "failed" if error is not None else "completed"
        await _write_status(writer, run_prefix, {
            "status": final_status,
            "urgency": metadata.urgency,
            "phases_completed": ["01_extraction", "02_chunks"] if state else [],
            "errors": [_format_error(error)] if error else [],
            "started_at": started_at,
            "completed_at": completed_at,
        })

    # --- Output dirs (filesystem fallback for CLI mode) --------------------
    output_dir = Path(metadata.output_path) / document_id
    run_dir_local = output_dir / run_id
    if not use_minio:
        run_dir_local.mkdir(parents=True, exist_ok=True)

    if error is not None and not use_minio:
        # In CLI mode the caller wants the exception; in HTTP mode the
        # status.json carries the failure and we let the run terminate
        # cleanly (the HTTP layer reads status.json).
        raise error

    chunks = state.chunks if state is not None else []
    result = AnalysisResult(
        document_id=document_id,
        run_id=run_id,
        summary=(state.dense_summary or state.refine_summary) if state else "",
        chunks_count=len(chunks),
        output_dir=output_dir,
        run_dir=run_dir_local,
        confidence_scores={
            name: out.confidence
            for name, out in (state.agent_outputs.items() if state else [])
        },
        fingerprint=fingerprint,
        artifact_prefix=run_prefix.prefix if use_minio else None,
        manifest_key=(
            f"{run_prefix.prefix}/00_metadata/run_manifest.json" if use_minio else None
        ),
        chunks_key=(
            f"{run_prefix.prefix}/02_chunks/chunks.jsonl" if use_minio else None
        ),
        embeddings_key=(
            f"{run_prefix.prefix}/02_chunks/embeddings.jsonl" if use_minio else None
        ),
        status_key=(
            f"{run_prefix.prefix}/status.json" if use_minio else None
        ),
    )

    logger.info(
        "Analysis %s: document_id=%s, chunks=%d, llm_calls=%d",
        "failed" if error else "complete",
        document_id,
        result.chunks_count,
        state.total_llm_calls if state else 0,
    )

    return result


# --- MinIO artifact writing -------------------------------------------------


async def _write_status(
    writer: BaseOutputWriter, run_prefix: RunPrefix, payload: dict[str, Any],
) -> None:
    """Atomic-ish status.json write — full payload each call (we don't merge)."""
    from ayextractor.storage.minio_layout import status_key
    await writer.write(status_key(run_prefix), json.dumps(payload, indent=2))


async def _write_input_fingerprint(
    writer: BaseOutputWriter, run_prefix: RunPrefix,
    document: DocumentInput, raw_bytes: bytes,
) -> None:
    """Write the immutable input fingerprint at run start (R-400-220 v2)."""
    import hashlib

    from ayextractor.storage.minio_layout import input_fingerprint_key

    payload = {
        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "size_bytes": len(raw_bytes),
        "format": document.format,
        "filename": document.filename,
    }
    await writer.write(input_fingerprint_key(run_prefix), json.dumps(payload, indent=2))


async def _write_minio_artifacts(
    writer: BaseOutputWriter,
    run_prefix: RunPrefix,
    state: PipelineState,
    embeddings_client: EmbeddingsClient | None,
    settings: Settings,
    metadata: Metadata,
    started_at: str,
    resolved: Any,  # ResolvedEnrichment
) -> dict[str, Any]:
    """Write the R-400-220 v2 artifact layout. Returns embedding metadata
    for the manifest.
    """
    from ayextractor.storage.minio_layout import (
        chunk_index_key,
        chunks_jsonl_key,
        dense_summary_key,
        embeddings_jsonl_key,
        enriched_text_key,
        references_key,
        run_manifest_key,
        run_stats_key,
        structure_key,
    )

    # --- 01_extraction ---
    if state.enriched_text:
        await writer.write(enriched_text_key(run_prefix), state.enriched_text)
    if state.structure is not None:
        await writer.write(structure_key(run_prefix), state.structure.model_dump_json(indent=2))
    if state.references:
        refs_payload = [ref.model_dump() for ref in state.references]
        await writer.write(references_key(run_prefix), json.dumps(refs_payload, indent=2))

    chunks = state.chunks

    # --- Embeddings — computed FIRST so they can be inlined into chunks.jsonl,
    #     which is the shape C7 /ingest-chunks reads (R-400-222 v2 / R-400-223 v3).
    embedding_meta: dict[str, Any] = {}
    embeddings: list[list[float]] | None = None
    if chunks and embeddings_client is not None:
        try:
            # Contextual retrieval : embed the augmented text (section_path +
            # content), NOT content alone, so the vector situates the chunk.
            # The SAME text is persisted as `search_text` for the BM25 arm.
            texts = [_embedding_text(chunk) for chunk in chunks]
            embed_result = await embeddings_client.embed_batch(texts)
            embeddings = list(embed_result.vectors)
            embedding_meta = {
                "embedding_model": embed_result.model,
                "embedding_model_version": embed_result.model_version,
                "embedding_dimension": embed_result.dimension,
                "embedding_total_calls": max(1, len(texts) // embeddings_client._batch_size + 1),
                "embedding_total_tokens": embed_result.total_tokens,
                "embedding_latency_ms": embed_result.latency_ms,
            }
            state.record_phase(
                "embedding",
                duration_ms=embed_result.latency_ms,
                tokens=embed_result.total_tokens,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Embedding pass failed — chunks will carry no vectors")

    # --- 02_chunks/chunks.jsonl (embeddings inlined) ---
    chunks_payload = _chunks_to_jsonl(chunks, run_prefix.run_id, embeddings=embeddings)
    await writer.write(chunks_jsonl_key(run_prefix), chunks_payload)

    # --- 02_chunks/chunk_index.json ---
    index_payload = [
        {
            "chunk_id": chunk.id,
            "seq": chunk.position,
            "char_start": chunk.byte_offset_start,
            "char_end": chunk.byte_offset_end,
            "token_count": chunk.token_count_est,
        }
        for chunk in chunks
    ]
    await writer.write(chunk_index_key(run_prefix), json.dumps(index_payload, indent=2))

    # --- 02_chunks/embeddings.jsonl (kept for the R-400-220 layout) ---
    if embeddings is not None:
        lines = [
            json.dumps({"chunk_id": chunk.id, "embedding": vec})
            for chunk, vec in zip(chunks, embeddings, strict=True)
        ]
        await writer.write(embeddings_jsonl_key(run_prefix), "\n".join(lines))

    # --- 02_chunks/refine_summary.md (when summarization is enabled) ---
    if resolved.summarization and state.refine_summary:
        await writer.write(
            run_prefix.key("02_chunks", "refine_summary.md"), state.refine_summary
        )

    # --- 02_chunks/dense_summary.md (when densification is enabled) ---
    if resolved.densification and state.dense_summary:
        await writer.write(dense_summary_key(run_prefix), state.dense_summary)

    # --- 00_metadata/run_manifest.json (R-400-221 v2) ---
    manifest = {
        "run_id": run_prefix.run_id,
        "ayextractor_version": _ayextractor_version(),
        "monorepo_git_sha": _monorepo_git_sha(),
        "tenant_id": metadata.tenant_id,
        "project_id": metadata.project_id,
        "source_id": metadata.source_id,
        "config": {
            "quality_tier": metadata.quality_tier,
            "urgency": metadata.urgency,
            "decontextualization_enabled": resolved.decontextualization,
            "summarization_enabled": resolved.summarization,
            "densification_enabled": resolved.densification,
            "image_vision_enabled": resolved.image_vision,
            "chunk_token_size": settings.chunk_target_size,
            "chunk_overlap": settings.chunk_overlap,
        },
        "llm_assignments": {},  # populated by the pipeline tracker (session 5+)
        "prompt_hashes": {},
        **embedding_meta,
        "phases": {
            "01_extraction": {"started_at": started_at, "completed_at": None, "origin": "fresh"},
            "02_chunks": {"started_at": started_at, "completed_at": None, "origin": "fresh"},
        },
        "tokens_used": {},
        "screener_stats": None,
        "cost_estimate_usd": 0.0,
        "status": "completed",
        "created_at": started_at,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    await writer.write(run_manifest_key(run_prefix), json.dumps(manifest, indent=2))

    # --- 00_metadata/run_stats.json (R-400-226 observability) ---
    # Per-phase wall-clock + LLM calls + token usage. Dollar cost is NOT computed
    # here — it is sourced authoritatively (snapshot-priced) from the C8 cost
    # receiver, joined by run_id.
    run_stats = {
        "run_id": run_prefix.run_id,
        "document_id": state.document_id,
        "quality_tier": metadata.quality_tier,
        "chunks_count": len(chunks),
        "images_captioned": sum(
            1
            for img in (getattr(state.extraction_result, "images", None) or [])
            if getattr(img, "description", "") and not img.description.startswith("[")
        ),
        "totals": {
            "llm_calls": state.total_llm_calls,
            "tokens": state.total_tokens_used,
            "duration_ms": sum(p.get("duration_ms", 0) for p in state.phase_stats),
        },
        "phases": state.phase_stats,
        "cost_note": (
            "Dollar cost is sourced from the C8 cost receiver "
            "(authoritative, snapshot-priced), joined by run_id."
        ),
    }
    await writer.write(run_stats_key(run_prefix), json.dumps(run_stats, indent=2))

    return embedding_meta


def _chunks_to_jsonl(
    chunks: list[Any], run_id: str, embeddings: list[list[float]] | None = None,
) -> str:
    """Serialise chunks to the R-400-222 v2 ChunkRich shape (one JSON per line).

    When `embeddings` is provided it is inlined per chunk (`embedding` field),
    which is the shape C7 `/ingest-chunks` reads (R-400-223 v3) — C7 takes the
    vectors from this artifact and does not re-embed.
    """
    lines: list[str] = []
    for i, chunk in enumerate(chunks):
        record = {
            "chunk_id": chunk.id,
            "seq": chunk.position,
            "text": chunk.content,
            # The exact text embedded + BM25-indexed (contextual retrieval).
            "search_text": _embedding_text(chunk),
            "original_text": chunk.original_content,
            "context_summary": chunk.context_summary,
            "global_summary": chunk.global_summary,
            "section_path": (
                [s.title for s in chunk.source_sections] if chunk.source_sections else []
            ),
            "char_start": chunk.byte_offset_start,
            "char_end": chunk.byte_offset_end,
            "token_count": chunk.token_count_est,
            "references": [],  # populated when reference_extractor wires into chunks
            "images": chunk.embedded_images,
            "tables": chunk.embedded_tables,
            "extraction_run_id": run_id,
            "embedding": embeddings[i] if embeddings is not None else None,
        }
        lines.append(json.dumps(record))
    return "\n".join(lines)


def _embedding_text(chunk: Any) -> str:
    """Contextual-retrieval text for one chunk : the structural situating
    context (`section_path`) prepended to the (self-contained) chunk content.

    This is the text embedded AND indexed for BM25 (persisted as `search_text`
    in chunks.jsonl), so both retrieval arms are contextual (Anthropic pattern).

    `global_summary` and the cumulative `context_summary` are DELIBERATELY
    excluded : being (near-)identical across chunks, they would pull every
    vector toward the document centroid and erode retrieval discrimination.
    `section_path` is short, per-chunk-distinct and recovers exact section-title
    token matches the dense arm misses. Falls back to bare content when the
    document has no section structure (no regression vs. content-only)."""
    sections = [s.title for s in chunk.source_sections] if chunk.source_sections else []
    sections = [t for t in sections if t]
    if not sections:
        return chunk.content
    return f"{' > '.join(sections)}\n\n{chunk.content}"


def _augment_text_with_images(text: str, images: list[Any]) -> str:
    """A1 — append a 'Figures' section listing each captioned image (id + page +
    description) so the chunker AND the map-reduce summary incorporate the
    image-extracted content. Placeholder/failed captions (description starting
    with '[') are skipped. PDF/DOCX extractors emit no positional image anchor
    in the text, so captions are grouped at the end (exact per-position anchoring
    is a follow-up — A2)."""
    captioned = [
        img
        for img in images
        if getattr(img, "description", "") and not img.description.startswith("[")
    ]
    if not captioned:
        return text
    lines = ["", "", "## Figures (extracted images)", ""]
    for img in captioned:
        page = f" (page {img.source_page})" if getattr(img, "source_page", None) else ""
        lines.append(f"- **{img.id}**{page}: {img.description}")
    return text + "\n".join(lines)


def _document_title(state: PipelineState, document: DocumentInput) -> str:
    """Best-effort document title for the enrichment agents: the first H1 in
    the structure, else the filename stem, else the document id."""
    structure = getattr(state, "structure", None)
    sections = getattr(structure, "sections", None) if structure is not None else None
    if sections:
        for sec in sections:
            if getattr(sec, "level", None) == 1 and getattr(sec, "title", ""):
                return sec.title
    if document.filename:
        return Path(document.filename).stem
    return state.document_id or ""


def _ayextractor_version() -> str:
    try:
        from ayextractor.version import __version__
        return __version__
    except Exception:  # noqa: BLE001
        return "unknown"


def _monorepo_git_sha() -> str:
    """Best-effort: read the git sha stamped at image build time via env."""
    import os
    return os.environ.get("MONOREPO_GIT_SHA", "unknown")


def _format_error(exc: Exception | None) -> dict[str, str]:
    if exc is None:
        return {}
    return {
        "error_class": exc.__class__.__name__,
        "error_message": str(exc),
    }


# --- Helpers (unchanged from v2) -------------------------------------------


def _apply_overrides(settings: Settings, metadata: Metadata) -> Settings:
    """Apply per-document config overrides if provided."""
    if metadata.config_overrides is None:
        return settings
    overrides = metadata.config_overrides.model_dump(exclude_none=True)
    if not overrides:
        return settings
    current = settings.model_dump()
    current.update(overrides)
    return Settings(**current)


def _generate_document_id() -> str:
    """Generate a unique document ID: yyyymmdd_hhmmss_{uuid4_short}."""
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:8]
    return f"{ts}_{short}"


def _generate_run_id(document_id: str) -> str:
    """Generate a run ID: yyyymmdd_hhmm_{uuid5_12}."""
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M")
    ns = uuid.NAMESPACE_DNS
    run_uuid = uuid.uuid5(ns, f"{document_id}_{ts}")
    return f"{ts}_{run_uuid.hex[:12]}"


def _resolve_content_bytes(document: DocumentInput) -> bytes:
    """Extract raw bytes from DocumentInput.content."""
    content = document.content
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode("utf-8")
    if isinstance(content, Path):
        return content.read_bytes()
    if isinstance(content, list):
        return b"".join(p.read_bytes() for p in content)
    msg = f"Unsupported content type: {type(content)}"
    raise ValueError(msg)
