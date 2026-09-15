# src/cache/models.py — v1
"""Cache domain models: DocumentFingerprint, CacheEntry, CacheLookupResult.

See spec §8 for full documentation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class DocumentFingerprint(BaseModel):
    """Multi-level document fingerprint for cache lookup."""

    exact_hash: str
    content_hash: str
    structural_hash: str
    semantic_hash: str
    constellation: list[str]
    timestamp: datetime
    source_format: str


class CacheEntry(BaseModel):
    """Single cache entry linking fingerprint to analysis output."""

    document_id: str
    fingerprint: DocumentFingerprint
    result_path: str
    created_at: datetime
    pipeline_version: str


class CacheLookupResult(BaseModel):
    """Result of a multi-level fingerprint lookup."""

    hit_level: (
        Literal[
            "exact", "content", "structural", "semantic", "constellation"
        ]
        | None
    ) = None
    matched_entry: CacheEntry | None = None
    similarity_score: float | None = None
    is_reusable: bool = False
