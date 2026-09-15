# src/cache/json_store.py — v1
"""JSON file-based cache store (default CACHE_BACKEND=json).

Stores cache entries as individual JSON files under CACHE_ROOT.
See spec §8.3 for cache lookup logic.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ayextractor.cache.base_cache_store import BaseCacheStore
from ayextractor.cache.fingerprint import hamming_distance
from ayextractor.cache.models import CacheEntry, CacheLookupResult, DocumentFingerprint

logger = logging.getLogger(__name__)


class JsonCacheStore(BaseCacheStore):
    """File-based cache store using JSON files."""

    def __init__(self, cache_root: Path, simhash_threshold: int = 3) -> None:
        self._root = Path(cache_root).expanduser()
        self._root.mkdir(parents=True, exist_ok=True)
        self._simhash_threshold = simhash_threshold

    async def get(self, key: str) -> CacheEntry | None:
        """Retrieve cache entry by key."""
        path = self._entry_path(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return CacheEntry(**data)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Failed to read cache entry %s: %s", key, e)
            return None

    async def put(self, key: str, entry: CacheEntry) -> None:
        """Store a cache entry."""
        path = self._entry_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(entry.model_dump_json(indent=2), encoding="utf-8")

    async def delete(self, key: str) -> None:
        """Remove a cache entry."""
        path = self._entry_path(key)
        if path.exists():
            path.unlink()

    async def lookup_fingerprint(
        self, fingerprint: DocumentFingerprint
    ) -> CacheLookupResult:
        """Multi-level fingerprint lookup (spec §8.3).

        Checks levels in order: exact → content → structural.
        """
        entries = await self.list_entries()

        for entry in entries:
            fp = entry.fingerprint

            # Level 1: Exact hash
            if fp.exact_hash == fingerprint.exact_hash:
                return CacheLookupResult(
                    hit_level="exact",
                    matched_entry=entry,
                    similarity_score=1.0,
                    is_reusable=True,
                )

            # Level 2: Content hash
            if fp.content_hash == fingerprint.content_hash:
                return CacheLookupResult(
                    hit_level="content",
                    matched_entry=entry,
                    similarity_score=1.0,
                    is_reusable=True,
                )

            # Level 3: Structural hash (SimHash with Hamming distance)
            if fp.structural_hash and fingerprint.structural_hash:
                distance = hamming_distance(
                    fp.structural_hash, fingerprint.structural_hash
                )
                if distance <= self._simhash_threshold:
                    return CacheLookupResult(
                        hit_level="structural",
                        matched_entry=entry,
                        similarity_score=1.0 - (distance / 64.0),
                        is_reusable=False,
                    )

        return CacheLookupResult()

    async def list_entries(self) -> list[CacheEntry]:
        """List all cached entries."""
        entries: list[CacheEntry] = []
        if not self._root.is_dir():
            return entries

        for path in self._root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                entries.append(CacheEntry(**data))
            except Exception:
                continue

        return entries

    def _entry_path(self, key: str) -> Path:
        """Return file path for a cache key."""
        safe_key = key.replace("/", "_").replace("\\", "_")
        return self._root / f"{safe_key}.json"
