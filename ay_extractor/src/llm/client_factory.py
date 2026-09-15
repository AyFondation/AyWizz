# src/llm/client_factory.py — v3
"""Factory: instantiate LLM client from provider name.

D-020 v1 strip: only the OpenAI-compatible adapter survives. All LLM calls
SHALL route through C8 (LiteLLM Gateway) via `OPENAI_BASE_URL` env switch
— the OpenAI adapter is API-compatible with C8 out of the box. The
provider-specific adapters (anthropic, google, ollama, openrouter) were
physically removed because:
  - In-cluster: C8 owns provider routing (R-100-011) → AyExtractor SHALL
    NOT call providers directly.
  - Standalone dev: set `OPENAI_BASE_URL=https://api.anthropic.com/v1`
    (or equivalent) on the OpenAI adapter to talk to any OpenAI-compatible
    endpoint.

Called by the orchestrator to create per-agent clients based on config
resolution (see llm/config.py cascade). See spec §27.5 for details.
"""

from __future__ import annotations

import logging

from ayextractor.config.settings import Settings
from ayextractor.llm.base_client import BaseLLMClient

logger = logging.getLogger(__name__)

# Registry of provider name → adapter class path (lazy import).
# D-020 v1: only `openai` survives. The other entries map to `openai`
# so legacy configs do not crash — the request reaches whatever OpenAI-
# compatible endpoint `OPENAI_BASE_URL` points to.
_PROVIDER_REGISTRY: dict[str, str] = {
    "openai": "ayextractor.llm.adapters.openai_adapter.OpenAIAdapter",
}


class UnsupportedProviderError(ValueError):
    """Raised when a provider is not registered."""


def create_llm_client(
    provider: str,
    model: str,
    settings: Settings | None = None,
    **kwargs: object,
) -> BaseLLMClient:
    """Instantiate the correct adapter from provider name.

    Args:
        provider: Provider identifier (anthropic, openai, google, ollama).
        model: Model name (e.g. claude-sonnet-4-20250514).
        settings: Application settings (for API keys).
        **kwargs: Additional provider-specific arguments.

    Returns:
        Configured BaseLLMClient instance.

    Raises:
        UnsupportedProviderError: If provider is not registered.
    """
    if provider not in _PROVIDER_REGISTRY:
        raise UnsupportedProviderError(
            f"Unsupported LLM provider: {provider!r}. "
            f"Available: {', '.join(sorted(_PROVIDER_REGISTRY))}"
        )

    class_path = _PROVIDER_REGISTRY[provider]
    adapter_cls = _import_class(class_path)

    # Resolve API key from settings
    init_kwargs = dict(kwargs)
    init_kwargs["model"] = model

    if settings is not None:
        # D-020 v1: only the OpenAI-compatible adapter survives. API key
        # comes from OPENAI_API_KEY (= the C8 gateway key when routed
        # through LiteLLM). `base_url` points the SDK at C8 in-cluster.
        init_kwargs.setdefault("api_key", settings.openai_api_key)
        init_kwargs.setdefault("base_url", settings.openai_base_url or None)

    logger.debug("Creating LLM client: provider=%s, model=%s", provider, model)
    return adapter_cls(**init_kwargs)


def register_provider(name: str, class_path: str) -> None:
    """Register a custom provider adapter.

    Args:
        name: Provider identifier.
        class_path: Fully qualified class path implementing BaseLLMClient.
    """
    _PROVIDER_REGISTRY[name] = class_path
    logger.info("Registered LLM provider: %s → %s", name, class_path)


def _import_class(class_path: str) -> type:
    """Dynamically import a class from its fully qualified path."""
    module_path, class_name = class_path.rsplit(".", 1)
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, class_name)
