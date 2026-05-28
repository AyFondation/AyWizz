# =============================================================================
# File: registry.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c6_validation/plugin/registry.py
# Description: Process-global plugin registry. Plugins register themselves by
#              calling `register_plugin(plugin)` at import time (R-700-002).
#              Registration is idempotent per (domain, name) pair: a second
#              call with the SAME instance is a no-op, but a different
#              instance raises PluginAlreadyRegisteredError.
#
#              The registry indexes plugins via their ``describe()`` metadata
#              so there is only one source of truth for the plugin schema.
#
# @relation implements:R-700-001
# @relation implements:R-700-002
# =============================================================================

from __future__ import annotations

from ay_platform_core.c6_validation.models import PluginDescriptor
from ay_platform_core.c6_validation.plugin.base import (
    PluginAlreadyRegisteredError,
    ValidationPlugin,
)


class PluginRegistry:
    """In-memory registry indexed by (domain, name)."""

    def __init__(self) -> None:
        self._plugins: dict[tuple[str, str], ValidationPlugin] = {}

    def _key(self, plugin: ValidationPlugin) -> tuple[str, str]:
        desc = plugin.describe()
        return (desc.domain, desc.name)

    def register(self, plugin: ValidationPlugin) -> None:
        key = self._key(plugin)
        existing = self._plugins.get(key)
        if existing is plugin:
            # Idempotent: re-import of the module re-runs the side effect;
            # that must not raise.
            return
        if existing is not None:
            raise PluginAlreadyRegisteredError(
                f"Plugin already registered for domain={key[0]!r}, name={key[1]!r}"
            )
        self._plugins[key] = plugin

    def clear(self) -> None:
        """Test-only utility."""
        self._plugins.clear()

    def domains(self) -> list[str]:
        """Distinct registered domains, sorted."""
        return sorted({d for (d, _) in self._plugins})

    def plugins_for_domain(self, domain: str) -> list[ValidationPlugin]:
        """All plugins registered under a given domain."""
        return [p for (d, _), p in self._plugins.items() if d == domain]

    def all_plugins(self) -> list[ValidationPlugin]:
        """All registered plugins, in registration order."""
        return list(self._plugins.values())

    def describe_all(self) -> list[PluginDescriptor]:
        """Projection of the registry as a list of descriptors for REST exposure."""
        return [p.describe() for p in self._plugins.values()]


_REGISTRY = PluginRegistry()


def register_plugin(plugin: ValidationPlugin) -> None:
    """Register a plugin with the process-global registry."""
    _REGISTRY.register(plugin)


def get_registry() -> PluginRegistry:
    """Return the process-global registry."""
    return _REGISTRY
