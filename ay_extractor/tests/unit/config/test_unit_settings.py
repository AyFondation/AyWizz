# tests/unit/config/test_unit_settings.py — v4 (D-020 post-strip)
"""Tests for config/settings.py — typed Settings v3 surface.

Complete rewrite from v2.1 (skip-marked). The v2 surface assumed ~90
fields covering rag/consolidator/vector/graph backends that D-020 v1
stripped; the v3 surface keeps only Phase 1+2 + MinIO + C8 routing +
embeddings. Tests are deliberately narrower and faster.
"""

from __future__ import annotations

import pytest

from ayextractor.config.settings import ConfigurationError, Settings


class TestDefaults:
    """The default Settings instance should be valid and fit the D-020 v1 scope."""

    def test_default_instance_constructs(self):
        s = Settings(_env_file=None)
        assert s is not None

    def test_default_llm_routing_openai_compat(self):
        s = Settings(_env_file=None)
        # D-020 v2 — provider routing is collapsed to OpenAI-compat;
        # actual upstream resolved by C8 agent_routes.
        assert s.llm_default_provider == "openai"
        assert s.openai_base_url == "http://c8:8000/v1"

    def test_default_output_writer_is_minio(self):
        s = Settings(_env_file=None)
        # D-020 session 2 strip — only `minio` survives.
        assert s.output_writer == "minio"
        assert s.output_minio_bucket == "c13-extractor-artifacts"

    def test_default_cache_backend_is_json(self):
        s = Settings(_env_file=None)
        # D-020 session 2 strip — sqlite/redis backends physically removed.
        assert s.cache_backend == "json"

    def test_default_quality_tier_implicit_minimal(self):
        """The quality_tier setting lives on `Metadata`, not `Settings` — but
        the agent toggles default to the `minimal` semantics here (no LLM
        unless image_analyzer is invoked)."""
        s = Settings(_env_file=None)
        # decontextualization_enabled is True at the Settings level (a
        # per-document override gates it on quality_tier=high).
        assert s.decontextualization_enabled is True

    def test_default_urgency_interactive(self):
        s = Settings(_env_file=None)
        assert s.urgency == "interactive"

    def test_default_embeddings_at_extractor_true(self):
        """D-020 v2 §B1 — embeddings produced by C13, not C7."""
        s = Settings(_env_file=None)
        assert s.embeddings_at_extractor is True

    def test_default_embedding_model_voyage_3(self):
        s = Settings(_env_file=None)
        assert s.embedding_model == "voyage-3"
        assert s.embedding_batch_size == 100


class TestValidators:
    """Validator V-05 — chunk_overlap must be < chunk_target_size."""

    def test_chunk_overlap_negative_rejected(self):
        with pytest.raises(ValueError, match="chunk_overlap must be >= 0"):
            Settings(_env_file=None, chunk_overlap=-1)

    def test_chunk_overlap_equal_target_size_rejected(self):
        with pytest.raises(ConfigurationError, match="CHUNK_OVERLAP"):
            Settings(_env_file=None, chunk_target_size=100, chunk_overlap=100)

    def test_chunk_overlap_greater_than_target_rejected(self):
        with pytest.raises(ConfigurationError, match="CHUNK_OVERLAP"):
            Settings(_env_file=None, chunk_target_size=100, chunk_overlap=200)

    def test_chunk_overlap_just_below_target_accepted(self):
        s = Settings(_env_file=None, chunk_target_size=100, chunk_overlap=99)
        assert s.chunk_overlap == 99


class TestLiteralFields:
    """`Literal[...]` fields reject anything outside the stripped surface."""

    def test_output_writer_rejects_local(self):
        # `local` was removed in D-020 v1 strip.
        with pytest.raises(ValueError):
            Settings(_env_file=None, output_writer="local")

    def test_output_writer_rejects_s3(self):
        with pytest.raises(ValueError):
            Settings(_env_file=None, output_writer="s3")

    def test_cache_backend_rejects_sqlite(self):
        with pytest.raises(ValueError):
            Settings(_env_file=None, cache_backend="sqlite")

    def test_cache_backend_rejects_redis(self):
        with pytest.raises(ValueError):
            Settings(_env_file=None, cache_backend="redis")

    def test_urgency_rejects_unknown_value(self):
        with pytest.raises(ValueError):
            Settings(_env_file=None, urgency="urgent")

    def test_urgency_accepts_background(self):
        s = Settings(_env_file=None, urgency="background")
        assert s.urgency == "background"


class TestOverrides:
    """Per-document overrides flow through cleanly."""

    def test_set_openai_base_url(self):
        s = Settings(
            _env_file=None,
            openai_base_url="http://litellm:4000/v1",
            openai_api_key="sk-test",
        )
        assert s.openai_base_url == "http://litellm:4000/v1"
        assert s.openai_api_key == "sk-test"

    def test_set_minio_credentials(self):
        s = Settings(
            _env_file=None,
            output_minio_endpoint="http://minio:9000",
            minio_access_key="ak",
            minio_secret_key="sk",
            minio_region="eu-west-1",
        )
        assert s.minio_region == "eu-west-1"
        assert s.minio_access_key == "ak"

    def test_set_embedding_batch_size(self):
        s = Settings(_env_file=None, embedding_batch_size=50)
        assert s.embedding_batch_size == 50
