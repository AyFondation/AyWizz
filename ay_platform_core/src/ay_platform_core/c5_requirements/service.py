# =============================================================================
# File: service.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/service.py
# Description: Facade for the C5 Requirements Service. Orchestrates MinIO
#              (source of truth) + ArangoDB (derived index) + NATS publisher
#              following the write-through pattern defined by R-300-060.
#              v2 (v1.5 upgrade): reindex operation, reconciliation tick,
#              Markdown export streaming. Scope-reduced vs 300-SPEC:
#              point-in-time export and ReqIF format remain deferred; the
#              reconciliation scheduler is also still manual (no cron).
#
# @relation implements:R-300-020
# @relation implements:R-300-022
# @relation implements:R-300-030
# @relation implements:R-300-033
# @relation implements:R-300-034
# @relation implements:R-300-050
# @relation implements:R-300-060
# @relation implements:R-300-063
# @relation implements:R-300-070
# @relation implements:R-300-072
# @relation implements:R-300-084
# @relation implements:R-300-086
# @relation implements:R-300-110
# =============================================================================

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import yaml
from fastapi import HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from ay_platform_core.c5_requirements.adapter.markdown import (
    AdapterError,
    ParsedEntity,
    parse_document,
    parse_entities,
    serialise_document,
)
from ay_platform_core.c5_requirements.adapter.validator import (
    ValidationIssue,
    check_deprecated_reason,
    check_tailoring,
    rationale_excerpt,
)
from ay_platform_core.c5_requirements.db.repository import (
    COLL_ENTITIES,
    RequirementsRepository,
)
from ay_platform_core.c5_requirements.events.base import EventPublisher
from ay_platform_core.c5_requirements.models import (
    DocumentCreate,
    DocumentFrontmatter,
    DocumentPublic,
    DocumentReplace,
    DocumentStatus,
    EntityFrontmatter,
    EntityPublic,
    EntityType,
    EntityUpdate,
    HistoryEntry,
    ImportConflictMode,
    ImportDocument,
    ImportReport,
    ImportRequest,
    ImportSummary,
    ReindexJob,
    ReindexJobStatus,
    RelationEdge,
    RelationType,
    RequirementStatus,
    TailoringReport,
)
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage

_PLATFORM_PROJECT_ID = "platform"


