# tests/unit/cache/test_models.py — v1
"""Tests for cache/models.py — fingerprint and cache entry models."""

from __future__ import annotations

from datetime import datetime, timezone

from ayextractor.cache.models import CacheEntry, CacheLookupResult, DocumentFingerprint


class TestDocumentFingerprint:
    def test_create(self):
        fp = DocumentFingerprint(
            exact_hash="aabb",
            content_hash="ccdd",
            structural_hash="eeff",
            semantic_hash="1122",
            constellation=["anchor1", "anchor2"],
            timestamp=datetime(2026, 2, 7, tzinfo=timezone.utc),
            source_format="pdf",
        )
        assert fp.source_format == "pdf"
        assert len(fp.constellation) == 2


class TestCacheEntry:
    def test_create(self):
        fp = DocumentFingerprint(
            exact_hash="a", content_hash="b", structural_hash="c",
            semantic_hash="d", constellation=[], source_format="pdf",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        ce = CacheEntry(
            document_id="doc1", fingerprint=fp,
            result_path="/out/doc1", created_at=datetime.now(timezone.utc),
            pipeline_version="0.1.0",
        )
        assert ce.document_id == "doc1"


class TestCacheLookupResult:
    def test_no_hit(self):
        r = CacheLookupResult()
        assert r.hit_level is None
        assert r.is_reusable is False

    def test_exact_hit(self):
        r = CacheLookupResult(hit_level="exact", is_reusable=True)
        assert r.is_reusable is True
