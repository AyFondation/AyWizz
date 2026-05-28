# =============================================================================
# File: models.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/models.py
# Description: Pydantic v2 models for C5 Requirements Service.
#              Public contracts: EntityPublic (R-300-005 boundary),
#              DocumentPublic, EntityCreate, EntityUpdate, HistoryEntry,
#              TailoringReport. Internal models carry storage-only fields
#              (content hash, MinIO path) that SHALL NOT cross the API
#              boundary.
#
# @relation implements:R-300-001
# @relation implements:R-300-005
# @relation implements:R-300-024
# @relation implements:R-300-050
# =============================================================================

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Enumerations — align exactly with meta/100-SPEC-METHODOLOGY.md §3.4 and §6.1
# ---------------------------------------------------------------------------


class EntityType(StrEnum):
    """Entity type prefix. See meta/100-SPEC-METHODOLOGY.md R-M100-020."""

    R = "R"  # Requirement
    D = "D"  # Decision (lives exclusively in 999-SYNTHESIS)
    T = "T"  # Validation artifact
    E = "E"  # Entity / contract
    Q = "Q"  # Open question


class RequirementStatus(StrEnum):
    """Entity lifecycle states per R-M100-090. Terminal: superseded, deprecated."""

    DRAFT = "draft"
    APPROVED = "approved"
    SUPERSEDED = "superseded"
    DEPRECATED = "deprecated"


class DocumentStatus(StrEnum):
    """Document-level status. No `deprecated` at document level per R-M100-030."""

    DRAFT = "draft"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class EntityCategory(StrEnum):
    """Closed set per R-M100-040. Rejecting unknown values is R-M100-041 behavior."""

    FUNCTIONAL = "functional"
    NFR = "nfr"
    SAFETY = "safety"
    SECURITY = "security"
    REGULATORY = "regulatory"
    UX = "ux"
    TOOLING = "tooling"
    ARCHITECTURE = "architecture"
    METHODOLOGY = "methodology"
    INFRASTRUCTURE = "infrastructure"
    FUNCTIONAL_SCOPE = "functional-scope"
    PIPELINE_DESIGN = "pipeline-design"
    MEMORY_RAG = "memory-rag"


class RelationType(StrEnum):
    """Supported edge types in req_relations (R-300-012)."""

    DERIVES_FROM = "derives-from"
    IMPACTS = "impacts"
    TAILORING_OF = "tailoring-of"
    SUPERSEDES = "supersedes"
    SUPERSEDED_BY = "superseded-by"