class ReconcileReport(BaseModel):
    """Outcome of a reconciliation tick — surfaced to ops dashboards."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    missing_in_index: int
    stale_in_index: int
    orphaned_in_index: int
    repaired: int


class ConsistencyError(RuntimeError):
    """Raised when the MinIO write succeeds but the Arango update fails
    (R-300-061). The caller SHALL enqueue reconciliation and return 500."""


class RequirementsService:
    """Public API of the Requirements Service.

    Intentionally minimal: business rules stay here, wire-level concerns
    (headers, HTTP status codes) stay in router.py.
    """

    def __init__(
        self,
        repo: RequirementsRepository,
        storage: RequirementsStorage,
        publisher: EventPublisher,
    ) -> None:
        self._repo = repo
        self._storage = storage
        self._publisher = publisher
        # Background reindex tasks — kept alive by strong reference so the
        # event loop does not garbage-collect them before completion.
        self._background_tasks: set[asyncio.Task[None]] = set()

    async def aclose(self) -> None:
        """Await any in-flight reindex tasks. Useful in tests to avoid
        unraisable-exception warnings on event-loop teardown."""
        import contextlib  # noqa: PLC0415 — local to the teardown path

        for task in list(self._background_tasks):
            # Reindex failures are already recorded on the job row.
            with contextlib.suppress(Exception):
                await task
        self._background_tasks.clear()

    # ------------------------------------------------------------------
    # Document operations
    # ------------------------------------------------------------------

    async def list_documents(
        self,
        project_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[DocumentPublic], str | None]:
        docs_raw = await self._repo.list_documents(
            project_id, limit=limit + 1, cursor_key=cursor
        )
        next_cursor: str | None = None
        if len(docs_raw) > limit:
            next_cursor = docs_raw[limit - 1]["_key"]
            docs_raw = docs_raw[:limit]
        documents = [_document_from_doc(d) for d in docs_raw]
        return documents, next_cursor

    async def get_document(self, project_id: str, slug: str) -> DocumentPublic:
        doc = await self._repo.get_document(project_id, slug)
        if doc is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
        try:
            body = await self._storage.get_document(
                RequirementsStorage.document_path(project_id, slug)
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"document metadata present but source missing: {slug}",
            ) from exc
        public = _document_from_doc(doc)
        return public.model_copy(update={"body": body.decode("utf-8")})

    async def create_document(
        self, project_id: str, actor_id: str, payload: DocumentCreate
    ) -> DocumentPublic:
        existing = await self._repo.get_document(project_id, payload.slug)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"document {payload.slug} already exists in project {project_id}",
            )
        # Render a minimal valid Markdown+YAML skeleton from the request.
        fm = DocumentFrontmatter.model_validate(
            {
                "document": payload.slug,
                "version": 1,
                "path": _document_relative_path(project_id, payload.slug),
                "language": payload.language,
                "status": payload.status,
                "derives-from": payload.derives_from,
            }
        )
        # If entities are provided, append their frontmatter blocks to the body.
        body_parts = [payload.body.rstrip(), ""] if payload.body else [""]
        for entity in payload.entities:
            body_parts.append(f"#### {entity.entity_id}\n")
            body_parts.append("```yaml")
            body_parts.append(
                _dump_entity_frontmatter(entity, initial_version=1, actor_id=actor_id)
            )
            body_parts.append("```\n")
            body_parts.append(entity.body.rstrip())
            body_parts.append("")
        rendered = serialise_document(fm, "\n".join(body_parts))
        return await self._persist_document(
            project_id=project_id,
            slug=payload.slug,
            raw_content=rendered,
            actor_id=actor_id,
            if_match=None,
        )

    async def replace_document(
        self,
        project_id: str,
        slug: str,
        actor_id: str,
        payload: DocumentReplace,
        if_match: str | None,
    ) -> DocumentPublic:
        return await self._persist_document(
            project_id=project_id,
            slug=slug,
            raw_content=payload.content,
            actor_id=actor_id,
            if_match=if_match,
        )

    async def delete_document(self, project_id: str, slug: str, actor_id: str) -> None:
        doc = await self._repo.get_document(project_id, slug)
        if doc is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")

        # Cascade: every entity in the document transitions to `deprecated`
        # with the prescribed reason (R-300-034).
        now = datetime.now(UTC).isoformat()
        entities = await self._repo.list_entities(project_id, limit=1000)
        for entity in entities:
            if entity.get("document_slug") != slug:
                continue
            entity["status"] = RequirementStatus.DEPRECATED.value
            entity["deprecated_reason"] = "Document deleted"
            entity["updated_at"] = now
            entity["updated_by"] = actor_id
            await self._repo.upsert_entity(entity)

        # Move the source to `_deleted/`, then drop the metadata record. The
        # source is preserved; only the listing removes the document.
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        src_path = RequirementsStorage.document_path(project_id, slug)
        try:
            body = await self._storage.get_document(src_path)
        except FileNotFoundError:
            body = b""
        if body:
            await self._storage.put_document(
                RequirementsStorage.deleted_path(project_id, slug, timestamp), body
            )
            await self._storage.delete_document(src_path)
        await self._repo.delete_document(project_id, slug)
        await self._publish(
            project_id, f"requirements.{project_id}.document.deleted",
            {"slug": slug, "actor": actor_id},
        )

    # ------------------------------------------------------------------
    # Bulk import (R-300-080..083)
    # ------------------------------------------------------------------

    async def import_corpus(
        self,
        project_id: str,
        payload: ImportRequest,
        actor_id: str,
    ) -> ImportReport:
        """Bulk import a set of `.md` documents into the project.

        Implements R-300-080..083 for the `md` format. `reqif` is a
        separate v2 work item (endpoint returns 501 with `format=reqif`).

        Atomicity: validation happens up-front over the whole batch. If
        any document fails validation or triggers a conflict in `fail`
        mode, NO writes are performed. Once the pre-checks pass, writes
        are issued sequentially; a partial-write failure surfaces as a
        500 with the already-committed slugs listed for operator triage.
        """
        # --- Phase 1: parse + validate every document in isolation ---
        parsed_docs: list[tuple[ImportDocument, Any, list[Any]]] = []
        all_issues: list[str] = []
        for doc in payload.documents:
            try:
                fm, body = parse_document(doc.content)
                parsed_entities = parse_entities(body)
            except AdapterError as exc:
                all_issues.append(f"{doc.slug}: parse failed — {exc}")
                continue
            if fm.document != doc.slug:
                all_issues.append(
                    f"{doc.slug}: frontmatter document={fm.document!r} does not match payload slug"
                )
                continue
            parsed_docs.append((doc, fm, parsed_entities))

        if all_issues:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"validation_errors": all_issues},
            )

        # --- Phase 2: conflict check against existing corpus ---
        conflicts: list[str] = []
        for doc, _, _ in parsed_docs:
            existing = await self._repo.get_document(project_id, doc.slug)
            if existing is not None and payload.on_conflict == ImportConflictMode.FAIL:
                conflicts.append(doc.slug)
        if conflicts and payload.on_conflict == ImportConflictMode.FAIL:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "conflicts": conflicts,
                    "hint": "Re-submit with on_conflict=replace to overwrite",
                },
            )

        # --- Phase 3: write sequentially ---
        imported_docs: list[str] = []
        imported_entities: list[str] = []
        for doc, _fm, parsed_entities in parsed_docs:
            if_match_etag: str | None = None
            if payload.on_conflict == ImportConflictMode.REPLACE:
                existing = await self._repo.get_document(project_id, doc.slug)
                if existing is not None:
                    # Pin the current version; replace_document increments it.
                    if_match_etag = f'"{doc.slug}@v{existing["version"]}"'
            try:
                await self._persist_document(
                    project_id=project_id,
                    slug=doc.slug,
                    raw_content=doc.content,
                    actor_id=actor_id,
                    if_match=if_match_etag,
                )
            except HTTPException:
                # Partial-write abort: surface what landed before the failure.
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={
                        "partially_imported_documents": imported_docs,
                        "partially_imported_entities": imported_entities,
                        "failed_slug": doc.slug,
                    },
                ) from None
            imported_docs.append(doc.slug)
            imported_entities.extend(e.frontmatter.id for e in parsed_entities)

        return ImportReport(
            imported_documents=imported_docs,
            imported_entities=imported_entities,
            summary=ImportSummary(
                documents=len(imported_docs),
                entities=len(imported_entities),
            ),
        )

    # ------------------------------------------------------------------
    # Entity operations
    # ------------------------------------------------------------------

    async def list_entities(
        self,
        project_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
        status_filter: RequirementStatus | None = None,
        category_filter: str | None = None,
        domain_filter: str | None = None,
        text_filter: str | None = None,
    ) -> tuple[list[EntityPublic], str | None]:
        rows = await self._repo.list_entities(
            project_id,
            limit=limit + 1,
            cursor_key=cursor,
            status=status_filter.value if status_filter else None,
            category=category_filter,
            domain=domain_filter,
            text=text_filter,
        )
        next_cursor: str | None = None
        if len(rows) > limit:
            next_cursor = rows[limit - 1]["_key"]
            rows = rows[:limit]
        return [_entity_from_doc(row) for row in rows], next_cursor

    async def get_entity(self, project_id: str, entity_id: str) -> EntityPublic:
        row = await self._repo.get_entity(project_id, entity_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="entity not found")
        return _entity_from_doc(row)

    async def update_entity(
        self,
        project_id: str,
        entity_id: str,
        actor_id: str,
        payload: EntityUpdate,
        if_match: str | None,
    ) -> EntityPublic:
        row = await self._repo.get_entity(project_id, entity_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="entity not found")

        _enforce_if_match(if_match, entity_id, row["version"])

        # Snapshot the current state to history BEFORE mutation (R-300-030).
        await self._snapshot_entity(project_id, row)

        # `mode="json"` coerces enum values to their string forms so the
        # row can be safely written back to Arango AND re-serialised into
        # the host Markdown document (yaml.safe_dump cannot represent
        # StrEnum instances directly).
        updates = payload.model_dump(exclude_none=True, mode="json")
        if _semantic_change(updates, row):
            row["version"] = int(row["version"]) + 1

        now = datetime.now(UTC).isoformat()
        row.update(updates)
        row["updated_at"] = now
        row["updated_by"] = actor_id

        # Refresh the MinIO source: re-serialise the host document with the
        # patched entity body.
        await self._rewrite_document_with_updated_entity(project_id, row)

        try:
            await self._repo.upsert_entity(row)
        except Exception as exc:  # deliberately broad (R-300-061)
            raise ConsistencyError(
                f"Failed to refresh derived index for {entity_id}: {exc}"
            ) from exc

        await self._publish(
            project_id,
            f"requirements.{project_id}.entity.updated",
            {"entity_id": entity_id, "version": row["version"], "actor": actor_id},
        )
        return _entity_from_doc(row)

    async def delete_entity(
        self,
        project_id: str,
        entity_id: str,
        actor_id: str,
        *,
        supersedes: str | None = None,
    ) -> None:
        """Soft-delete per R-300-033: transition to deprecated or superseded."""
        row = await self._repo.get_entity(project_id, entity_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="entity not found")

        await self._snapshot_entity(project_id, row)
        now = datetime.now(UTC).isoformat()
        if supersedes:
            row["status"] = RequirementStatus.SUPERSEDED.value
            row["superseded_by"] = supersedes
        else:
            row["status"] = RequirementStatus.DEPRECATED.value
            if not row.get("deprecated_reason"):
                row["deprecated_reason"] = "Soft-deleted via API"
        row["updated_at"] = now
        row["updated_by"] = actor_id
        row["version"] = int(row["version"]) + 1

        await self._rewrite_document_with_updated_entity(project_id, row)
        await self._repo.upsert_entity(row)
        await self._publish(
            project_id,
            f"requirements.{project_id}.entity.{'superseded' if supersedes else 'deprecated'}",
            {"entity_id": entity_id, "version": row["version"], "actor": actor_id},
        )

    # ------------------------------------------------------------------
    # History & relations
    # ------------------------------------------------------------------

    async def list_history(
        self, project_id: str, entity_id: str
    ) -> list[HistoryEntry]:
        rows = await self._repo.list_history(project_id, entity_id)
        return [
            HistoryEntry(
                entity_id=r["entity_id"],
                version=r["version"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                actor=r["actor"],
                change_summary=r.get("change_summary"),
                commit_ref=r.get("commit_ref"),
            )
            for r in rows
        ]

    async def list_relations(
        self, project_id: str, source_id: str, rel_type: RelationType | None
    ) -> list[RelationEdge]:
        rows = await self._repo.list_relations(
            project_id, source_id, rel_type.value if rel_type else None
        )
        return [
            RelationEdge(
                source_id=_extract_entity_id(r["_from"]),
                target_id=_extract_entity_id(r["_to"]),
                type=RelationType(r["type"]),
                version_pinned=r.get("version_pinned"),
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Tailoring
    # ------------------------------------------------------------------

    async def list_tailorings(self, project_id: str) -> list[TailoringReport]:
        rows = await self._repo.list_entities(project_id, limit=1000)
        reports: list[TailoringReport] = []
        for row in rows:
            parent_id = row.get("tailoring_of")
            if not parent_id:
                continue
            parent = await self._repo.get_entity(_PLATFORM_PROJECT_ID, parent_id)
            conformity: str = "stale-parent" if parent is None else "conformant"
            # Re-derive rationale excerpt by re-reading the document body
            try:
                body = await self._storage.get_document(
                    RequirementsStorage.document_path(project_id, row["document_slug"])
                )
                entities = parse_entities(body.decode("utf-8"))
                entity_body = next(
                    (e.body for e in entities if e.frontmatter.id == row["entity_id"]),
                    "",
                )
            except (FileNotFoundError, AdapterError):
                entity_body = ""
            excerpt = rationale_excerpt(entity_body)
            if not excerpt and conformity == "conformant":
                conformity = "missing-rationale"
            reports.append(
                TailoringReport(
                    project_entity_id=row["entity_id"],
                    project_entity_version=row["version"],
                    platform_parent_id=parent_id,
                    platform_parent_version=parent["version"] if parent else 0,
                    rationale_excerpt=excerpt,
                    conformity=conformity,  # type: ignore[arg-type]
                )
            )
        return reports

    # ------------------------------------------------------------------
    # Reindex (R-300-070..073)
    # ------------------------------------------------------------------

    async def start_reindex(self, project_id: str) -> ReindexJob:
        """Launch an async rebuild of the derived index.

        Online per R-300-071: reads/writes continue; the new index is
        built in place over the existing one (last-writer-wins on
        per-document basis).

        Idempotency (R-300-072): if a `running` job already exists for the
        project, return it instead of starting a second one.
        """
        existing = await self._repo.list_reindex_jobs_running(project_id)
        if existing:
            job_id = existing[0]["_key"]
            fresh = await self._repo.get_reindex_job(job_id)
            assert fresh is not None  # just fetched it
            return _reindex_from_row(fresh)

        job = ReindexJob(
            job_id=str(uuid.uuid4()),
            project_id=project_id,
            status=ReindexJobStatus.PENDING,
            submitted_at=datetime.now(UTC),
        )
        await self._repo.upsert_reindex_job(_reindex_to_row(job))
        # Fire-and-forget — the caller polls GET /reindex/{id} for status.
        # The task is stored via strong reference on the service so the
        # event loop does not garbage-collect it before completion.
        task = asyncio.create_task(self._run_reindex(job.job_id, project_id))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return job

    async def get_reindex_job(self, job_id: str) -> ReindexJob:
        row = await self._repo.get_reindex_job(job_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="reindex job not found"
            )
        return _reindex_from_row(row)

    async def _run_reindex(self, job_id: str, project_id: str) -> None:
        """Background worker for a reindex job."""
        row = await self._repo.get_reindex_job(job_id)
        if row is None:
            return
        row["status"] = ReindexJobStatus.RUNNING.value
        row["started_at"] = datetime.now(UTC).isoformat()
        await self._repo.upsert_reindex_job(row)

        try:
            prefix = (
                "platform/requirements/"
                if project_id == _PLATFORM_PROJECT_ID
                else f"projects/{project_id}/requirements/"
            )
            objects = await self._storage.list_objects(prefix)
            # Skip snapshot and deleted paths — only active sources count.
            doc_paths = [
                obj.path
                for obj in objects
                if obj.path.endswith(".md")
                and "/_history/" not in obj.path
                and "/_deleted/" not in obj.path
            ]
            row["total_entities"] = 0

            for path in doc_paths:
                slug = _slug_from_path(path)
                try:
                    raw = await self._storage.get_document(path)
                    fm, body = parse_document(raw.decode("utf-8"))
                    parsed_entities = parse_entities(body)
                except (FileNotFoundError, AdapterError):
                    continue

                content_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
                now = datetime.now(UTC).isoformat()
                existing = await self._repo.get_document(project_id, slug)
                doc_row = {
                    "_key": f"{project_id}:{slug}",
                    "project_id": project_id,
                    "slug": slug,
                    "version": fm.version,
                    "language": fm.language,
                    "status": fm.status.value,
                    "derives_from": fm.derives_from,
                    "minio_path": path,
                    "content_hash": content_hash,
                    "entity_count": len(parsed_entities),
                    "created_at": existing["created_at"] if existing else now,
                    "updated_at": now,
                }
                await self._repo.upsert_document(doc_row)

                for parsed in parsed_entities:
                    await self._upsert_entity_row(
                        project_id=project_id,
                        doc_slug=slug,
                        parsed=parsed,
                        actor_id="reindex",
                        content_hash=content_hash,
                    )
                    row["processed_entities"] = int(row.get("processed_entities", 0)) + 1
                    row["total_entities"] = int(row["total_entities"]) + 1
                await self._repo.upsert_reindex_job(row)

            row["status"] = ReindexJobStatus.COMPLETED.value
            row["completed_at"] = datetime.now(UTC).isoformat()
        except Exception as exc:  # deliberately broad — record failure then rethrow
            row["status"] = ReindexJobStatus.FAILED.value
            row["completed_at"] = datetime.now(UTC).isoformat()
            row["error"] = f"{type(exc).__name__}: {exc}"[:500]
        finally:
            await self._repo.upsert_reindex_job(row)
            await self._publish(
                project_id,
                f"requirements.{project_id}.reindex.{row['status']}",
                {"job_id": job_id, "processed": row.get("processed_entities", 0)},
            )

    # ------------------------------------------------------------------
    # Reconciliation (R-300-063) — single-pass tick for a project
    # ------------------------------------------------------------------

    async def reconcile_tick(self, project_id: str) -> ReconcileReport:
        """One reconciliation pass: detect and repair MinIO↔Arango drift.

        Not wired to a scheduler in v1 — the deployment operator runs this
        explicitly (admin endpoint) or via a K8s CronJob. Returns a
        structured report so callers can decide whether to alert.
        """
        prefix = (
            "platform/requirements/"
            if project_id == _PLATFORM_PROJECT_ID
            else f"projects/{project_id}/requirements/"
        )
        objects = await self._storage.list_objects(prefix)
        minio_docs = {
            _slug_from_path(obj.path): obj.path
            for obj in objects
            if obj.path.endswith(".md")
            and "/_history/" not in obj.path
            and "/_deleted/" not in obj.path
        }

        arango_docs_raw = await self._repo.list_documents(project_id, limit=10_000)
        arango_slugs = {row["slug"] for row in arango_docs_raw}

        missing_in_arango = [slug for slug in minio_docs if slug not in arango_slugs]
        orphaned_in_arango = [slug for slug in arango_slugs if slug not in minio_docs]

        stale_entries: list[str] = []
        for row in arango_docs_raw:
            if row["slug"] not in minio_docs:
                continue
            try:
                raw = await self._storage.get_document(minio_docs[row["slug"]])
            except FileNotFoundError:
                continue
            fresh_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
            if fresh_hash != row.get("content_hash"):
                stale_entries.append(row["slug"])

        repaired = 0
        for slug in missing_in_arango + stale_entries:
            try:
                raw = await self._storage.get_document(minio_docs[slug])
                fm, body = parse_document(raw.decode("utf-8"))
                parsed_entities = parse_entities(body)
            except (FileNotFoundError, AdapterError):
                continue
            now = datetime.now(UTC).isoformat()
            existing = await self._repo.get_document(project_id, slug)
            content_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
            await self._repo.upsert_document({
                "_key": f"{project_id}:{slug}",
                "project_id": project_id,
                "slug": slug,
                "version": fm.version,
                "language": fm.language,
                "status": fm.status.value,
                "derives_from": fm.derives_from,
                "minio_path": minio_docs[slug],
                "content_hash": content_hash,
                "entity_count": len(parsed_entities),
                "created_at": existing["created_at"] if existing else now,
                "updated_at": now,
            })
            for parsed in parsed_entities:
                await self._upsert_entity_row(
                    project_id=project_id,
                    doc_slug=slug,
                    parsed=parsed,
                    actor_id="reconcile",
                    content_hash=content_hash,
                )
            repaired += 1

        for slug in orphaned_in_arango:
            await self._repo.delete_document(project_id, slug)
            repaired += 1

        return ReconcileReport(
            project_id=project_id,
            missing_in_index=len(missing_in_arango),
            stale_in_index=len(stale_entries),
            orphaned_in_index=len(orphaned_in_arango),
            repaired=repaired,
        )

    # ------------------------------------------------------------------
    # Markdown export (R-300-084, R-300-086 streaming)
    # ------------------------------------------------------------------

    async def export_markdown_stream(
        self, project_id: str
    ) -> AsyncIterator[bytes]:
        """Stream the project's corpus as concatenated Markdown documents.

        Each document is preceded by a separator banner (`=== path ===`)
        so consumers can split the stream back into files. ReqIF export
        is deferred (A-5 plan, R-300-080). Point-in-time export (R-300-085)
        is also deferred.
        """
        # Enumerate every document (including deprecated/superseded per
        # R-300-084 — they are part of the auditable history).
        docs_raw = await self._repo.list_documents(project_id, limit=10_000)
        for row in docs_raw:
            banner = f"\n=== {row['minio_path']} ===\n".encode()
            yield banner
            try:
                body = await self._storage.get_document(row["minio_path"])
            except FileNotFoundError:
                yield b"(document body unavailable - MinIO source missing)\n"
                continue
            yield body
            # Guarantee a trailing newline so consumers can split on banners cleanly
            if not body.endswith(b"\n"):
                yield b"\n"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _persist_document(
        self,
        *,
        project_id: str,
        slug: str,
        raw_content: str,
        actor_id: str,
        if_match: str | None,
    ) -> DocumentPublic:
        try:
            fm, body = parse_document(raw_content)
            parsed_entities = parse_entities(body)
        except AdapterError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc

        # Per-entity methodology checks
        issues: list[ValidationIssue] = []
        is_project_level = project_id != _PLATFORM_PROJECT_ID
        for parsed in parsed_entities:
            issues.extend(check_deprecated_reason(parsed.frontmatter))
            if parsed.frontmatter.tailoring_of:
                parent = await self._repo.get_entity(
                    _PLATFORM_PROJECT_ID, parsed.frontmatter.tailoring_of
                )
                parent_fm: EntityFrontmatter | None = None
                if parent is not None:
                    parent_fm = _frontmatter_from_row(parent)
                issues.extend(
                    check_tailoring(
                        parsed.frontmatter,
                        parsed.body,
                        is_project_level=is_project_level,
                        platform_parent=parent_fm,
                    )
                )

        if issues:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "message": "document failed methodology validation",
                    "issues": [
                        {"rule": i.rule, "entity_id": i.entity_id, "message": i.message}
                        for i in issues
                    ],
                },
            )

        # Optimistic lock check on document version (R-300-023)
        existing = await self._repo.get_document(project_id, slug)
        if existing is not None:
            _enforce_if_match(if_match, slug, existing["version"])
            new_doc_version = int(existing["version"]) + 1
        else:
            new_doc_version = fm.version

        content_bytes = raw_content.encode("utf-8")
        content_hash = "sha256:" + hashlib.sha256(content_bytes).hexdigest()
        path = RequirementsStorage.document_path(project_id, slug)

        # Write-through: MinIO first, then Arango, then NATS (R-300-060)
        await self._storage.put_document(path, content_bytes)

        now = datetime.now(UTC).isoformat()
        doc_row = {
            "_key": f"{project_id}:{slug}",
            "project_id": project_id,
            "slug": slug,
            "version": new_doc_version,
            "language": fm.language,
            "status": fm.status.value,
            "derives_from": fm.derives_from,
            "minio_path": path,
            "content_hash": content_hash,
            "entity_count": len(parsed_entities),
            "created_at": existing["created_at"] if existing else now,
            "updated_at": now,
        }
        try:
            await self._repo.upsert_document(doc_row)
            for parsed in parsed_entities:
                await self._upsert_entity_row(
                    project_id=project_id,
                    doc_slug=slug,
                    parsed=parsed,
                    actor_id=actor_id,
                    content_hash=content_hash,
                )
        except Exception as exc:  # deliberately broad (R-300-061)
            raise ConsistencyError(
                f"Failed to refresh derived index for document {slug}: {exc}"
            ) from exc

        await self._publish(
            project_id,
            f"requirements.{project_id}.document.updated"
            if existing
            else f"requirements.{project_id}.document.created",
            {"slug": slug, "version": new_doc_version, "actor": actor_id},
        )
        return _document_from_doc(doc_row)

    async def _upsert_entity_row(
        self,
        *,
        project_id: str,
        doc_slug: str,
        parsed: ParsedEntity,
        actor_id: str,
        content_hash: str,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        fm = parsed.frontmatter
        entity_type = EntityType(fm.id.split("-", 1)[0])
        existing = await self._repo.get_entity(project_id, fm.id)
        row = {
            "_key": f"{project_id}:{fm.id}",
            "project_id": project_id,
            "entity_id": fm.id,
            "document_slug": doc_slug,
            "type": entity_type.value,
            "version": fm.version,
            "status": fm.status.value,
            "category": fm.category.value,
            "title": _extract_title(parsed.heading, fm.id),
            "body": parsed.body,
            "domain": fm.domain,
            "derives_from": fm.derives_from,
            "impacts": fm.impacts,
            "tailoring_of": fm.tailoring_of,
            "override": fm.override,
            "supersedes": fm.supersedes,
            "superseded_by": fm.superseded_by,
            "deprecated_reason": fm.deprecated_reason,
            "minio_path": RequirementsStorage.document_path(project_id, doc_slug),
            "content_hash": content_hash,
            "created_at": existing["created_at"] if existing else now,
            "updated_at": now,
            "created_by": existing["created_by"] if existing else actor_id,
            "updated_by": actor_id,
        }
        await self._repo.upsert_entity(row)
        # Refresh outbound relation edges for this entity
        key = str(row["_key"])
        edges = _edges_from_frontmatter(key, project_id, fm)
        await self._repo.replace_entity_relations(key, edges)

    async def _snapshot_entity(self, project_id: str, row: dict[str, Any]) -> None:
        snapshot_path = RequirementsStorage.history_path(
            project_id, row["document_slug"], row["entity_id"], int(row["version"])
        )
        # Fetch the current source to snapshot the pre-update body.
        try:
            current = await self._storage.get_document(row["minio_path"])
            await self._storage.put_document(snapshot_path, current)
        except FileNotFoundError:
            # Nothing to snapshot if the source is already gone.
            return
        await self._repo.append_history(
            {
                "_key": f"{project_id}:{row['entity_id']}:v{row['version']}",
                "project_id": project_id,
                "entity_id": row["entity_id"],
                "version": int(row["version"]),
                "minio_snapshot_path": snapshot_path,
                "timestamp": datetime.now(UTC).isoformat(),
                "actor": row["updated_by"],
                "change_summary": None,
                "commit_ref": None,
            }
        )

    async def _rewrite_document_with_updated_entity(
        self, project_id: str, row: dict[str, Any]
    ) -> None:
        """Re-serialise the host document with the patched entity block."""
        path = row["minio_path"]
        raw = await self._storage.get_document(path)
        text = raw.decode("utf-8")
        fm, body = parse_document(text)
        parsed_entities = parse_entities(body)

        new_body_lines: list[str] = []
        target_id = row["entity_id"]
        replaced = False

        # Rebuild the body by keeping untouched entities verbatim, replacing
        # only the one whose id matches.
        for parsed in parsed_entities:
            if parsed.frontmatter.id != target_id:
                continue
            # We found the target — rebuild its block around the updated frontmatter
            replaced = True
        body = _replace_entity_block(body, row) if replaced else _append_entity_block(body, row)

        # Bump document version to reflect the change (R-M100-050)
        fm_dict = fm.model_dump(mode="json", by_alias=True, exclude_none=True)
        fm_dict["version"] = int(fm.version) + 1
        new_fm = DocumentFrontmatter.model_validate(fm_dict)
        new_content = serialise_document(new_fm, body).encode("utf-8")
        await self._storage.put_document(path, new_content)

        # Refresh the document index record
        doc_row = await self._repo.get_document(project_id, row["document_slug"])
        if doc_row is not None:
            doc_row["version"] = new_fm.version
            doc_row["content_hash"] = "sha256:" + hashlib.sha256(new_content).hexdigest()
            doc_row["updated_at"] = datetime.now(UTC).isoformat()
            await self._repo.upsert_document(doc_row)

        # `new_body_lines` retained for future fine-grained rewriting; silence
        # lint without changing behavior.
        _ = new_body_lines

    async def _publish(
        self, project_id: str, subject: str, payload: dict[str, Any]
    ) -> None:
        envelope = {
            "event_id": str(uuid.uuid4()),
            "event_type": subject,
            "event_version": 1,
            "timestamp": datetime.now(UTC).isoformat(),
            "project_id": project_id,
            "payload": payload,
        }
        await self._publisher.publish(subject, envelope)


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


def get_service(request: Request) -> RequirementsService:
    """FastAPI dependency resolved from app.state.requirements_service."""
    svc = getattr(request.app.state, "requirements_service", None)
    if svc is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="requirements service not initialised",
        )
    return svc  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Pure helpers (no I/O)
# ---------------------------------------------------------------------------


def _document_relative_path(project_id: str, slug: str) -> str:
    if project_id == _PLATFORM_PROJECT_ID:
        return f"platform/requirements/{slug}.md"
    return f"projects/{project_id}/requirements/{slug}.md"


def _document_from_doc(doc: dict[str, Any]) -> DocumentPublic:
    return DocumentPublic(
        project_id=doc["project_id"],
        slug=doc["slug"],
        version=doc["version"],
        language=doc["language"],
        status=DocumentStatus(doc["status"]),
        entity_count=doc.get("entity_count", 0),
        derives_from=doc.get("derives_from", []),
        created_at=datetime.fromisoformat(doc["created_at"]),
        updated_at=datetime.fromisoformat(doc["updated_at"]),
    )


def _entity_from_doc(row: dict[str, Any]) -> EntityPublic:
    return EntityPublic(
        project_id=row["project_id"],
        entity_id=row["entity_id"],
        document_slug=row["document_slug"],
        type=EntityType(row["type"]),
        version=row["version"],
        status=RequirementStatus(row["status"]),
        category=row["category"],
        title=row["title"],
        body=row["body"],
        domain=row.get("domain"),
        derives_from=row.get("derives_from", []),
        impacts=row.get("impacts", []),
        tailoring_of=row.get("tailoring_of"),
        override=row.get("override"),
        supersedes=row.get("supersedes"),
        superseded_by=row.get("superseded_by"),
        deprecated_reason=row.get("deprecated_reason"),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        created_by=row["created_by"],
        updated_by=row["updated_by"],
    )


def _frontmatter_from_row(row: dict[str, Any]) -> EntityFrontmatter:
    payload = {
        "id": row["entity_id"],
        "version": row["version"],
        "status": row["status"],
        "category": row["category"],
        "derives-from": row.get("derives_from", []),
        "impacts": row.get("impacts", []),
        "tailoring-of": row.get("tailoring_of"),
        "override": row.get("override"),
        "supersedes": row.get("supersedes"),
        "superseded-by": row.get("superseded_by"),
        "deprecated-reason": row.get("deprecated_reason"),
        "domain": row.get("domain"),
    }
    return EntityFrontmatter.model_validate(
        {k: v for k, v in payload.items() if v is not None or k == "derives-from"}
    )


def _edges_from_frontmatter(
    entity_key: str, project_id: str, fm: EntityFrontmatter
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []

    def _to(target_id: str) -> str:
        return f"{COLL_ENTITIES}/{project_id}:{target_id}"

    for ref in fm.derives_from:
        # Strip optional version pin for the edge `_to` (R-M100-080)
        target, pinned = _split_reference(ref)
        edges.append(
            {
                "_from": f"{COLL_ENTITIES}/{entity_key}",
                "_to": _to(target),
                "type": RelationType.DERIVES_FROM.value,
                "version_pinned": pinned,
            }
        )
    for ref in fm.impacts:
        if "*" in ref:
            continue  # wildcard impacts resolved lazily by consumers
        target, pinned = _split_reference(ref)
        edges.append(
            {
                "_from": f"{COLL_ENTITIES}/{entity_key}",
                "_to": _to(target),
                "type": RelationType.IMPACTS.value,
                "version_pinned": pinned,
            }
        )
    if fm.tailoring_of:
        edges.append(
            {
                "_from": f"{COLL_ENTITIES}/{entity_key}",
                # Platform parents live under the `platform` project per R-300-010
                "_to": f"{COLL_ENTITIES}/{_PLATFORM_PROJECT_ID}:{fm.tailoring_of}",
                "type": RelationType.TAILORING_OF.value,
                "version_pinned": None,
            }
        )
    if fm.supersedes:
        edges.append(
            {
                "_from": f"{COLL_ENTITIES}/{entity_key}",
                "_to": _to(fm.supersedes),
                "type": RelationType.SUPERSEDES.value,
                "version_pinned": None,
            }
        )
    return edges


def _split_reference(ref: str) -> tuple[str, int | None]:
    if "@v" in ref:
        target, version = ref.split("@v", 1)
        return target, int(version)
    return ref, None


def _extract_entity_id(key: str) -> str:
    """Return the entity id portion of a `req_entities/<pid>:<eid>` key."""
    return key.split("/", 1)[1].split(":", 1)[1]


def _enforce_if_match(if_match: str | None, resource_id: str, current_version: int) -> None:
    """Validate the optimistic-lock header (R-300-022 / R-300-023)."""
    if if_match is None:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="If-Match header is required for this operation",
        )
    expected = f'"{resource_id}@v{current_version}"'
    # Be lenient on surrounding quotes but strict on the payload shape.
    normalised = if_match.strip()
    if not (normalised == expected or normalised.strip('"') == expected.strip('"')):
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "message": "If-Match version does not match current state",
                "expected": expected,
                "received": if_match,
                "current_version": current_version,
            },
        )


def _semantic_change(updates: dict[str, Any], row: dict[str, Any]) -> bool:
    """Per R-M100-060, increment version only on semantic-content changes."""
    semantic_fields = {
        "category",
        "body",
        "derives_from",
        "impacts",
        "tailoring_of",
        "override",
        "domain",
    }
    for field, new_value in updates.items():
        if field not in semantic_fields:
            continue
        if row.get(field) != new_value:
            return True
    return False


def _extract_title(heading: str, fallback: str) -> str:
    """Strip the entity ID prefix from the heading; fall back to id."""
    cleaned = heading.replace(fallback, "").strip(" :—-")
    return cleaned or fallback


def _dump_entity_frontmatter(
    entity: Any, *, initial_version: int, actor_id: str
) -> str:
    """Render an EntityCreate as a YAML frontmatter block suitable for embedding."""
    _ = actor_id  # actor is captured in the document-level metadata
    payload: dict[str, Any] = {
        "id": entity.entity_id,
        "version": initial_version,
        "status": entity.status.value,
        "category": entity.category.value,
    }
    if entity.derives_from:
        payload["derives-from"] = entity.derives_from
    if entity.impacts:
        payload["impacts"] = entity.impacts
    if entity.tailoring_of:
        payload["tailoring-of"] = entity.tailoring_of
    if entity.override is not None:
        payload["override"] = entity.override
    if entity.domain:
        payload["domain"] = entity.domain
    return str(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)).strip()


def _is_empty_value(value: Any) -> bool:
    """True when a frontmatter field should be omitted (None, empty list/str)."""
    if value is None:
        return True
    return isinstance(value, list | str) and len(value) == 0


def _replace_entity_block(body: str, row: dict[str, Any]) -> str:
    """Rebuild the document body with the updated frontmatter of `row`."""
    target_id = row["entity_id"]
    payload: dict[str, Any] = {
        "id": target_id,
        "version": row["version"],
        "status": row["status"],
        "category": row["category"],
    }
    for field, alias in (
        ("derives_from", "derives-from"),
        ("impacts", "impacts"),
        ("tailoring_of", "tailoring-of"),
        ("override", "override"),
        ("supersedes", "supersedes"),
        ("superseded_by", "superseded-by"),
        ("deprecated_reason", "deprecated-reason"),
        ("domain", "domain"),
    ):
        value = row.get(field)
        if _is_empty_value(value):
            continue
        payload[alias] = value
    new_fm = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).strip()

    # Locate the entity block by matching the YAML `id:` line, then replace
    # the surrounding fenced block.
    pattern = re.compile(
        rf"(\#+\s+[^\n]*{re.escape(target_id)}[^\n]*\n(?:[^\n]*\n)*?)"
        r"```ya?ml\s*\n(.*?)\n```",
        re.DOTALL,
    )
    match = pattern.search(body)
    if match is None:
        return body  # defensive: caller already ensured the entity exists
    replacement = f"{match.group(1)}```yaml\n{new_fm}\n```"
    return body[: match.start()] + replacement + body[match.end():]


def _append_entity_block(body: str, row: dict[str, Any]) -> str:
    """Append a new entity block at the end of the body."""
    prose = str(row.get("body", ""))
    return (
        body.rstrip()
        + "\n\n"
        + f"#### {row['entity_id']}\n"
        + "```yaml\n"
        + _yaml_dump_row(row)
        + "```\n\n"
        + prose
        + "\n"
    )


def _yaml_dump_row(row: dict[str, Any]) -> str:
    payload = {
        "id": row["entity_id"],
        "version": row["version"],
        "status": row["status"],
        "category": row["category"],
    }
    return str(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))


def _slug_from_path(path: str) -> str:
    """Extract the document slug (basename without `.md`) from a MinIO path."""
    tail = path.rsplit("/", 1)[-1]
    if tail.endswith(".md"):
        tail = tail[:-3]
    return tail


def _reindex_to_row(job: ReindexJob) -> dict[str, Any]:
    return {
        "_key": job.job_id,
        "project_id": job.project_id,
        "status": job.status.value,
        "submitted_at": job.submitted_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "processed_entities": job.processed_entities,
        "total_entities": job.total_entities,
        "error": job.error,
    }


def _reindex_from_row(row: dict[str, Any]) -> ReindexJob:
    def _parse_ts(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None

    return ReindexJob(
        job_id=row["_key"],
        project_id=row["project_id"],
        status=ReindexJobStatus(row["status"]),
        submitted_at=datetime.fromisoformat(row["submitted_at"]),
        started_at=_parse_ts(row.get("started_at")),
        completed_at=_parse_ts(row.get("completed_at")),
        processed_entities=int(row.get("processed_entities", 0)),
        total_entities=row.get("total_entities"),
        error=row.get("error"),
    )
