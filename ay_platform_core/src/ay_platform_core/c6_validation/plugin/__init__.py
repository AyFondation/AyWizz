# =============================================================================
# File: __init__.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c6_validation/plugin/__init__.py
# Description: Plugin sub-package re-exports.
# =============================================================================

from ay_platform_core.c6_validation.plugin.base import (
    PluginAlreadyRegisteredError,
    ValidationPlugin,
)
from ay_platform_core.c6_validation.plugin.registry import (
    PluginRegistry,
    get_registry,
    register_plugin,
)

__all__ = [
    "PluginAlreadyRegisteredError",
    "PluginRegistry",
    "ValidationPlugin",
    "get_registry",
    "register_plugin",
]
