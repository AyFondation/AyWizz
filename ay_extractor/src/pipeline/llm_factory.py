# src/pipeline/llm_factory.py — v1
"""LLM factory — creates per-agent LLM clients using config routing.

Resolves provider:model for each agent via the 3-level cascade
(per-component → per-phase → default → fallback) and instantiates
the appropriate adapter.

See spec §17.3 for routing logic, §27 for adapter interfaces.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ayextractor.llm.config import LLMAssignment, resolve_llm

if TYPE_CHECKING:
    from ayextractor.config.settings import Settings
    from ayextractor.llm.base_client import BaseLLMClient

logger = logging.getLogger(__name__)


class LLMFactory:
    """Create and cache LLM clients per agent.

    Clients are cached by (provider, model) key so agents sharing
    the same assignment reuse a single client instance.
    """

    def __init__(
        self, settings: Settings, headers: dict[str, str] | None = None
    ) -> None:
        self._settings = settings
        # Attribution headers (X-Run-Id / X-Source-Id / X-Tenant-Id / …) sent on
        # every LLM call so C8 records the per-source ingestion cost.
        self._headers = headers
        self._clients: dict[str, BaseLLMClient] = {}

    def get_client(self, agent_name: str) -> BaseLLMClient:
        """Get or create LLM client for a specific agent.

        Args:
            agent_name: Agent name for routing resolution.

        Returns:
            BaseLLMClient instance.
        """
        assignment = resolve_llm(agent_name, self._settings)
        cache_key = assignment.key

        if cache_key not in self._clients:
            self._clients[cache_key] = _create_client(
                assignment, self._settings, headers=self._headers
            )
            logger.info(
                "Created LLM client for '%s': %s (source: %s)",
                agent_name,
                cache_key,
                assignment.source,
            )
        else:
            logger.debug(
                "Reusing cached LLM client for '%s': %s",
                agent_name,
                cache_key,
            )

        return self._clients[cache_key]

    def __call__(self, agent_name: str) -> BaseLLMClient:
        """Callable `llm_factory(agent_name) -> client` interface consumed by
        the enrichment orchestrator and the image pipeline."""
        return self.get_client(agent_name)


def _create_client(
    assignment: LLMAssignment,
    settings: Settings,
    headers: dict[str, str] | None = None,
) -> BaseLLMClient:
    """Instantiate the appropriate LLM adapter.

    Uses lazy imports to avoid loading all adapters at startup.
    """
    provider = assignment.provider.lower()

    # D-020 v1 strip: only the OpenAI-compatible adapter survives. All LLM
    # calls route through C8 (LiteLLM Gateway) via `openai_base_url`. Legacy
    # provider strings (`anthropic`, `google`, `ollama`, `openrouter`) are
    # mapped to the same OpenAI adapter — C8's `agent_routes` resolves the
    # actual upstream model. Standalone dev pointing at a provider directly
    # works too: set `OPENAI_BASE_URL=https://api.anthropic.com/v1` etc.
    if provider in {"openai", "anthropic", "google", "ollama", "openrouter"}:
        from ayextractor.llm.adapters.openai_adapter import OpenAIAdapter
        return OpenAIAdapter(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
            model=assignment.model,
            headers=headers,
        )
    raise ValueError(f"Unknown LLM provider: {provider}")
