# src/llm/config.py — v1
"""Per-component LLM routing with 3-level cascade resolution.

Resolution order (spec §17.3):
  1. Per-component env var (LLM_SUMMARIZER=openai:gpt-4o)
  2. Per-phase env var (LLM_PHASE_ANALYSIS=anthropic:claude-sonnet-4-20250514)
  3. Default provider + model (LLM_DEFAULT_PROVIDER + LLM_DEFAULT_MODEL)
  4. Hardcoded fallback (anthropic:claude-sonnet-4-20250514)
"""

from __future__ import annotations

from dataclasses import dataclass

from ayextractor.config.agents import PHASE_COMPONENT_MAP
from ayextractor.config.settings import Settings

_FALLBACK_PROVIDER = "anthropic"
_FALLBACK_MODEL = "claude-sonnet-4-20250514"


@dataclass(frozen=True)
class LLMAssignment:
    """Resolved LLM provider:model for a component."""

    provider: str
    model: str
    source: str  # "component", "phase", "default", or "fallback"

    @property
    def key(self) -> str:
        """Return 'provider:model' string."""
        return f"{self.provider}:{self.model}"


def _find_phase(component: str) -> str | None:
    """Find which phase a component belongs to."""
    for phase, components in PHASE_COMPONENT_MAP.items():
        if component in components:
            return phase
    return None


def _parse_assignment(value: str) -> tuple[str, str] | None:
    """Parse 'provider:model' string. Returns None if empty."""
    if not value or ":" not in value:
        return None
    provider, model = value.split(":", 1)
    return (provider.strip(), model.strip())


def resolve_llm(component: str, settings: Settings) -> LLMAssignment:
    """Resolve LLM assignment for a component using 3-level cascade.

    Args:
        component: Component name (e.g. "summarizer", "entity_normalizer").
        settings: Application settings.

    Returns:
        Resolved LLMAssignment with provider, model, and resolution source.
    """
    # Level 1: Per-component override
    attr = f"llm_{component}"
    per_component = getattr(settings, attr, "")
    parsed = _parse_assignment(per_component)
    if parsed:
        return LLMAssignment(provider=parsed[0], model=parsed[1], source="component")

    # Level 2: Per-phase override
    phase = _find_phase(component)
    if phase:
        phase_attr = f"llm_phase_{phase}"
        per_phase = getattr(settings, phase_attr, "")
        parsed = _parse_assignment(per_phase)
        if parsed:
            return LLMAssignment(provider=parsed[0], model=parsed[1], source="phase")

    # Level 3: Default
    if settings.llm_default_provider and settings.llm_default_model:
        return LLMAssignment(
            provider=settings.llm_default_provider,
            model=settings.llm_default_model,
            source="default",
        )

    # Level 4: Hardcoded fallback
    return LLMAssignment(
        provider=_FALLBACK_PROVIDER,
        model=_FALLBACK_MODEL,
        source="fallback",
    )


def resolve_all(settings: Settings) -> dict[str, LLMAssignment]:
    """Resolve LLM assignments for all known components.

    Returns:
        Dict mapping component name to resolved LLMAssignment.
    """
    all_components: set[str] = set()
    for components in PHASE_COMPONENT_MAP.values():
        all_components.update(components)

    return {comp: resolve_llm(comp, settings) for comp in sorted(all_components)}
