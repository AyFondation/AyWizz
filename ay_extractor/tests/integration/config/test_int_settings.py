# tests/integration/config/test_int_settings.py — v1
"""Integration tests for configuration loading.

Tests Settings with real .env files, validation rules, and cross-field consistency.
No external services required.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ayextractor.config.settings import ConfigurationError, Settings


class TestSettingsLoading:

    def test_defaults(self):
        # D-020: the default egress provider is `openai` (the C8 LiteLLM proxy
        # speaks the OpenAI protocol), not `anthropic`.
        settings = Settings(_env_file=None)
        assert settings.llm_default_provider == "openai"
        assert settings.chunking_strategy == "structural"
        assert settings.cache_enabled is True

    def test_from_env_file(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "LLM_DEFAULT_PROVIDER=ollama\n"
            "LLM_DEFAULT_MODEL=qwen2.5:1.5b\n"
            "OLLAMA_BASE_URL=http://localhost:11434\n"
            "EMBEDDING_PROVIDER=ollama\n"
            "EMBEDDING_OLLAMA_MODEL=nomic-embed-text\n"
            "VECTOR_DB_TYPE=qdrant\n"
            "VECTOR_DB_URL=http://localhost:6333\n"
            "GRAPH_DB_TYPE=arangodb\n"
            "GRAPH_DB_URI=http://localhost:8529\n"
            "GRAPH_DB_USER=root\n"
            "GRAPH_DB_PASSWORD=testpwd\n"
        )
        settings = Settings(_env_file=str(env_file))
        assert settings.llm_default_provider == "ollama"
        assert settings.vector_db_type == "qdrant"
        assert settings.graph_db_type == "arangodb"

    def test_oversize_strategy_options(self):
        for strategy in ["reject", "truncate", "sample"]:
            s = Settings(_env_file=None, oversize_strategy=strategy)
            assert s.oversize_strategy == strategy

    def test_chunking_strategies(self):
        for strat in ["structural", "semantic"]:
            s = Settings(_env_file=None, chunking_strategy=strat)
            assert s.chunking_strategy == strat


class TestSettingsValidation:

    def test_chunk_overlap_must_be_below_target(self):
        """V-05 (the surviving cross-field rule after the D-020 strip):
        chunk_overlap >= chunk_target_size is rejected. The legacy V-03
        `rag_enabled requires a DB` rule was removed with the rag_* settings —
        C7 owns RAG now."""
        with pytest.raises(ConfigurationError):
            Settings(_env_file=None, chunk_target_size=100, chunk_overlap=100)
