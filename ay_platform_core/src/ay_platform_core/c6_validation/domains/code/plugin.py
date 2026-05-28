# =============================================================================
# File: plugin.py
# Version: 3
# Path: ay_platform_core/src/ay_platform_core/c6_validation/domains/code/plugin.py
# Description: Built-in `code` domain plugin. Registers itself at import time
#              (R-700-002). Declares metadata via ``describe()`` and
#              dispatches checks to the concrete implementations in
#              ``checks.py``.
#
# @relation implements:R-700-001
# @relation implements:R-700-003
# @relation implements:R-700-014
# =============================================================================

from __future__ import annotations

from contextvars import ContextVar

from ay_platform_core.c6_validation.domains.code import checks as _checks
from ay_platform_core.c6_validation.models import (
    CheckContext,
    CheckResult,
    CheckSpec,
    PluginDescriptor,
    Severity,
)
from ay_platform_core.c6_validation.plugin.registry import register_plugin

# Ambient run_id used by checks to stamp findings. The service sets this
# before each ``plugin.run_check(...)`` call. A ContextVar keeps the
# Protocol signature lean (run_id not part of the plugin API).
_CURRENT_RUN_ID: ContextVar[str] = ContextVar("_CURRENT_RUN_ID", default="")


def set_current_run_id(run_id: str) -> None:
    """Set the ambient run_id used by checks to stamp findings."""
    _CURRENT_RUN_ID.set(run_id)


_CHECK_SPECS: list[CheckSpec] = [
    CheckSpec(
        check_id="req-without-code",
        title="Approved requirement without implementing code",
        severity_default=Severity.BLOCKING,
        description=(
            "R-700-020. Fails when an approved requirement has no "
            "`@relation implements:<id>` marker in any production module."
        ),
    ),
    CheckSpec(
        check_id="code-without-requirement",
        title="Production module without any entity reference",
        severity_default=Severity.BLOCKING,
        description="R-700-021. Every non-test src/ module must reference an entity.",
    ),
    CheckSpec(
        check_id="interface-signature-drift",
        title="Interface signature drift (E-*) [STUB]",
        severity_default=Severity.INFO,
        description="R-700-022. v1 stub pending machine-readable E-* specs.",
    ),
    CheckSpec(
        check_id="test-absent-for-requirement",
        title="Approved requirement without a test reference",
        severity_default=Severity.BLOCKING,
        description="R-700-023. Every approved requirement must be validated by a test.",
    ),
    CheckSpec(
        check_id="orphan-test",
        title="Test file not referencing any requirement",
        severity_default=Severity.BLOCKING,
        description="R-700-024. Test files must carry at least one `@relation` marker.",
    ),
    CheckSpec(
        check_id="obsolete-reference",
        title="`@relation` target points to unknown or deprecated entity",
        severity_default=Severity.BLOCKING,
        description="R-700-025. All marker targets must exist in C5 and not be deprecated.",
    ),
    CheckSpec(
        check_id="version-drift",
        title="Version-pinned marker drifted from entity current version",
        severity_default=Severity.BLOCKING,
        description=(
            "R-700-026. For every `@vN` pin, the referenced entity's current "
            "version (as stored in C5) SHALL equal N. Otherwise blocking."
        ),
    ),
    CheckSpec(
        check_id="data-model-drift",
        title="Pydantic model drift relative to E-* entity",
        severity_default=Severity.BLOCKING,
        description=(
            "R-700-027. Compares a model's fields against an E-* "
            "`fields:`/`model_name:` declaration (opt-in)."
        ),
    ),
    CheckSpec(
        check_id="cross-layer-coherence",
        title="Project tailoring without explicit override",
        severity_default=Severity.BLOCKING,
        description=(
            "R-700-028. Defence-in-depth on top of the C5 write-time guard. "
            "Any project entity with `tailoring-of:` SHALL also carry "
            "`override: true`; otherwise blocking."
        ),
    ),
]


_DESCRIPTOR = PluginDescriptor(
    domain="code",
    name="builtin-code",
    version="1.0.0",
    artifact_formats=["python"],
    checks=list(_CHECK_SPECS),
)


class CodeDomainPlugin:
    """Built-in plugin for the `code` production domain."""

    def describe(self) -> PluginDescriptor:
        # Return a copy so downstream consumers cannot mutate the cached
        # descriptor (defensive — Pydantic v2 models are not frozen here).
        return _DESCRIPTOR.model_copy(deep=True)

    async def run_check(
        self,
        check_id: str,
        context: CheckContext,
    ) -> CheckResult:
        run_id = _CURRENT_RUN_ID.get()
        try:
            findings = _checks.dispatch(check_id, run_id=run_id, context=context)
        except KeyError:
            return CheckResult(
                findings=[],
                error_message=f"Unknown check_id for domain=code: {check_id!r}",
            )
        except Exception as exc:
            # R-700-014: one check failing does not fail the run. The service
            # translates ``error_message`` into a `severity=info` finding.
            return CheckResult(
                findings=[],
                error_message=f"{type(exc).__name__}: {exc}",
            )
        return CheckResult(findings=findings)


# Registration side-effect (R-700-002).
_PLUGIN_INSTANCE = CodeDomainPlugin()
register_plugin(_PLUGIN_INSTANCE)
