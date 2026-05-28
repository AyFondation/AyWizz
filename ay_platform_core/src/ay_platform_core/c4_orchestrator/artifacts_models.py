# =============================================================================
# File: artifacts_models.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/artifacts_models.py
# Description: Pydantic models for the project-artifacts surface (Pass 1
#              of the Code source / DocGen feature). The UX browses these
#              via the new read-only REST surface declared in R-200-131 ;
#              MinIO never appears in the UI — every blob transits
#              through `GET .../blob` (R-200-133 transparent backend).
#
#              v2 (2026-05-21) : `ArtifactNode.version` — per-file
#              revision count for live-docs, batched per AI response
#              (one bump per assistant turn that modified the file, even
#              if that turn wrote it multiple times). Derived from the
#              Gitea commit history (R-200-147 versioning proxy) ; None
#              for non-live-docs runs and when Gitea is unavailable.
#
# @relation implements:R-200-130
# @relation implements:R-200-131
# @relation implements:R-200-132
# =============================================================================

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ArtifactRunStatus(StrEnum):
    """Lifecycle status of an artifact run, mirroring `RunStatus` but
    scoped to the artifact bookkeeping (an orchestrator run can be
    `BLOCKED` mid-pipeline yet still have artifacts to surface — the
    artifact run's status reflects whether the file tree is complete,
    not whether the orchestrator marked the run done)."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ArtifactRunPublic(BaseModel):
    """One row in the `GET /api/v1/projects/{pid}/artifacts/runs`
    list. Just enough metadata for the UX to display the run picker ;
    file-level details land in `ArtifactNode` (one per file under the
    run's MinIO prefix). R-200-132."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    project_id: str
    tenant_id: str
    started_at: datetime
    completed_at: datetime | None = None
    status: ArtifactRunStatus
    file_count: int = Field(default=0, ge=0)
    total_bytes: int = Field(default=0, ge=0)
    # Free-form short label for the operator (e.g. "Initial scaffold",
    # "Iteration 3 after coverage gate"). Optional — falls back to
    # `run_id` short prefix in the UI if absent.
    label: str | None = None


class ArtifactRunList(BaseModel):
    """Wrapper for the runs listing — keeps room for pagination cursors
    later without a contract break."""

    model_config = ConfigDict(extra="forbid")

    runs: list[ArtifactRunPublic]


class ArtifactNode(BaseModel):
    """One entry of the per-run tree returned by
    `GET /api/v1/projects/{pid}/artifacts/runs/{rid}/tree`. Flat list ;
    the UX rebuilds the hierarchy client-side by splitting `path` on
    `/`. POSIX-style forward slashes only (R-200-130)."""

    model_config = ConfigDict(extra="forbid")

    path: str
    kind: Literal["file", "dir"]
    size_bytes: int = Field(ge=0)
    mime_type: str | None = None
    # Per-file revision count, batched per AI response (live-docs only).
    # None when the run is not live-docs, the node is a directory, or
    # the Gitea history is unavailable. The UX renders `name (vN)`.
    version: int | None = Field(default=None, ge=1)


class ArtifactTree(BaseModel):
    """Wrapper for the tree response — same forward-compat reasoning
    as `ArtifactRunList`."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    nodes: list[ArtifactNode]


class ArtifactCommit(BaseModel):
    """One commit returned by the platform-proxied
    `GET /api/v1/projects/{pid}/git/commits` endpoint (R-200-147).
    Tighter shape than the raw Gitea wire — only the fields the UX
    actually surfaces."""

    model_config = ConfigDict(extra="forbid")

    sha: str
    message: str
    author_name: str
    author_email: str
    committed_at: datetime


class ArtifactCommitList(BaseModel):
    """Wrapper for the commits response — keeps room for pagination
    cursors when the UX grows past 50 per page."""

    model_config = ConfigDict(extra="forbid")

    commits: list[ArtifactCommit]
    page: int
