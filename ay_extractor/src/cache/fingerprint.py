# src/cache/fingerprint.py — v2
"""Multi-level document fingerprinting inspired by Shazam constellation approach.

Levels 1-3 are implemented (exact hash, content hash, structural hash).
Levels 4-5 (semantic/constellation) require embeddings and are deferred.
See spec §8.1 for full documentation.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from ayextractor.cache.models import DocumentFingerprint


def compute_fingerprint(
    raw_bytes: bytes,
    extracted_text: str,
    source_format: str,
) -> DocumentFingerprint:
    """Compute multi-level document fingerprint.

    Args:
        raw_bytes: Original document bytes.
        extracted_text: Extracted and cleaned text.
        source_format: Document format (pdf, md, txt, etc.).

    Returns:
        DocumentFingerprint with levels 1-3 computed, 4-5 as placeholders.
    """
    exact_hash = _level1_exact_hash(raw_bytes)
    content_hash = _level2_content_hash(extracted_text)
    structural_hash = _level3_simhash(extracted_text)

    return DocumentFingerprint(
        exact_hash=exact_hash,
        content_hash=content_hash,
        structural_hash=structural_hash,
        semantic_hash="",  # Level 4: requires embeddings
        constellation=[],  # Level 5: requires section analysis
        timestamp=datetime.now(timezone.utc),
        source_format=source_format,
    )


def _level1_exact_hash(raw_bytes: bytes) -> str:
    """Level 1: SHA-256 on raw document bytes."""
    return hashlib.sha256(raw_bytes).hexdigest()


def _level2_content_hash(text: str) -> str:
    """Level 2: SHA-256 on normalized text (case/whitespace/punctuation stripped)."""
    normalized = _normalize_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _level3_simhash(text: str, n: int = 3) -> str:
    """Level 3: SimHash on character n-grams.

    Returns a hex string representation of the SimHash.
    """
    normalized = _normalize_text(text)
    if not normalized:
        return "0" * 16

    shingles = _make_shingles(normalized, n)
    if not shingles:
        return "0" * 16

    vector = [0] * 64
    for shingle in shingles:
        h = int(hashlib.md5(shingle.encode("utf-8")).hexdigest(), 16)  # noqa: S324
        for i in range(64):
            if h & (1 << i):
                vector[i] += 1
            else:
                vector[i] -= 1

    fingerprint = 0
    for i in range(64):
        if vector[i] >= 0:
            fingerprint |= 1 << i

    return f"{fingerprint:016x}"


def hamming_distance(hash_a: str, hash_b: str) -> int:
    """Compute Hamming distance between two SimHash hex strings."""
    if not hash_a and not hash_b:
        return 0
    if not hash_a or not hash_b:
        return 64  # Max distance if one is empty
    try:
        a = int(hash_a, 16)
        b = int(hash_b, 16)
    except ValueError:
        return 64  # Max distance
    xor = a ^ b
    return bin(xor).count("1")


def _normalize_text(text: str) -> str:
    """Normalize text: lowercase, strip whitespace and punctuation."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _make_shingles(text: str, n: int) -> list[str]:
    """Create character n-grams from text."""
    words = text.split()
    if len(words) < n:
        return [text]
    return [" ".join(words[i : i + n]) for i in range(len(words) - n + 1)]