class ReindexJobStatus(StrEnum):
    """Lifecycle of an async reindex job (R-300-070)."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# ID patterns — enforce R-M100-020 at the validation boundary
# ---------------------------------------------------------------------------

# Typed entities: `<TYPE>-<DOC-RANGE>-<SEQ>` where DOC-RANGE = NNN or MNNN.
_ENTITY_ID_TYPED = re.compile(r"^(R|T|E|Q)-(M?[0-9]{3})-[0-9]{3}$")
# Decisions are flat (see R-M100-020 exception).
_ENTITY_ID_DECISION = re.compile(r"^D-[0-9]{3}$")
# Version-pinned reference: `<ID>@v<N>` per R-M100-080.
_VERSIONED_REF = re.compile(r"^(?:(?:R|T|E|Q)-M?[0-9]{3}-[0-9]{3}|D-[0-9]{3})@v[0-9]+$")
# Document slug: `NNN-SPEC-<slug>`. Tolerant of uppercase and hyphen.
_DOCUMENT_SLUG = re.compile(r"^[0-9]{3}-[A-Z]+-[A-Z0-9-]+$")


def is_valid_entity_id(value: str) -> bool:
    return bool(_ENTITY_ID_TYPED.match(value) or _ENTITY_ID_DECISION.match(value))


def is_valid_entity_reference(value: str) -> bool:
    """Accept unversioned entity IDs or `<ID>@v<N>` pinned references."""
    return is_valid_entity_id(value) or bool(_VERSIONED_REF.match(value))


# ---------------------------------------------------------------------------
# Frontmatter schemas — strict per R-M100-040/041 (R-300-005 enforcement)
# ---------------------------------------------------------------------------


class DocumentFrontmatter(BaseModel):
    """Document-level frontmatter per R-M100-030. Unknown fields rejected."""

    model_config = ConfigDict(extra="forbid")

    document: str
    version: int = Field(ge=1)
    path: str
    language: str = "en"
    status: DocumentStatus
    derives_from: list[str] = Field(default_factory=list, alias="derives-from")


class EntityFrontmatter(BaseModel):
    """Entity-level frontmatter per R-M100-040. Unknown fields rejected."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    version: int = Field(ge=1)
    status: RequirementStatus
    category: EntityCategory
    derives_from: list[str] = Field(default_factory=list, alias="derives-from")
    impacts: list[str] = Field(default_factory=list)
    tailoring_of: str | None = Field(default=None, alias="tailoring-of")
    override: bool | None = None
    supersedes: str | None = None
    superseded_by: str | None = Field(default=None, alias="superseded-by")
    deprecated_reason: str | None = Field(default=None, alias="deprecated-reason")
    domain: str | None = None

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not is_valid_entity_id(v):
            raise ValueError(
                f"Invalid entity id '{v}': must match <TYPE>-<NNN|MNNN>-<SEQ> "
                "or D-NNN (see meta/100-SPEC-METHODOLOGY.md R-M100-020)"
            )
        return v

    @field_validator("derives_from", "impacts")
    @classmethod
    def _validate_refs(cls, v: list[str]) -> list[str]:
        # `impacts` may contain wildcards (e.g. R-300-*), so only check exact-format
        # entries. Wildcard patterns are permitted.
        for ref in v:
            if "*" in ref:
                continue
            if not is_valid_entity_reference(ref):
                raise ValueError(f"Invalid entity reference: {ref!r}")
        return v


# ---------------------------------------------------------------------------
# Public API models — the contract consumed by C3/C4/C6/C7/C9
# ---------------------------------------------------------------------------


class EntityPublic(BaseModel):
    """Entity as exposed through the REST API.

    NOT exposed: content_hash, raw MinIO path (internal invariants).
    """

    model_config = ConfigDict(extra="forbid")

    project_id: str
    entity_id: str
    document_slug: str
    type: EntityType
    version: int
    status: RequirementStatus
    category: EntityCategory
    title: str
    body: str
    domain: str | None = None
    derives_from: list[str] = Field(default_factory=list)
    impacts: list[str] = Field(default_factory=list)
    tailoring_of: str | None = None
    override: bool | None = None
    supersedes: str | None = None
    superseded_by: str | None = None
    deprecated_reason: str | None = None
    created_at: datetime
    updated_at: datetime
    created_by: str
    updated_by: str


class DocumentPublic(BaseModel):
    """Document descriptor — metadata + optional body."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    slug: str
    version: int
    language: str
    status: DocumentStatus
    entity_count: int
    derives_from: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    body: str | None = None  # Included only on GET /documents/{slug}


# ---------------------------------------------------------------------------
# Mutating request models
# ---------------------------------------------------------------------------


class EntityCreate(BaseModel):
    """POST body for creating an entity within an existing document.

    v1 scope: entities are created as part of document writes, so standalone
    POST .../entities is not exposed. This schema exists for embedding in
    document-level write requests and for the internal service layer.
    """

    model_config = ConfigDict(extra="forbid")

    entity_id: str
    type: EntityType
    status: RequirementStatus = RequirementStatus.DRAFT
    category: EntityCategory
    title: str
    body: str
    domain: str | None = None
    derives_from: list[str] = Field(default_factory=list)
    impacts: list[str] = Field(default_factory=list)
    tailoring_of: str | None = None
    override: bool | None = None

    @field_validator("entity_id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not is_valid_entity_id(v):
            raise ValueError(f"Invalid entity id: {v!r}")
        return v


class EntityUpdate(BaseModel):
    """PATCH body. Every field optional; version bump logic lives in the service."""

    model_config = ConfigDict(extra="forbid")

    status: RequirementStatus | None = None
    category: EntityCategory | None = None
    title: str | None = None
    body: str | None = None
    domain: str | None = None
    derives_from: list[str] | None = None
    impacts: list[str] | None = None
    tailoring_of: str | None = None
    override: bool | None = None
    supersedes: str | None = None
    superseded_by: str | None = None
    deprecated_reason: str | None = None


class DocumentCreate(BaseModel):
    """POST body for creating a new document (empty or with initial entities)."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    language: str = "en"
    status: DocumentStatus = DocumentStatus.DRAFT
    derives_from: list[str] = Field(default_factory=list)
    body: str = ""
    entities: list[EntityCreate] = Field(default_factory=list)

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        if not _DOCUMENT_SLUG.match(v):
            raise ValueError(
                f"Invalid document slug {v!r}: expected NNN-<KIND>-<SLUG> "
                "(e.g. 300-SPEC-REQUIREMENTS-MGMT)"
            )
        return v


