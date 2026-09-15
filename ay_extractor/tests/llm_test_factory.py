# tests/llm_test_factory.py — v2
"""Test LLM factory — creates adapter from .env configuration.

Changelog:
    v2: Use dotenv_values() instead of load_dotenv() to prevent os.environ
        pollution during pytest collection (which breaks Settings tests).
    v1: Initial factory with module-level load_dotenv().

CRITICAL: Never use load_dotenv() in test infrastructure code.
    pydantic-settings reads os.environ ALWAYS, even with _env_file=None.
    Any load_dotenv() call in any imported module pollutes the ENTIRE
    pytest session, breaking tests that assert on Settings defaults.
    Use dotenv_values() instead — reads .env into a dict without
    touching os.environ.

Env vars (all have backward-compatible defaults):
    TEST_LLM_PROVIDER      default: ollama
    TEST_LLM_MODEL         default: qwen2.5:0.5b
    TEST_EMBED_PROVIDER    default: ollama
    TEST_EMBED_MODEL       default: nomic-embed-text
    TEST_EMBED_DIMENSIONS  default: 768
"""

from __future__ import annotations

import os

from dotenv import dotenv_values

# Read .env into a dict WITHOUT modifying os.environ
_dotenv = dotenv_values()


def _env(key: str, default: str = "") -> str:
    """Read from os.environ first, then .env file, then default."""
    return os.environ.get(key, "") or _dotenv.get(key, "") or default


# ── Configuration (lazy, from _env helper) ──────────────────────

def get_test_llm_provider() -> str:
    return _env("TEST_LLM_PROVIDER", "ollama").lower()

def get_test_llm_model() -> str:
    return _env("TEST_LLM_MODEL", "qwen2.5:0.5b")

def get_test_embed_provider() -> str:
    return _env("TEST_EMBED_PROVIDER", "ollama").lower()

def get_test_embed_model() -> str:
    return _env("TEST_EMBED_MODEL", "nomic-embed-text")

def get_test_embed_dimensions() -> int:
    return int(_env("TEST_EMBED_DIMENSIONS", "768"))

def provider_needs_docker() -> bool:
    return get_test_llm_provider() == "ollama"

def embedder_needs_docker() -> bool:
    return get_test_embed_provider() == "ollama"


# ── LLM Factory ─────────────────────────────────────────────────

def create_test_llm(**overrides):
    """Create a real LLM adapter from .env configuration.

    D-020 v1 strip: only the OpenAI-compatible adapter survives. Legacy
    `TEST_LLM_PROVIDER` values (`anthropic`, `google`, `ollama`,
    `openrouter`) are mapped to the same adapter — the upstream is
    selected via `OPENAI_BASE_URL` (e.g. `http://localhost:11434/v1` for
    Ollama, `https://api.anthropic.com/v1` for Anthropic native, …).

    Args:
        **overrides: Override any default (model, api_key, base_url).

    Returns:
        BaseLLMClient instance ready for API calls.
    """
    provider = overrides.pop("provider", get_test_llm_provider()).lower()
    model = overrides.pop("model", get_test_llm_model())

    # All legacy provider strings route to the OpenAI adapter — the actual
    # endpoint is selected via base_url.
    if provider in {"openai", "anthropic", "google", "ollama", "openrouter"}:
        from ayextractor.llm.adapters.openai_adapter import OpenAIAdapter

        base_url_default = {
            "ollama": _env("OLLAMA_BASE_URL", "http://localhost:11434") + "/v1",
            "openrouter": _env("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        }.get(provider, _env("OPENAI_BASE_URL", ""))
        api_key_default = {
            "openai": _env("OPENAI_API_KEY"),
            "anthropic": _env("ANTHROPIC_API_KEY"),
            "google": _env("GOOGLE_API_KEY"),
            "openrouter": _env("OPENROUTER_API_KEY"),
        }.get(provider, "")

        return OpenAIAdapter(
            model=model,
            api_key=overrides.pop("api_key", api_key_default),
            base_url=overrides.pop("base_url", base_url_default) or None,
            **overrides,
        )

    raise ValueError(
        f"Unknown TEST_LLM_PROVIDER: {provider!r}. "
        f"D-020 v1 supports OpenAI-compatible only (route any provider via base_url)."
    )


# ── Embedder Factory ────────────────────────────────────────────

def create_test_embedder(**overrides):
    """Create a real embeddings client from .env configuration.

    D-020 v2 §B1: embeddings are now produced by C13's `EmbeddingsClient`
    (OpenAI-compatible `/embeddings`). The legacy per-provider embedders
    (`OllamaEmbedder`, `SentenceTransformersEmbedder`, …) were stripped
    alongside the rest of `rag/embeddings/` in session 2.
    """
    from ayextractor.llm.embeddings_client import EmbeddingsClient

    model = overrides.pop("model", get_test_embed_model())
    base_url = overrides.pop("base_url", None)
    if base_url is None:
        provider = overrides.pop("provider", get_test_embed_provider()).lower()
        base_url = {
            "ollama": _env("OLLAMA_BASE_URL", "http://localhost:11434") + "/v1",
        }.get(provider, _env("OPENAI_BASE_URL", "")) or None
    api_key = overrides.pop("api_key", _env("OPENAI_API_KEY"))

    return EmbeddingsClient(model=model, api_key=api_key, base_url=base_url, **overrides)


# ── Info helper ─────────────────────────────────────────────────

def test_config_info() -> str:
    """Human-readable config string for test output headers."""
    return (
        f"LLM={get_test_llm_provider()}:{get_test_llm_model()} "
        f"Embed={get_test_embed_provider()}:{get_test_embed_model()} "
        f"docker_required={provider_needs_docker() or embedder_needs_docker()}"
    )