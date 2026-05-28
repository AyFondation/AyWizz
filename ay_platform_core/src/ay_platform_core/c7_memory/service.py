# =============================================================================
# File: service.py
# Version: 3
# Path: ay_platform_core/src/ay_platform_core/c7_memory/service.py
# Description: Facade for the C7 Memory Service. Wires ingestion (parse +
#              chunk + embed + index), federated retrieval, entity-event
#              handlers, and the admin surface.
#
#              v2 (Phase F.2): hybrid retrieval. After the initial
#              vector scan + score, if a KG repo is wired and the graph
#              is non-empty for the project, expand the candidate pool
#              with chunks of source_ids reachable in 1 hop from the
#              top-K seeds (proposition A — pulls in chunks that
#              `scan_cap` may have cut off), then apply a multiplicative
#              boost to chunks whose source_id is graph-related to a
#              seed (proposition B — small ranking bump for
#              contextually-related-but-not-direct-vector matches).
#              Both knobs are configurable; default 1-hop, boost 1.3.
#
# @relation implements:R-400-020
# @relation implements:R-400-030
# @relation implements:R-400-031
# @relation implements:R-400-040
# @relation implements:R-400-042
# @relation implements:R-400-070
# @relation implements:R-400-071
# @relation implements:R-400-207
# @relation implements:R-400-208
# =============================================================================

from __future__ import annotations

import contextlib
import hashlib
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, Request, status

from ay_platform_core.c7_memory.artifacts import (
    CHUNKS_ARTIFACT,
    KG_ARTIFACT,
    deserialize_chunks,
    deserialize_kg,
    serialize_chunks,
    serialize_kg,
)
from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.contextualizer import contextualise_chunks
from ay_platform_core.c7_memory.db.repository import MemoryRepository
from ay_platform_core.c7_memory.embedding.base import EmbeddingProvider
from ay_platform_core.c7_memory.ingestion.chunker import chunk_text
from ay_platform_core.c7_memory.ingestion.parser import (
    ParseFailureError,
    UnsupportedMimeError,
    parse,
)
from ay_platform_core.c7_memory.kg.code_extractor import extract_structural_python
from ay_platform_core.c7_memory.kg.extractor import (
    KGExtractionError,
    extract_entities_and_relations,
)
from ay_platform_core.c7_memory.kg.ontology import (
    StructuralExtraction,
    StructuralKGResult,
)
from ay_platform_core.c7_memory.kg.repository import KGRepository
from ay_platform_core.c7_memory.kg.structural_extractor import extract_structural
from ay_platform_core.c7_memory.models import (
    ChunkIngestRequest,
    ChunkPublic,
    ChunkStatus,
    EntityEmbedRequest,
    IndexKind,
    KGExtractionResult,
    KGRelationSample,
    KGSummary,
    ParseStatus,
    Provenance,
    QuotaStatus,
    RetrievalHit,
    RetrievalRequest,
    RetrievalResponse,
    SourceIngestRequest,
    SourceListResponse,
    SourcePublic,
)
from ay_platform_core.c7_memory.retrieval.fusion import reciprocal_rank_fusion
from ay_platform_core.c7_memory.retrieval.similarity import cosine, snippet
from ay_platform_core.c7_memory.storage.minio_storage import MemorySourceStorage
from ay_platform_core.c8_llm.client import LLMGatewayClient