class DocumentReplace(BaseModel):
    """PUT body — full raw document content (frontmatter + body).

    The server parses the content and re-validates every entity against
    R-M100-040/041. Non-conforming writes are rejected with HTTP 422.
    """

    model_config = ConfigDict(extra="forbid")

    content: str


# ---------------------------------------------------------------------------
# Bulk import (R-300-080..083)
# ---------------------------------------------------------------------------


class ImportConflictMode(StrEnum):
    """Behaviour when an incoming document or entity already exists."""

    FAIL = "fail"
    REPLACE = "replace"


class ImportDocument(BaseModel):
    """One document in an import package — raw Markdown + YAML body."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    content: str

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        if not _DOCUMENT_SLUG.match(v):
            raise ValueError(f"invalid document slug: {v!r}")
        return v


class ImportRequest(BaseModel):
    """POST /api/v1/projects/{pid}/requirements/import body (format=md)."""

    model_config = ConfigDict(extra="forbid")

    documents: list[ImportDocument] = Field(min_length=1)
    on_conflict: ImportConflictMode = ImportConflictMode.FAIL


class ImportSummary(BaseModel):
    """Counts surface in the response for quick diagnostics."""

    model_config = ConfigDict(extra="forbid")

    documents: int
    entities: int


class ImportReport(BaseModel):
    """Response body for a successful import."""

    model_config = ConfigDict(extra="forbid")

    imported_documents: list[str]
    imported_entities: list[str]
    summary: ImportSummary


# ---------------------------------------------------------------------------
# History, tailoring, reindex, import/export (mostly stub surfaces in v1)
# ---------------------------------------------------------------------------


class HistoryEntry(BaseModel):
    """One snapshot pointer per R-300-032."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str
    version: int
    timestamp: datetime
    actor: str
    change_summary: str | None = None
    commit_ref: str | None = None


class TailoringReport(BaseModel):
    """Row of GET .../tailorings per R-300-052."""

    model_config = ConfigDict(extra="forbid")

    project_entity_id: str
    project_entity_version: int
    platform_parent_id: str
    platform_parent_version: int
    rationale_excerpt: str
    conformity: Literal["conformant", "stale-parent", "missing-rationale"]


class ReindexJob(BaseModel):
    """Reindex job descriptor per R-300-070."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    project_id: str
    status: ReindexJobStatus
    submitted_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    processed_entities: int = 0
    total_entities: int | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# List envelopes — used by GET endpoints (R-300-025 cursor pagination)
# ---------------------------------------------------------------------------


class EntityListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entities: list[EntityPublic]
    next_cursor: str | None = None


class DocumentListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documents: list[DocumentPublic]
    next_cursor: str | None = None


class HistoryListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    history: list[HistoryEntry]


class RelationListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relations: list[RelationEdge]


class RelationEdge(BaseModel):
    """Edge in req_relations — exposed on GET .../relations."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    target_id: str
    type: RelationType
    version_pinned: int | None = None


# Resolve forward reference on RelationListResponse
RelationListResponse.model_rebuild()
