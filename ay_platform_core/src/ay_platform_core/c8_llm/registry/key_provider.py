# =============================================================================
# File: key_provider.py
# Version: 3
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/key_provider.py
# Description: Per-request call-target resolver wired into the C8 gateway client.
#              Given the alias the platform routes to, it resolves model →
#              provider and returns a `CallTarget`: the rewritten litellm model
#              string `<provider.wire_format>/<upstream_model>`, the provider's
#              MANDATORY `api_base`, and the decrypted provider key. The client
#              rewrites the request body with these — so routing depends on the
#              provider's explicit endpoint, never a built-in default, and the
#              alias is free to change (model_id is the stable reference).
#
#              GRACEFUL: an unknown alias (e.g. the test `_mock_llm` model) or a
#              dangling provider returns None → the client leaves the body as-is
#              (the proxy/mock handles it). The key is NEVER logged.
#
# @relation implements:R-800-148
# =============================================================================

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, NamedTuple

from ay_platform_core.c8_llm.models import RoutingDecision
from ay_platform_core.c8_llm.registry.catalog_repository import TenantCatalogRepository
from ay_platform_core.c8_llm.registry.catalog_service import TenantCatalogService
from ay_platform_core.c8_llm.registry.models import ModelQuality
from ay_platform_core.c8_llm.registry.provider_repository import (
    LLMProviderRepository,
    ProviderStore,
)
from ay_platform_core.c8_llm.registry.repository import (
    LLMRegistryRepository,
    RegistryStore,
)
from ay_platform_core.c8_llm.registry.service import LLMRegistryService
from ay_platform_core.crypto.secret_cipher import SecretCipher, SecretCipherError

_log = logging.getLogger("c8_llm.key_provider")


class CallTarget(NamedTuple):
    """Per-call upstream override resolved from the registry + provider.

    `model` is the rewritten litellm model id (`wire_format/upstream_model`);
    `api_base` is the provider's mandatory endpoint; `api_key` is the decrypted
    provider key, or None when no key is stored / no master key is configured."""

    model: str
    api_base: str
    api_key: str | None


def _provider_aad(provider_id: str) -> str:
    return f"llm_provider:{provider_id}:api_key"


class RegistryKeyProvider:
    """Async callable `(alias) -> CallTarget | None` over the model + provider
    registries. Best-effort by construction."""

    def __init__(
        self,
        registry_repo: RegistryStore,
        provider_repo: ProviderStore,
        cipher: SecretCipher | None,
    ) -> None:
        self._registry = registry_repo
        self._providers = provider_repo
        self._cipher = cipher

    async def __call__(self, alias: str) -> CallTarget | None:
        try:
            model_doc = await self._registry.get_by_alias(alias)
            if model_doc is None:
                return None  # unknown alias (e.g. mock model) → no override
            provider_id = model_doc.get("provider_id")
            prov = await self._providers.get(provider_id) if provider_id else None
            if prov is None:
                _log.warning(
                    "model %r references missing provider %r — no override",
                    alias, provider_id,
                )
                return None
            api_key: str | None = None
            ciphertext = prov.get("api_key_ciphertext")
            if ciphertext is not None and self._cipher is not None:
                api_key = self._cipher.decrypt(
                    ciphertext, aad=_provider_aad(str(prov["_key"]))
                )
            model = f"{prov['wire_format']}/{model_doc['upstream_model']}"
            return CallTarget(model=model, api_base=prov["base_url"], api_key=api_key)
        except SecretCipherError as exc:
            _log.warning("call-target resolution failed for alias %s: %s", alias, exc)
            return None


def build_registry_key_provider(db: Any) -> RegistryKeyProvider:
    """Build a call-target resolver over the shared Arango `db`. ALWAYS returns
    a provider: with a master key it injects the decrypted provider key; without
    one it still rewrites the model + injects api_base (not secrets), the key
    falling back to the proxy env key. Wired in each LLM-calling component's app
    factory (option B: C3/C4/C7 hold the master key)."""
    try:
        cipher: SecretCipher | None = SecretCipher.from_env()
    except SecretCipherError:
        _log.warning(
            "AY_SECRET_MASTER_KEY absent — provider api_key injection disabled "
            "(model rewrite + api_base still applied; proxy env key as fallback)"
        )
        cipher = None
    return RegistryKeyProvider(
        LLMRegistryRepository(db), LLMProviderRepository(db), cipher
    )