class MemoryService:
    """Public API of the Memory Service."""

    def __init__(
        self,
        config: MemoryConfig,
        repo: MemoryRepository,
        embedder: EmbeddingProvider,
        storage: MemorySourceStorage | None = None,
        kg_repo: KGRepository | None = None,
        llm_client: LLMGatewayClient | None = None,
    ) -> None:
        self._config = config
        self._repo = repo
        self._embedder = embedder
        # `storage` is optional: tests that don't exercise the upload
        # endpoint can pass None. The /sources/upload route requires
        # storage to be present and 503's otherwise.
        self._storage = storage
        # Phase F.1 — KG extraction. Both `kg_repo` and `llm_client`
        # are required for the extract endpoint; absent → 503.
        self._kg_repo = kg_repo
        self._llm = llm_client

    # ------------------------------------------------------------------
    # Ingestion (admin/test direct path — C12 upload still goes via NATS
    # in production; this lets operators and integration tests ingest
    # pre-parsed content without spinning up the full pipeline)
    # ------------------------------------------------------------------

    async def ingest_source(
        self, payload: SourceIngestRequest, *, tenant_id: str
    ) -> SourcePublic:
        """Ingest a source whose CONTENT is already a UTF-8 string.

        Used by C12 webhooks and tests. The string is round-tripped
        through the parser registry to apply MIME-specific text shaping
        (e.g. markdown frontmatter strip). For binary uploads (PDF /
        DOCX), the platform path is the n8n `extract_and_ingest`
        workflow → C13 → `ingest_chunks_from_extractor` (R-400-223 v2 —
        D-020 session 7 removed the legacy `ingest_uploaded_source`).
        """
        await self._enforce_quota(tenant_id, payload.project_id, payload.size_bytes)

        try:
            text = parse(payload.mime_type, payload.content.encode("utf-8"))
        except UnsupportedMimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)
            ) from exc
        except ParseFailureError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc

        return await self._index_parsed_source(
            tenant_id=tenant_id,
            project_id=payload.project_id,
            source_id=payload.source_id,
            mime_type=payload.mime_type,
            uploaded_by=payload.uploaded_by,
            size_bytes=payload.size_bytes,
            parsed_text=text,
        )

    # D-020 session 7 — `ingest_uploaded_source` (Phase B legacy upload path)
    # physically removed. The C12 → C13 → C7 chain now owns parsing +
    # chunking + embedding ; C7 only accepts pre-chunked rich payloads via
    # `ingest_chunks_from_extractor` (R-400-223 v2). See
    # `infra/c13_extractor/docs/deployment.md` for the new ingestion flow.

    async def ingest_conversation_turn(
        self,
        *,
        tenant_id: str,
        project_id: str,
        conversation_id: str,
        turn_id: str,
        user_message: str,
        assistant_reply: str,
        actor_id: str,
        **_forward_auth_kwargs: Any,
    ) -> SourcePublic:
        # `**_forward_auth_kwargs` mirrors `retrieve()` — keeps the call
        # signature compatible with `RemoteMemoryService` so callers
        # (C3 _rag_stream) don't need to branch on which variant is
        # wired.
        """Phase E of v1 plan — index a conversation turn into the
        CONVERSATIONS index so follow-up questions can retrieve prior
        exchanges as context.

        The user/assistant pair is concatenated into a single text body
        (one chunk per ~chunk_token_size words) so retrieve sees the
        full exchange as a single semantic unit. Source row is tagged
        `mime_type=text/plain`, `uploaded_by=conv:{actor_id}` for
        operator audit; quota is enforced same as upload.
        """
        body = (
            f"User: {user_message.strip()}\n\n"
            f"Assistant: {assistant_reply.strip()}"
        )
        size_bytes = len(body.encode("utf-8"))
        await self._enforce_quota(tenant_id, project_id, size_bytes)
        return await self._index_parsed_source(
            tenant_id=tenant_id,
            project_id=project_id,
            source_id=f"conv:{conversation_id}:{turn_id}",
            mime_type="text/plain",
            uploaded_by=f"conv:{actor_id}",
            size_bytes=size_bytes,
            parsed_text=body,
            index_kind=IndexKind.CONVERSATIONS,
        )

    async def ingest_chunks_from_extractor(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_id: str,
        payload: ChunkIngestRequest,
    ) -> SourcePublic:
        """R-400-223 v2 — pure-INSERT path for chunks produced by C13.

        The request body carries:
          - the full set of ChunkRich items (R-400-222 v2),
          - the embedding model metadata stamped by C13 (D-020 v2 §B1).

        C7's responsibilities here are STRICTLY:
          1. Validate payload shape (Pydantic — already enforced by FastAPI).
          2. Cross-validate embedding metadata vs each chunk's vector.
          3. Enforce the per-project quota (R-400-024) against the chunks'
             cumulative token_count.
          4. Persist `memory_chunks` + `memory_sources` records, copying
             every ChunkRich field plus the embedding vector AS-IS (no
             re-embedding).
          5. Stamp each source row with a `processing_version` carrying
             the C13 embedding model identity so staleness detection works
             across the upgrade boundary.

        Backward-compat fallback (transitional, D-020 session 7 removes it):
          when a chunk's `embedding` field is None, C7 falls back to its
          own embedder. Operators relying on this path SHOULD migrate to
          C13-produced embeddings.
        """
        # 1. Cross-validate embedding metadata.
        chunks = payload.chunks
        chunks_with_embedding = [c for c in chunks if c.embedding is not None]
        if chunks_with_embedding:
            dims = {len(c.embedding) for c in chunks_with_embedding}  # type: ignore[arg-type]
            if dims != {payload.embedding_dimension}:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"embedding_dimension mismatch: declared "
                        f"{payload.embedding_dimension}, vectors carry {sorted(dims)}"
                    ),
                )

        # 2. Quota enforcement on cumulative token_count
        # (token_count proxy for size_bytes — chunks are post-parse, so the
        # raw blob was already accounted for in the upload path).
        total_tokens = sum(c.token_count for c in chunks)
        # Quota uses bytes; conservatively assume ~4 bytes per token (UTF-8 word
        # average). Operators tune `c7_per_project_quota_bytes` per tenant.
        size_estimate = total_tokens * 4
        await self._enforce_quota(tenant_id, project_id, size_estimate)

        # 3. Persist a synthetic SourceIngestRequest for _source_row helpers.
        size_bytes = sum(len(c.text.encode("utf-8")) for c in chunks)
        synth_payload = SourceIngestRequest(
            source_id=source_id,
            project_id=project_id,
            mime_type=payload.mime_type,  # type: ignore[arg-type]
            content="placeholder-not-stored-c13-produced",
            size_bytes=max(size_bytes, 1),
            uploaded_by=payload.uploaded_by,
        )

        # 4. Resolve embeddings — use payload vectors where present, fall back
        #    to the local embedder otherwise (transitional path).
        needs_fallback = [i for i, c in enumerate(chunks) if c.embedding is None]
        fallback_vectors: list[list[float]] = []
        if needs_fallback:
            fallback_vectors = await self._embedder.embed_batch(
                [chunks[i].text for i in needs_fallback]
            )

        # 5. Build chunk rows. The processing_version uses the C13 embedding
        #    model id (not C7's) so a future C13 model upgrade marks rows
        #    stale and triggers a re-ingest.
        version = _format_processing_version(
            self._config.chunk_token_size,
            self._config.chunk_overlap,
            payload.embedding_model,
        )
        now = datetime.now(UTC).isoformat()
        chunk_rows: list[dict[str, Any]] = []
        fallback_iter = iter(fallback_vectors)
        for chunk in chunks:
            vector = chunk.embedding
            if vector is None:
                vector = next(fallback_iter)
            content_hash = (
                "sha256:" + hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
            )
            chunk_rows.append({
                "_key": f"{tenant_id}:{project_id}:{chunk.chunk_id}",
                "chunk_id": chunk.chunk_id,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "index": IndexKind.EXTERNAL_SOURCES.value,
                "source_id": source_id,
                "entity_id": None,
                "entity_version": None,
                "chunk_index": chunk.seq,
                "content": chunk.text,
                # R-400-222 v2 rich fields preserved through the row metadata.
                "context": chunk.context_summary or "",
                "content_hash": content_hash,
                "vector": vector,
                "model_id": payload.embedding_model,
                "model_dim": payload.embedding_dimension,
                "created_at": now,
                "status": ChunkStatus.ACTIVE.value,
                "metadata": {
                    "mime_type": payload.mime_type,
                    "extraction_run_id": chunk.extraction_run_id,
                    "section_path": chunk.section_path,
                    "char_start": chunk.char_start,
                    "char_end": chunk.char_end,
                    "token_count": chunk.token_count,
                    "original_text": chunk.original_text,
                    "global_summary": chunk.global_summary,
                    "references": chunk.references,
                    "images": chunk.images,
                    "tables": chunk.tables,
                },
            })
        await self._repo.upsert_chunks(chunk_rows)

        # 6. Optional MinIO artifact mirroring (R-400-207 — the chunks the
        #    INDEX holds reconstructible). C13 already wrote chunks.jsonl
        #    to MinIO; this path keeps C7's own artifact tracking in sync.
        if self._storage is not None:
            await self._storage.put_artifact(
                tenant_id=tenant_id,
                project_id=project_id,
                source_id=source_id,
                name=CHUNKS_ARTIFACT,
                data=serialize_chunks(source_id, payload.embedding_model, chunk_rows),
            )

        source_row = _source_row(
            payload=synth_payload,
            tenant_id=tenant_id,
            model_id=payload.embedding_model,
            chunk_count=len(chunks),
            parse_status=ParseStatus.INDEXED,
            processing_version=version,
        )
        await self._repo.upsert_source(source_row)
        public = _source_public(source_row, current_version=version)

        # D-020 session 7 — preserve the auto-KG hook from the legacy
        # `ingest_uploaded_source` path so freshly indexed sources land
        # with their KG already populated when the operator opts in. Same
        # best-effort contract: a failure here SHALL NOT cause the
        # ingest to fail.
        if (
            self._config.auto_extract_kg_on_upload
            and self._kg_repo is not None
            and self._llm is not None
        ):
            with contextlib.suppress(Exception):
                await self.extract_kg(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    source_id=source_id,
                )
        return public

    def _current_processing_version(self) -> str:
        """Pipeline descriptor a fresh ingestion would stamp now (R-400-208)."""
        return _format_processing_version(
            self._config.chunk_token_size,
            self._config.chunk_overlap,
            self._embedder.model_id,
        )

    async def _index_parsed_source(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_id: str,
        mime_type: str,
        uploaded_by: str,
        size_bytes: int,
        parsed_text: str,
        index_kind: IndexKind = IndexKind.EXTERNAL_SOURCES,
    ) -> SourcePublic:
        """Shared post-parse pipeline used by `ingest_source`,
        `ingest_uploaded_source`, and `ingest_conversation_turn`.
        Chunks the text, embeds the chunks, and persists rows under
        `index_kind` (default `EXTERNAL_SOURCES`; `CONVERSATIONS` for
        Phase E conversation memory)."""
        # Synthesise a SourceIngestRequest-like payload for the helper
        # builders below. We use the typed model where it'd compose
        # cleanly; otherwise inline.
        synth_payload = SourceIngestRequest(
            source_id=source_id,
            project_id=project_id,
            mime_type=mime_type,  # type: ignore[arg-type]
            content="placeholder-not-stored",
            size_bytes=size_bytes,
            uploaded_by=uploaded_by,
        )

        version = self._current_processing_version()
        chunks = chunk_text(
            parsed_text,
            token_size=self._config.chunk_token_size,
            overlap=self._config.chunk_overlap,
        )
        if not chunks:
            source_row = _source_row(
                payload=synth_payload,
                tenant_id=tenant_id,
                model_id=self._embedder.model_id,
                chunk_count=0,
                parse_status=ParseStatus.PARSED,
                processing_version=version,
            )
            await self._repo.upsert_source(source_row)
            return _source_public(source_row, current_version=version)

        # R-400-203 — cumulative chunk contextualisation. When enabled and an
        # LLM is wired, each chunk gets a short document-aware context ; the
        # EMBEDDED text is the contextualised chunk (the raw chunk is still
        # stored as `content`). Best-effort + no-op without an LLM, so the
        # dense vectors degrade gracefully to raw-chunk embeddings.
        contexts: list[str] = ["" for _ in chunks]
        if self._config.contextualisation_enabled and self._llm is not None:
            contexts = await contextualise_chunks(
                llm_client=self._llm,
                document=parsed_text,
                chunk_texts=[c.text for c in chunks],
                agent_name=self._config.contextualisation_agent,
                tenant_id=tenant_id,
                project_id=project_id,
                source_id=source_id,
            )
        embed_texts = [
            f"{ctx}\n\n{chunk.text}" if ctx else chunk.text
            for chunk, ctx in zip(chunks, contexts, strict=True)
        ]
        vectors = await self._embedder.embed_batch(embed_texts)
        if len(vectors) != len(chunks):
            raise RuntimeError(
                "embedder returned a different number of vectors than "
                "input chunks — adapter contract violation"
            )
        if any(len(v) != self._embedder.dimension for v in vectors):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "embedder produced a vector of unexpected dimension; "
                    "declared dim does not match actual output"
                ),
            )

        now = datetime.now(UTC).isoformat()
        chunk_rows: list[dict[str, Any]] = []
        for chunk, vector, ctx in zip(chunks, vectors, contexts, strict=True):
            content_hash = (
                "sha256:" + hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
            )
            chunk_id = f"{source_id}:{chunk.index}"
            chunk_rows.append({
                "_key": f"{tenant_id}:{project_id}:{chunk_id}",
                "chunk_id": chunk_id,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "index": index_kind.value,
                "source_id": source_id,
                "entity_id": None,
                "entity_version": None,
                "chunk_index": chunk.index,
                "content": chunk.text,
                # R-400-203 : the document-aware context the chunk was
                # embedded with ("" when contextualisation was off/unavailable).
                "context": ctx,
                "content_hash": content_hash,
                "vector": vector,
                "model_id": self._embedder.model_id,
                "model_dim": self._embedder.dimension,
                "created_at": now,
                "status": ChunkStatus.ACTIVE.value,
                "metadata": {"mime_type": mime_type},
            })
        await self._repo.upsert_chunks(chunk_rows)

        # R-400-207: persist the embedded chunk rows as a durable MinIO
        # artifact so the vector store can be rebuilt by replay without
        # re-embedding. External sources only (conversation memory is not
        # a rebuildable "source"), and only when blob storage is wired.
        if self._storage is not None and index_kind is IndexKind.EXTERNAL_SOURCES:
            await self._storage.put_artifact(
                tenant_id=tenant_id,
                project_id=project_id,
                source_id=source_id,
                name=CHUNKS_ARTIFACT,
                data=serialize_chunks(source_id, self._embedder.model_id, chunk_rows),
            )

        source_row = _source_row(
            payload=synth_payload,
            tenant_id=tenant_id,
            model_id=self._embedder.model_id,
            chunk_count=len(chunks),
            parse_status=ParseStatus.INDEXED,
            processing_version=version,
        )
        await self._repo.upsert_source(source_row)
        return _source_public(source_row, current_version=version)

    async def extract_kg(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_id: str,
    ) -> KGExtractionResult:
        """Phase F.1 — extract entities + relations from a previously
        ingested source via the C8 LLM gateway, then persist to the
        knowledge graph collections.

        Requires both `llm_client` and `kg_repo` to have been wired at
        construction time; absent → 503. The source must already be in
        Arango (POST /sources or POST /sources/upload before this).
        """
        if self._llm is None or self._kg_repo is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "KG extraction not configured — wire C8 LLMGatewayClient "
                    "and KGRepository to enable POST /sources/{sid}/extract-kg"
                ),
            )

        existing = await self._repo.get_source(tenant_id, project_id, source_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="source not found",
            )

        # Reconstruct the source's text from its persisted chunks. Cheap
        # for v1 sources (≤ a few MB); avoids re-parsing the raw blob.
        chunk_rows = await self._repo.scan_chunks(
            tenant_id=tenant_id,
            project_id=project_id,
            indexes=[IndexKind.EXTERNAL_SOURCES.value],
            model_id=self._embedder.model_id,
            include_deprecated=False,
            include_history=False,
            scan_cap=self._config.retrieval_scan_cap,
        )
        source_chunks = sorted(
            (c for c in chunk_rows if c.get("source_id") == source_id),
            key=lambda c: c.get("chunk_index", 0),
        )
        source_text = "\n\n".join(c["content"] for c in source_chunks).strip()

        try:
            entities, relations = await extract_entities_and_relations(
                llm_client=self._llm,
                source_text=source_text,
                tenant_id=tenant_id,
                project_id=project_id,
                source_id=source_id,
            )
        except KGExtractionError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"LLM-based KG extraction failed: {exc}",
            ) from exc

        added_entities, added_relations = await self._kg_repo.persist(
            tenant_id=tenant_id,
            project_id=project_id,
            source_id=source_id,
            entities=entities,
            relations=relations,
        )
        # R-400-207: persist the extracted triples (with provenance per
        # R-400-201) as a durable MinIO artifact so the graph store can be
        # rebuilt by replay WITHOUT re-invoking the LLM. The triples are
        # already in Arango; this snapshot is the replay source of truth.
        if self._storage is not None:
            await self._storage.put_artifact(
                tenant_id=tenant_id,
                project_id=project_id,
                source_id=source_id,
                name=KG_ARTIFACT,
                data=serialize_kg(source_id, entities, relations),
            )

        return KGExtractionResult(
            source_id=source_id,
            entities_added=added_entities,
            relations_added=added_relations,
            entities=entities,
            relations=relations,
        )

    async def extract_structural_kg(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_id: str,
        kind: str = "requirements",
    ) -> StructuralKGResult:
        """V2 #3-A.a (R-400-200) — deterministic schema-guided L1 extraction
        over an already-ingested source. No LLM, closed ontology (E-400-006),
        persisted to the KG collections tagged `L1` / `EXTRACTED` / 1.0.

        `kind="requirements"` (default) parses spec entity blocks (`id:` +
        `derives-from:`) from the chunk-reconstructed text. `kind="code"`
        parses the Python AST (MODULE/CLASS/FUNCTION/METHOD + IMPORTS /
        INHERITS_FROM + `@relation` edges) — it reads the ORIGINAL raw bytes
        (Python indentation is significant, so the whitespace-collapsed chunk
        text won't do).

        Requires `kg_repo` (503 if absent) ; `kind="code"` also requires blob
        storage (503) + stored raw bytes (409). Re-running is free +
        reproducible, so no replay artifact is stored (R-400-207's rationale
        applies only to LLM extraction)."""
        if self._kg_repo is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "structural KG extraction not configured — wire "
                    "KGRepository to enable "
                    "POST /sources/{sid}/extract-structural"
                ),
            )

        existing = await self._repo.get_source(tenant_id, project_id, source_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="source not found",
            )

        if kind == "code":
            extraction = await self._extract_code_structural(
                tenant_id=tenant_id,
                project_id=project_id,
                source_id=source_id,
                mime_type=str(existing["mime_type"]),
            )
        else:
            chunk_rows = await self._repo.scan_chunks(
                tenant_id=tenant_id,
                project_id=project_id,
                indexes=[IndexKind.EXTERNAL_SOURCES.value],
                model_id=self._embedder.model_id,
                include_deprecated=False,
                include_history=False,
                scan_cap=self._config.retrieval_scan_cap,
            )
            source_chunks = sorted(
                (c for c in chunk_rows if c.get("source_id") == source_id),
                key=lambda c: c.get("chunk_index", 0),
            )
            source_text = "\n\n".join(c["content"] for c in source_chunks).strip()
            extraction = extract_structural(source_text)

        added_entities, added_relations = await self._kg_repo.persist_structural(
            tenant_id=tenant_id,
            project_id=project_id,
            source_id=source_id,
            extraction=extraction,
        )
        return StructuralKGResult(
            source_id=source_id,
            entities_added=added_entities,
            relations_added=added_relations,
            entities=extraction.entities,
            relations=extraction.relations,
        )

    async def _extract_code_structural(
        self, *, tenant_id: str, project_id: str, source_id: str, mime_type: str
    ) -> StructuralExtraction:
        """Read the ORIGINAL raw bytes of a code source (Python indentation is
        significant) and run the tree-sitter AST extractor. 503 if storage
        absent, 409 if no raw bytes (string-ingested), 415/422 on parse."""
        if self._storage is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="blob storage not configured — code extraction requires MinIO",
            )
        try:
            content_bytes = await self._storage.get_source_blob(
                tenant_id=tenant_id,
                project_id=project_id,
                source_id=source_id,
                mime_type=mime_type,
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "source has no stored raw bytes "
                    "(string-ingested sources cannot be code-extracted)"
                ),
            ) from exc
        try:
            text = parse(mime_type, content_bytes)
        except UnsupportedMimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)
            ) from exc
        except ParseFailureError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc
        return extract_structural_python(text, module_name=source_id)

    async def rebuild_from_artifacts(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_id: str,
    ) -> dict[str, int]:
        """Replay a source's persisted MinIO artifacts into ArangoDB
        WITHOUT re-embedding or re-invoking the LLM (R-400-207).

        `chunks.json` -> vector store (required; 404 if absent).
        `kg.json` -> graph store (optional; skipped when the source had
        no KG extracted). The databases are projections of the artifact
        layer, so this rebuild is pure, deterministic, and free.

        Note (v1 scope): restores the two stores (chunks + KG), which is
        the literal R-400-207 guarantee. Reconstructing the
        `memory_sources` listing row from artifacts is a follow-up;
        retrieval scans `memory_chunks` directly, so it works without it.
        """
        if self._storage is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="blob storage not configured — rebuild requires MinIO",
            )

        try:
            raw_chunks = await self._storage.get_artifact(
                tenant_id=tenant_id,
                project_id=project_id,
                source_id=source_id,
                name=CHUNKS_ARTIFACT,
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"no chunks artifact for source {source_id} — nothing "
                    "to replay (was it ingested with blob storage wired?)"
                ),
            ) from exc

        chunks_artifact = deserialize_chunks(raw_chunks)
        await self._repo.upsert_chunks(chunks_artifact.chunks)

        entities_restored = 0
        relations_restored = 0
        if self._kg_repo is not None:
            try:
                raw_kg = await self._storage.get_artifact(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    source_id=source_id,
                    name=KG_ARTIFACT,
                )
            except FileNotFoundError:
                raw_kg = None
            if raw_kg is not None:
                kg_artifact = deserialize_kg(raw_kg)
                entities_restored, relations_restored = await self._kg_repo.persist(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    source_id=source_id,
                    entities=kg_artifact.entities,
                    relations=kg_artifact.relations,
                )

        return {
            "chunks": len(chunks_artifact.chunks),
            "entities": entities_restored,
            "relations": relations_restored,
        }

    async def download_source(
        self, tenant_id: str, project_id: str, source_id: str,
    ) -> tuple[bytes, str, str]:
        """Fetch the raw bytes of a previously-uploaded source from
        MinIO. Returns `(bytes, mime_type, filename)` so the router
        can set Content-Type + Content-Disposition correctly.

        Errors:
          - 503 if MinIO storage isn't wired (e.g. test stack without
            blob storage).
          - 404 if the source row doesn't exist (wrong tenant/project,
            or source deleted).
          - 404 if the row exists but the MinIO object is missing
            (sources ingested via the JSON-only `POST /sources` path
            never wrote a blob; download is meaningless for those).
        """
        if self._storage is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "blob storage not configured — download requires "
                    "MinIO storage to be wired"
                ),
            )
        existing = await self._repo.get_source(tenant_id, project_id, source_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="source not found",
            )
        mime_type = str(existing["mime_type"])
        try:
            blob = await self._storage.get_source_blob(
                tenant_id=tenant_id,
                project_id=project_id,
                source_id=source_id,
                mime_type=mime_type,
            )
        except FileNotFoundError as exc:
            # Row present, blob absent — the source was ingested via
            # the JSON `POST /sources` endpoint which doesn't persist
            # to MinIO. Surface 404 rather than 500.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="source has no downloadable blob "
                "(ingested without upload)",
            ) from exc
        import mimetypes as _mt  # noqa: PLC0415 — keep module hot path lean
        ext = _mt.guess_extension(mime_type) or ""
        filename = f"{source_id}{ext}"
        return blob, mime_type, filename

    async def delete_source(
        self, tenant_id: str, project_id: str, source_id: str
    ) -> None:
        existing = await self._repo.get_source(tenant_id, project_id, source_id)
        if existing is None:
            # R-400-071: 404, not 403 — do not leak tenant boundaries.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="source not found"
            )
        await self._repo.delete_chunks_for_source(tenant_id, project_id, source_id)
        await self._repo.delete_source(tenant_id, project_id, source_id)

    async def list_sources(
        self, tenant_id: str, project_id: str
    ) -> SourceListResponse:
        rows = await self._repo.list_sources(tenant_id, project_id)
        current = self._current_processing_version()
        return SourceListResponse(
            sources=[_source_public(r, current_version=current) for r in rows]
        )

    async def get_source(
        self, tenant_id: str, project_id: str, source_id: str
    ) -> SourcePublic:
        row = await self._repo.get_source(tenant_id, project_id, source_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="source not found"
            )
        return _source_public(row, current_version=self._current_processing_version())

    # D-020 session 7 — `reprocess_source` (R-400-208) physically removed.
    # In the new world a reprocess means "re-run the n8n
    # `extract_and_ingest` workflow against the same source_id" — owned by
    # C12, not C7. C7 still stamps `processing_version` on every ingest
    # (R-400-208) so staleness detection at GET /sources/{id} keeps
    # working ; only the re-run trigger moved out of C7.

    async def kg_summary(
        self, tenant_id: str, project_id: str, *, sample_limit: int = 10
    ) -> KGSummary:
        """Project-level knowledge-graph inspection view (the simple graph
        bootstrap): entity/relation counts + a small sample of triples with
        provenance. Requires the KG repo wired; 503 otherwise."""
        if self._kg_repo is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="knowledge graph not configured — wire KGRepository",
            )
        raw = await self._kg_repo.summary(
            tenant_id, project_id, sample_limit=sample_limit,
        )
        sample = [
            KGRelationSample(
                subject=r.get("subject") or "",
                relation=r.get("relation") or "",
                object=r.get("object") or "",
                provenance=(
                    Provenance(r["provenance"])
                    if r.get("provenance")
                    else Provenance.INFERRED
                ),
                confidence=(
                    r["confidence"] if r.get("confidence") is not None else 1.0
                ),
            )
            for r in raw["sample"]
        ]
        return KGSummary(
            project_id=project_id,
            entity_count=raw["entity_count"],
            relation_count=raw["relation_count"],
            sample=sample,
        )

    # ------------------------------------------------------------------
    # Entity embedding (R-400-030) — triggered by requirements events.
    # Exposed as a method so C5 event consumers (or tests) can call it
    # directly; in production an async worker would dequeue from NATS
    # and invoke this path.
    # ------------------------------------------------------------------

    async def embed_entity(
        self, payload: EntityEmbedRequest, *, tenant_id: str
    ) -> ChunkPublic:
        vector = await self._embedder.embed_one(payload.content)
        if len(vector) != self._embedder.dimension:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="embedder produced vector of unexpected dimension",
            )

        if payload.preserve_history:
            await self._repo.mark_entity_superseded(
                tenant_id, payload.project_id, payload.entity_id, payload.entity_version
            )

        now = datetime.now(UTC).isoformat()
        content_hash = "sha256:" + hashlib.sha256(payload.content.encode("utf-8")).hexdigest()
        chunk_id = f"{payload.entity_id}@v{payload.entity_version}"
        row: dict[str, Any] = {
            "_key": f"{tenant_id}:{payload.project_id}:{chunk_id}",
            "chunk_id": chunk_id,
            "tenant_id": tenant_id,
            "project_id": payload.project_id,
            "index": IndexKind.REQUIREMENTS.value,
            "source_id": None,
            "entity_id": payload.entity_id,
            "entity_version": payload.entity_version,
            "chunk_index": 0,
            "content": payload.content,
            "content_hash": content_hash,
            "vector": vector,
            "model_id": self._embedder.model_id,
            "model_dim": self._embedder.dimension,
            "created_at": now,
            "status": ChunkStatus.ACTIVE.value,
            "metadata": dict(payload.metadata),
        }
        await self._repo.upsert_chunk(row)
        return _chunk_public(row)

    # ------------------------------------------------------------------
    # Retrieval (R-400-040)
    # ------------------------------------------------------------------

    async def retrieve(
        self,
        payload: RetrievalRequest,
        *,
        tenant_id: str,
        **_forward_auth_kwargs: Any,
    ) -> RetrievalResponse:
        # `**_forward_auth_kwargs` swallows `user_id` / `user_roles`
        # passed by callers that share their signature with
        # `RemoteMemoryService.retrieve` — the in-process variant
        # already trusts its `tenant_id` arg, so the headers are
        # informational here. Keeping the kwargs ensures the two
        # implementations are call-compatible.
        started = time.monotonic()
        # R-400-042: the query is embedded with the ACTIVE embedder; we
        # only compare against stored chunks that used the same model.
        query_vector = await self._embedder.embed_one(payload.query)

        rows = await self._repo.scan_chunks(
            tenant_id=tenant_id,
            project_id=payload.project_id,
            indexes=[ix.value for ix in payload.indexes],
            model_id=self._embedder.model_id,
            include_deprecated=payload.include_deprecated,
            include_history=payload.include_history,
            scan_cap=self._config.retrieval_scan_cap,
        )
        # Apply post-scan filters (metadata + history) — kept Python-side
        # so the AQL scan stays simple and the repository remains reusable.
        filtered = [
            r for r in rows if _row_matches_filters(r, payload)
        ]

        weights = payload.weights or {}

        def _cosine_weighted(row: dict[str, Any]) -> float:
            raw = cosine(query_vector, list(row["vector"]))
            multiplier = weights.get(IndexKind(row["index"]), 1.0)
            return raw * multiplier

        scored: list[tuple[dict[str, Any], float]] = [
            (row, _cosine_weighted(row)) for row in filtered
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)

        # ----------------------------------------------------------------
        # Phase F.2 — KG expansion (hybrid retrieval).
        # Active iff a KG repo is wired AND the initial top-K seeds have
        # source_ids that the graph knows about. Two effects combined:
        #   (A) pool widening — chunks of graph-neighbour source_ids that
        #       the `scan_cap` cut off are fetched directly and added to
        #       the candidate pool.
        #   (B) ranking boost — chunks whose source_id is reachable in
        #       the graph from a seed source_id get their score
        #       multiplied by `kg_expansion_boost` (default 1.3). Pure-
        #       vector ranking still wins for clearly more relevant
        #       direct matches; graph signal only nudges borderline.
        # ----------------------------------------------------------------
        if self._kg_repo is not None and scored:
            scored = await self._apply_kg_expansion(
                scored=scored,
                payload=payload,
                tenant_id=tenant_id,
                cosine_fn=_cosine_weighted,
            )

        # R-400-202 — hybrid retrieval : fuse the dense (cosine + KG) ranking
        # with the BM25 lexical arm by reciprocal rank fusion. The lexical arm
        # recovers exact-token matches (IDs, names) the dense arm misses. If
        # it returns nothing (no text_en tokens / view not yet indexed) the
        # dense ranking stands. `dense` mode keeps the pure-cosine v1 path.
        if self._config.retrieval_mode == "hybrid":
            lexical_rows = await self._repo.lexical_search(
                tenant_id=tenant_id,
                project_id=payload.project_id,
                query=payload.query,
                indexes=[ix.value for ix in payload.indexes],
                model_id=self._embedder.model_id,
                include_deprecated=payload.include_deprecated,
                include_history=payload.include_history,
                limit=max(payload.top_k * 5, 50),
            )
            # Apply the same metadata/history filters as the dense arm so the
            # two arms agree on what is eligible before fusion.
            lexical_rows = [r for r in lexical_rows if _row_matches_filters(r, payload)]
            # Guard against stale ArangoSearch entries : the view commits /
            # consolidates asynchronously, so just after a delete it can still
            # return removed chunks. When the dense scan was NOT truncated it
            # is the complete live set → a lexical hit absent from it is stale,
            # so drop it. When truncated, keep lexical-only hits (they may be
            # live chunks beyond scan_cap — the recall win of the lexical arm).
            if len(rows) < self._config.retrieval_scan_cap:
                dense_ids = {row["chunk_id"] for row, _ in scored}
                lexical_rows = [r for r in lexical_rows if r["chunk_id"] in dense_ids]
            if lexical_rows:
                rows_by_id: dict[str, dict[str, Any]] = {
                    row["chunk_id"]: row for row, _ in scored
                }
                for row in lexical_rows:
                    rows_by_id.setdefault(row["chunk_id"], row)
                fused = reciprocal_rank_fusion(
                    [
                        [row["chunk_id"] for row, _ in scored],
                        [row["chunk_id"] for row in lexical_rows],
                    ],
                    k=self._config.rrf_k,
                )
                scored = sorted(
                    ((rows_by_id[cid], score) for cid, score in fused.items()),
                    key=lambda pair: pair[1],
                    reverse=True,
                )

        top = scored[: payload.top_k]

        hits = [
            RetrievalHit(
                chunk_id=row["chunk_id"],
                index=IndexKind(row["index"]),
                score=score,
                content=row["content"],
                snippet=snippet(row["content"]),
                source_id=row.get("source_id"),
                entity_id=row.get("entity_id"),
                entity_version=row.get("entity_version"),
                metadata=dict(row.get("metadata", {})),
            )
            for row, score in top
        ]
        return RetrievalResponse(
            retrieval_id=str(uuid.uuid4()),
            request=payload,
            hits=hits,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    async def _apply_kg_expansion(
        self,
        *,
        scored: list[tuple[dict[str, Any], float]],
        payload: RetrievalRequest,
        tenant_id: str,
        cosine_fn: Any,
    ) -> list[tuple[dict[str, Any], float]]:
        """Phase F.2 hybrid expansion. Returns a re-sorted scored list
        with (A) extra chunks pulled from graph-neighbour source_ids
        and (B) boosted scores for chunks whose source_id is graph-
        related to a top-K seed."""
        assert self._kg_repo is not None  # invariant — caller checked
        top_seeds = scored[: payload.top_k]
        seed_source_ids = sorted({
            sid for row, _ in top_seeds
            if (sid := row.get("source_id")) is not None
        })
        if not seed_source_ids:
            return scored

        neighbour_source_ids = await self._kg_repo.find_neighbor_source_ids(
            tenant_id=tenant_id,
            project_id=payload.project_id,
            seed_source_ids=seed_source_ids,
            depth=self._config.kg_expansion_depth,
        )
        if not neighbour_source_ids:
            return scored

        # Cap the new source_ids we'll bring in (proposition A) — bound
        # the cost of the extra fetch + scoring round.
        capped_neighbours = sorted(set(neighbour_source_ids))[
            : self._config.kg_expansion_neighbour_cap
        ]
        already_seen = {
            sid for row, _ in scored
            if (sid := row.get("source_id")) is not None
        }
        extra_source_ids = [s for s in capped_neighbours if s not in already_seen]
        if extra_source_ids:
            extra_rows = await self._repo.fetch_chunks_for_source_ids(
                tenant_id=tenant_id,
                project_id=payload.project_id,
                source_ids=extra_source_ids,
                indexes=[ix.value for ix in payload.indexes],
                model_id=self._embedder.model_id,
                include_deprecated=payload.include_deprecated,
                include_history=payload.include_history,
            )
            extra_filtered = [r for r in extra_rows if _row_matches_filters(r, payload)]
            scored = scored + [(row, cosine_fn(row)) for row in extra_filtered]

        # Proposition B: boost any chunk whose source_id is in the graph-
        # neighbour set (including the just-added extras). Seeds
        # themselves are NOT boosted (they're already at the top by
        # vector similarity; double-counting would hide cosine signal).
        neighbour_set = set(capped_neighbours)
        boost = self._config.kg_expansion_boost
        if boost != 1.0:
            scored = [
                (row, score * boost
                 if row.get("source_id") in neighbour_set else score)
                for row, score in scored
            ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored

    # ------------------------------------------------------------------
    # Quota (R-400-024)
    # ------------------------------------------------------------------

    async def quota(self, tenant_id: str, project_id: str) -> QuotaStatus:
        totals = await self._repo.quota_totals(tenant_id, project_id)
        return QuotaStatus(
            project_id=project_id,
            bytes_used=totals["bytes_used"],
            bytes_limit=self._config.default_quota_bytes,
            chunk_count=totals["chunk_count"],
            source_count=totals["source_count"],
        )

    async def _enforce_quota(
        self, tenant_id: str, project_id: str, incoming_bytes: int
    ) -> None:
        totals = await self._repo.quota_totals(tenant_id, project_id)
        if totals["bytes_used"] + incoming_bytes > self._config.default_quota_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=(
                    f"project quota exceeded: {totals['bytes_used']} + "
                    f"{incoming_bytes} > {self._config.default_quota_bytes}"
                ),
            )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _format_processing_version(
    chunk_token_size: int, chunk_overlap: int, model_id: str | None
) -> str:
    """Deterministic descriptor of the chunk + embed pipeline (R-400-208).

    A source is 'stale' when its stored descriptor differs from the one a
    fresh ingestion would produce — i.e. the chunk window/overlap or the
    embedding model changed since it was last processed.
    """
    return f"chunk={chunk_token_size}/{chunk_overlap};embed={model_id or 'unknown'}"


def _source_row(
    *,
    payload: SourceIngestRequest,
    tenant_id: str,
    model_id: str,
    chunk_count: int,
    parse_status: ParseStatus,
    processing_version: str,
) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "_key": f"{tenant_id}:{payload.project_id}:{payload.source_id}",
        "tenant_id": tenant_id,
        "project_id": payload.project_id,
        "source_id": payload.source_id,
        "minio_raw_path": None,
        "minio_parsed_path": None,
        "minio_chunks_path": None,
        "mime_type": payload.mime_type,
        "size_bytes": payload.size_bytes,
        "uploaded_by": payload.uploaded_by,
        "uploaded_at": now,
        "parse_status": parse_status.value,
        "parse_error": None,
        "chunk_count": chunk_count,
        "model_id": model_id,
        "processing_version": processing_version,
    }


