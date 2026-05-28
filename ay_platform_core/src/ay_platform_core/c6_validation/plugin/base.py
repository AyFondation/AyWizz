# =============================================================================
# File: base.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c6_validation/plugin/base.py
# Description: ValidationPlugin Protocol (the contract every domain plugin
#              must implement) plus common error types. Metadata is exposed
#              via ``describe()`` so the on-wire schema lives in exactly one
#              place (PluginDescriptor) — the coherence check forbids
#              parallel structural definitions.
#
# @relation implements:R-700-003
# =============================================================================

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ay_platform_core.c6_validation.models import (
    CheckContext,
    CheckResult,
    PluginDescriptor,
)


class PluginAlreadyRegisteredError(RuntimeError):
    """Raised by the registry when a `(domain, name)` pair is registered twice."""


@runtime_checkable
class ValidationPlugin(Protocol):
    """Minimal contract every domain plugin must honour.

    A plugin exposes its metadata (domain / name / version / checks /
    artifact_formats) via ``describe()``, and executes one check at a time
    via ``run_check(check_id, context) -> CheckResult``.

    Implementations are pure w.r.t. their input: checks SHALL NOT persist
    state themselves. Persistence is handled by ``ValidationService``.
    """

    def describe(self) -> PluginDescriptor:
        """Return the plugin's metadata as a PluginDescriptor."""
        ...

    async def run_check(
        self,
        check_id: str,
        context: CheckContext,
    ) -> CheckResult:
        """Execute a single check and return its findings."""
        ...
