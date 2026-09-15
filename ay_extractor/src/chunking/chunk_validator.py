# src/chunking/chunk_validator.py — v1
"""Chunk validation ensuring integrity constraints.

Validates:
- No split inside atomic IMAGE_CONTENT / TABLE_CONTENT blocks (spec §7.3)
- Chunk size within bounds
- Sequential linking consistency
- Fingerprint uniqueness
See spec §4.1 step 2b.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ayextractor.core.models import Chunk

_OPEN_IMAGE = re.compile(r"<<<IMAGE_CONTENT\b")
_CLOSE_IMAGE = re.compile(r"<<<END_IMAGE_CONTENT>>>")
_OPEN_TABLE = re.compile(r"<<<TABLE_CONTENT\b")
_CLOSE_TABLE = re.compile(r"<<<END_TABLE_CONTENT>>>")


@dataclass
class ValidationResult:
    """Result of chunk validation."""

    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_chunks(
    chunks: list[Chunk],
    max_chunk_size: int = 10000,
    min_chunk_size: int = 50,
) -> ValidationResult:
    """Validate a list of chunks for integrity.

    Args:
        chunks: Ordered list of chunks to validate.
        max_chunk_size: Maximum allowed chunk size in chars.
        min_chunk_size: Minimum expected chunk size in chars.

    Returns:
        ValidationResult with errors and warnings.
    """
    result = ValidationResult()

    if not chunks:
        result.warnings.append("Empty chunk list")
        return result

    seen_ids: set[str] = set()
    seen_fps: set[str] = set()

    for i, chunk in enumerate(chunks):
        # Unique IDs
        if chunk.id in seen_ids:
            result.valid = False
            result.errors.append(f"Duplicate chunk ID: {chunk.id}")
        seen_ids.add(chunk.id)

        # Unique fingerprints
        if chunk.fingerprint in seen_fps:
            result.warnings.append(f"Duplicate fingerprint in {chunk.id}")
        seen_fps.add(chunk.fingerprint)

        # Position consistency
        if chunk.position != i:
            result.valid = False
            result.errors.append(f"Chunk {chunk.id} position {chunk.position} != index {i}")

        # Atomic block integrity
        n_open_img = len(_OPEN_IMAGE.findall(chunk.content))
        n_close_img = len(_CLOSE_IMAGE.findall(chunk.content))
        if n_open_img != n_close_img:
            result.valid = False
            result.errors.append(
                f"Chunk {chunk.id}: unbalanced IMAGE_CONTENT blocks "
                f"({n_open_img} open, {n_close_img} close)"
            )

        n_open_tbl = len(_OPEN_TABLE.findall(chunk.content))
        n_close_tbl = len(_CLOSE_TABLE.findall(chunk.content))
        if n_open_tbl != n_close_tbl:
            result.valid = False
            result.errors.append(
                f"Chunk {chunk.id}: unbalanced TABLE_CONTENT blocks "
                f"({n_open_tbl} open, {n_close_tbl} close)"
            )

        # Size
        if chunk.char_count > max_chunk_size:
            result.warnings.append(
                f"Chunk {chunk.id} exceeds max size: {chunk.char_count} > {max_chunk_size}"
            )
        if chunk.char_count < min_chunk_size:
            result.warnings.append(
                f"Chunk {chunk.id} below min size: {chunk.char_count} < {min_chunk_size}"
            )

    # Linking consistency
    for i, chunk in enumerate(chunks):
        if i > 0 and chunk.preceding_chunk_id != chunks[i - 1].id:
            result.warnings.append(
                f"Chunk {chunk.id} preceding_chunk_id mismatch: "
                f"expected {chunks[i-1].id}, got {chunk.preceding_chunk_id}"
            )
        if i < len(chunks) - 1 and chunk.following_chunk_id != chunks[i + 1].id:
            result.warnings.append(
                f"Chunk {chunk.id} following_chunk_id mismatch: "
                f"expected {chunks[i+1].id}, got {chunk.following_chunk_id}"
            )

    return result