def _source_public(
    row: dict[str, Any], *, current_version: str | None = None
) -> SourcePublic:
    stored_version = row.get("processing_version")
    is_stale = current_version is not None and stored_version != current_version
    return SourcePublic(
        source_id=row["source_id"],
        project_id=row["project_id"],
        mime_type=row["mime_type"],
        size_bytes=row["size_bytes"],
        uploaded_by=row["uploaded_by"],
        uploaded_at=datetime.fromisoformat(row["uploaded_at"]),
        parse_status=ParseStatus(row["parse_status"]),
        parse_error=row.get("parse_error"),
        chunk_count=row.get("chunk_count", 0),
        model_id=row.get("model_id"),
        processing_version=stored_version,
        is_stale=is_stale,
    )


def _chunk_public(row: dict[str, Any]) -> ChunkPublic:
    return ChunkPublic(
        chunk_id=row["chunk_id"],
        project_id=row["project_id"],
        index=IndexKind(row["index"]),
        source_id=row.get("source_id"),
        entity_id=row.get("entity_id"),
        entity_version=row.get("entity_version"),
        chunk_index=row["chunk_index"],
        content=row["content"],
        content_hash=row["content_hash"],
        model_id=row["model_id"],
        model_dim=row["model_dim"],
        created_at=datetime.fromisoformat(row["created_at"]),
        status=ChunkStatus(row["status"]),
        metadata=dict(row.get("metadata", {})),
    )


def _row_matches_filters(row: dict[str, Any], payload: RetrievalRequest) -> bool:
    """Post-scan filtering: metadata + history gating.

    - `include_history=False` (default): for each entity_id, only the
      latest active version. Deprecated chunks are governed by
      `include_deprecated`.
    - `filters`: simple key/value match against row['metadata'] and
      top-level fields.
    """
    # History: when False, drop superseded requirement-entity chunks.
    if (
        not payload.include_history
        and row.get("entity_id") is not None
        and row.get("status") == ChunkStatus.SUPERSEDED.value
    ):
        return False
    # Metadata filter: every declared key SHALL match.
    for key, expected in payload.filters.items():
        actual = row.get(key)
        if actual is None:
            actual = row.get("metadata", {}).get(key)
        if actual != expected:
            return False
    return True


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


def get_service(request: Request) -> MemoryService:
    svc = getattr(request.app.state, "memory_service", None)
    if svc is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="memory service not initialised",
        )
    return svc  # type: ignore[no-any-return]