# ---------------------------------------------------------------------------
# Provider-independent MODEL resolution (D-011)
# ---------------------------------------------------------------------------

# Agent → preferred `model_quality` order. The platform routes an agent to a
# QUALITY TIER (never a model/provider name); the operator's registry catalogue
# then supplies the concrete project model of that quality. Each tuple is a
# FALLBACK chain, so any enabled model the project has is usable.
_HIGH_FIRST = (ModelQuality.HIGH, ModelQuality.MEDIUM, ModelQuality.LOW)
_MEDIUM_FIRST = (ModelQuality.MEDIUM, ModelQuality.HIGH, ModelQuality.LOW)
_LOW_FIRST = (ModelQuality.LOW, ModelQuality.MEDIUM, ModelQuality.HIGH)
_AGENT_QUALITY_ORDER: dict[str, tuple[ModelQuality, ...]] = {
    "architect": _HIGH_FIRST,
    "c6-judge": _HIGH_FIRST,
    "sub-agent": _LOW_FIRST,
    "c7-kg-extractor": _LOW_FIRST,
    "c7-contextualizer": _LOW_FIRST,
    "summarizer": _LOW_FIRST,
    "decontextualizer": _LOW_FIRST,
    "densifier": _LOW_FIRST,
    "image_analyzer": _LOW_FIRST,
}

# Agent → the BENCHMARKS its work is judged on, and their relative weight.
#
# This is the other half of "route on quality per euro". The quality TIER
# above says how good a model must be; this says good AT WHAT. They answer
# different questions and neither substitutes for the other: a model can sit
# in the `high` tier and be the wrong buy for extraction work.
#
# WHY THE MAP LIVES HERE. The agent name is the platform's one honest signal
# of intent — it is what the orchestrator already declares, and the only
# place that knows both the work and its name. C8 must not learn about
# pipeline phases, and C4 must not learn how models are ranked; this table is
# the seam that keeps both true, exactly as `_AGENT_QUALITY_ORDER` does.
#
# EMPTY IS A VALID AND HONEST ENTRY. An agent with no profile resolves on
# price among everything that already clears its tier and capability gates —
# which is right when nobody has established what "good" means for that
# work. Inventing a profile so the feature looks used would route real
# spending on a guess.
_AGENT_BENCHMARKS: dict[str, dict[str, float]] = {
    # Design and code-shaped reasoning: the coding leaderboards are the ones
    # that track it, weighted toward the agentic one since these agents work
    # in a tool loop rather than emitting a single snippet.
    "architect": {"swe-bench": 0.7, "gpqa": 0.3},
    "sub-agent": {"swe-bench": 1.0},
    # Judging a diff is a reasoning task about code, not a coding task.
    "c6-judge": {"gpqa": 0.6, "swe-bench": 0.4},
    # Ingestion work is language work: faithfulness to a source text, not
    # problem solving. Scored on whatever the operator uses to measure that.
    "c7-kg-extractor": {"doc-synthesis": 1.0},
    "c7-contextualizer": {"doc-synthesis": 1.0},
    "summarizer": {"doc-synthesis": 1.0},
    "decontextualizer": {"doc-synthesis": 1.0},
    "densifier": {"doc-synthesis": 1.0},
    # Deliberately absent: `image_analyzer`. Vision quality is gated by the
    # `vision` CAPABILITY, and no benchmark in this platform's registry
    # measures how WELL a model reads an image. A profile here would be a
    # number with nothing behind it.
}

# `(agent_name, tenant_id, project_id, *, require_tool_calling) -> alias | None`
ModelResolver = Callable[..., Awaitable[str | None]]


async def _resolve_model_via_catalog(
    catalog: Any,
    agent_name: str,
    tenant_id: str | None,
    project_id: str | None,
    *,
    require_tool_calling: bool,
) -> str | None:
    """Core resolution (extracted for testing without a DB): try the agent's
    quality tiers in order, returning the first project model that resolves."""
    if not tenant_id:
        return None
    for quality in _AGENT_QUALITY_ORDER.get(agent_name, _MEDIUM_FIRST):
        try:
            resolved = await catalog.resolve(
                tenant_id,
                quality,
                project_id=project_id,
                require_tool_calling=require_tool_calling,
                # What this agent's work is judged on. Empty for an agent
                # with no established profile, which resolves on price.
                benchmarks=_AGENT_BENCHMARKS.get(agent_name, {}),
            )
        except Exception as exc:  # best-effort, never break a call
            _log.warning("model resolution failed for %s: %s", agent_name, exc)
            return None
        if resolved is not None:
            return str(resolved.model_alias)
    return None


