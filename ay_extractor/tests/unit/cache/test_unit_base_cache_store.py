# tests/unit/cache/test_base_cache_store.py — v1
"""Tests for cache/base_cache_store.py — BaseCacheStore ABC."""

from __future__ import annotations

import pytest

from ayextractor.cache.base_cache_store import BaseCacheStore


class TestBaseCacheStore:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            BaseCacheStore()  # type: ignore[abstract]

    def test_has_required_methods(self):
        for method in ["get", "put", "delete", "lookup_fingerprint", "list_entries"]:
            assert hasattr(BaseCacheStore, method)
