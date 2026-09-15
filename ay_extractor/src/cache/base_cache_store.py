# src/cache/base_cache_store.py — v1
"""Abstract cache store interface.

See spec §30.4 for full documentation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ayextractor.cache.models import CacheEntry, CacheLookupResult, DocumentFingerprint


class BaseCacheStore(ABC):
    """Unified interface for cache storage backends."""

    @abstractmethod
    async def get(self, key: str) -> CacheEntry | None:
        """Retrieve cache entry by fingerprint key."""

    @abstractmethod
    async def put(self, key: str, entry: CacheEntry) -> None:
        """Store cache entry."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove cache entry."""

    @abstractmethod
    async def lookup_fingerprint(
        self, fingerprint: DocumentFingerprint
    ) -> CacheLookupResult:
        """Multi-level fingerprint lookup."""

    @abstractmethod
    async def list_entries(self) -> list[CacheEntry]:
        """List all cached entries (for batch scan dedup)."""