async def _resolve_with_decision(
    catalog: Any,
    agent_name: str,
    tenant_id: str | None,
    project_id: str | None,
    *,
    require_tool_calling: bool,
) -> tuple[str | None, RoutingDecision]:
    """As `_resolve_model_via_catalog`, plus WHY — for the call ledger.

    Kept as a second function rather than widening the first: the alias-only
    form is what several callers and a good deal of test surface already
    depend on, and the decision is additive. The two share the loop below
    only in shape, which is deliberate — a resolver that returned a tuple
    everywhere would push the decision through code paths that have no use
    for it and no way to store it.

    A failure yields `(None, empty decision)`, never a decision claiming
    price decided. Nothing was decided: nothing resolved.
    """
    profile = _AGENT_BENCHMARKS.get(agent_name, {})
    if not tenant_id:
        return None, RoutingDecision()
    for quality in _AGENT_QUALITY_ORDER.get(agent_name, _MEDIUM_FIRST):
        try:
            resolved = await catalog.resolve(
                tenant_id,
                quality,
                project_id=project_id,
                require_tool_calling=require_tool_calling,
                benchmarks=profile,
            )
        except Exception as exc:  # best-effort, never break a call
            _log.warning("model resolution failed for %s: %s", agent_name, exc)
            return None, RoutingDecision()
        if resolved is not None:
            return str(resolved.model_alias), RoutingDecision(
                quality_requested=str(quality),
                # The tier actually obtained, which may be higher than the
                # one this iteration asked for.
                quality_resolved=str(resolved.model_quality),
                benchmarks=profile,
                composite_score=resolved.composite_score,
                effective_cost=resolved.effective_cost,
                decided_by=(
                    "benchmark-ratio"
                    if resolved.decided_by_benchmark
                    else "price"
                ),
            )
    return None, RoutingDecision()


def build_registry_model_resolver(db: Any) -> ModelResolver:
    """Resolve an agent's call to a CONCRETE model alias from the operator's
    registry catalogue, scoped to the project (provider-INDEPENDENT, D-011 — no
    model/provider name in config). Maps the agent to a quality tier, then
    returns the project's cheapest enabled model of that quality, falling back
    across qualities so ANY registered project model works. None → no model is
    configured for the project yet (caller leaves the model unset). Best-effort:
    a registry/DB error yields None and NEVER breaks the LLM call."""
    catalog = TenantCatalogService(
        TenantCatalogRepository(db), LLMRegistryService(LLMRegistryRepository(db))
    )

    async def resolve(
        agent_name: str,
        tenant_id: str | None,
        project_id: str | None,
        *,
        require_tool_calling: bool = False,
    ) -> str | None:
        return await _resolve_model_via_catalog(
            catalog,
            agent_name,
            tenant_id,
            project_id,
            require_tool_calling=require_tool_calling,
        )

    return resolve


# `(agent, tenant, project, *, require_tool_calling) -> (alias, decision)`
DecidingModelResolver = Callable[..., Awaitable[tuple[str | None, RoutingDecision]]]


def build_deciding_model_resolver(db: Any) -> DecidingModelResolver:
    """As `build_registry_model_resolver`, but also returns WHY.

    A SECOND BUILDER RATHER THAN A WIDER CONTRACT. The alias-only resolver
    has two consumers with different needs: the LLM client, which can
    attach the reason to the call it is about to make, and the generate
    engine, which only needs a name. Widening the shared return type would
    push a tuple through a caller that has nowhere to put the second half,
    and every test that stubs it. Both builders wrap the same catalogue, so
    the choice costs nothing but clarity at the wiring site.
    """
    catalog = TenantCatalogService(
        TenantCatalogRepository(db), LLMRegistryService(LLMRegistryRepository(db))
    )

    async def resolve(
        agent_name: str,
        tenant_id: str | None,
        project_id: str | None,
        *,
        require_tool_calling: bool = False,
    ) -> tuple[str | None, RoutingDecision]:
        return await _resolve_with_decision(
            catalog,
            agent_name,
            tenant_id,
            project_id,
            require_tool_calling=require_tool_calling,
        )

    return resolve
