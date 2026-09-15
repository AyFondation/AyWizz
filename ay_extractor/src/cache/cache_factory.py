# src/cache/cache_factory.py — v3
"""Factory for cache store instantiation.

D-020 v1 strip: only the JSON-backed cache survives (backed by MinIO when
deployed as C13 — the writer is the one configured under storage/).
The sqlite and redis adapters were physically removed; setting
`CACHE_BACKEND=sqlite|redis` now raises a clear error so the operator
fixes the config rather than silently falling back to JSON.
"""

from __future__ import annotations

from ayextractor.cache.base_cache_store import BaseCacheStore
from ayextractor.config.settings import Settings


def create_cache_store(settings: Settings | None = None) -> BaseCacheStore:
    """Instantiate the configured cache backend.

    Args:
        settings: Application settings. Defaults to JSON backend.

    Returns:
        Configured BaseCacheStore implementation.

    Raises:
        ValueError: If `CACHE_BACKEND` is set to anything other than ``json``.
            Per D-020 v1, the sqlite and redis backends have been removed.
    """
    backend = "json" if settings is None else settings.cache_backend
    simhash_threshold = 3 if settings is None else settings.simhash_threshold

    if backend == "json":
        from ayextractor.cache.json_store import JsonCacheStore
        cache_root = "output/.cache" if settings is None else str(settings.cache_root)
        return JsonCacheStore(
            cache_root=cache_root, simhash_threshold=simhash_threshold
        )

    raise ValueError(
        f"Unsupported cache backend: {backend!r}. "
        "D-020 v1 strip removed the sqlite and redis adapters; only `json` is supported."
    )
