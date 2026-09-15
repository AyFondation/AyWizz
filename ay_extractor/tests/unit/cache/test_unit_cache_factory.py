# tests/unit/cache/test_unit_cache_factory.py — v4
"""Tests for cache/cache_factory.py — D-020 v1 (JSON-only)."""

from __future__ import annotations

import pytest

from ayextractor.cache.cache_factory import create_cache_store
from ayextractor.cache.json_store import JsonCacheStore
from ayextractor.config.settings import Settings


class TestCreateCacheStore:
    def test_default_json(self):
        """Default config produces a JsonCacheStore."""
        store = create_cache_store()
        assert isinstance(store, JsonCacheStore)

    def test_explicit_json(self, tmp_path):
        s = Settings(_env_file=None, cache_backend="json", cache_root=tmp_path)
        store = create_cache_store(s)
        assert isinstance(store, JsonCacheStore)

    def test_legacy_sqlite_raises(self):
        """`sqlite` backend was stripped by D-020 v1 — pydantic rejects it."""
        # The `cache_backend` field is `Literal["json"]` post-strip; passing
        # `sqlite` raises during Settings construction (validator) — earlier
        # than the factory itself.
        with pytest.raises((ValueError, Exception)):
            Settings(_env_file=None, cache_backend="sqlite")

    def test_legacy_redis_raises(self):
        with pytest.raises((ValueError, Exception)):
            Settings(_env_file=None, cache_backend="redis")

    def test_factory_unknown_backend_via_model_construct(self):
        """If a backend somehow bypasses pydantic, the factory raises."""
        s = Settings.model_construct(cache_backend="nonexistent")
        with pytest.raises(ValueError, match="Unsupported cache backend"):
            create_cache_store(s)
