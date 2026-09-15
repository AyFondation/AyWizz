# src/storage/models.py — v1
"""Storage domain models: RunManifest, StepManifest.

See spec §5 for full documentation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class StepManifest(BaseModel):
    """Manifest for a single pipeline step."""

    origin: Literal["fresh", "carried_from"]
    carried_from: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    output_hash: str | None = None


class RunManifest(BaseModel):
    """Full manifest for a pipeline run, written to run_manifest.json."""

    run_id: str
    document_id: str
    pipeline_version: str
    created_at: datetime
    completed_at: datetime | None = None
    status: Literal["running", "completed", "failed", "partial"]
    config_overrides_applied: dict[str, Any]
    llm_assignments: dict[str, str]
    prompt_hashes: dict[str, str | None]
    steps: dict[str, StepManifest]
