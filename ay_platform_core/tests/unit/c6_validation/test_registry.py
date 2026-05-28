# =============================================================================
# File: test_registry.py
# Version: 1
# Path: ay_platform_core/tests/unit/c6_validation/test_registry.py
# Description: Unit tests for the plugin registry (R-700-001 / R-700-002):
#              registration idempotency, duplicate rejection, domain listing,
#              descriptor projection.
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c6_validation.models import (
    CheckContext,
    CheckResult,
    CheckSpec,
    PluginDescriptor,
    Severity,
)
from ay_platform_core.c6_validation.plugin.base import PluginAlreadyRegisteredError
from ay_platform_core.c6_validation.plugin.registry import PluginRegistry


class _FakePlugin:
    def __init__(self, domain: str, name: str) -> None:
        self._descriptor = PluginDescriptor(
            domain=domain,
            name=name,
            version="0.1.0",
            artifact_formats=["python"],
            checks=[
                CheckSpec(
                    check_id=f"{name}-demo",
                    title="demo",
                    severity_default=Severity.INFO,
                    description="x",
                )
            ],
        )

    def describe(self) -> PluginDescriptor:
        return self._descriptor

    async def run_check(  # pragma: no cover - unused in registry tests
        self, check_id: str, context: CheckContext
    ) -> CheckResult:
        raise NotImplementedError


@pytest.mark.unit
class TestPluginRegistry:
    def test_empty_registry_listings(self) -> None:
        reg = PluginRegistry()
        assert reg.domains() == []
        assert reg.all_plugins() == []
        assert reg.describe_all() == []

    def test_register_and_describe(self) -> None:
        reg = PluginRegistry()
        p = _FakePlugin("code", "example")
        reg.register(p)
        descs = reg.describe_all()
        assert len(descs) == 1
        assert descs[0].domain == "code"
        assert descs[0].name == "example"
        assert descs[0].checks[0].check_id == "example-demo"

    def test_duplicate_pair_raises(self) -> None:
        reg = PluginRegistry()
        p1 = _FakePlugin("code", "example")
        p2 = _FakePlugin("code", "example")
        reg.register(p1)
        with pytest.raises(PluginAlreadyRegisteredError):
            reg.register(p2)

    def test_re_register_same_instance_is_idempotent(self) -> None:
        reg = PluginRegistry()
        p = _FakePlugin("code", "example")
        reg.register(p)
        # Simulates a re-import triggering the side effect a second time.
        reg.register(p)
        assert len(reg.all_plugins()) == 1

    def test_plugins_per_domain(self) -> None:
        reg = PluginRegistry()
        reg.register(_FakePlugin("code", "one"))
        reg.register(_FakePlugin("code", "two"))
        reg.register(_FakePlugin("documentation", "three"))
        code_names = {p.describe().name for p in reg.plugins_for_domain("code")}
        assert code_names == {"one", "two"}
        doc_names = {p.describe().name for p in reg.plugins_for_domain("documentation")}
        assert doc_names == {"three"}
        assert reg.domains() == ["code", "documentation"]

    def test_clear(self) -> None:
        reg = PluginRegistry()
        reg.register(_FakePlugin("code", "one"))
        reg.clear()
        assert reg.all_plugins() == []
