# =============================================================================
# File: test_interface_consistency.py
# Version: 5
# Path: ay_platform_core/tests/coherence/test_interface_consistency.py
# Description: Coherence 2 - code<->code interface consistency.
#              Runs all standalone coherence checks (scripts/checks/) as
#              pytest tests so they are gated by the full test suite. Each
#              test imports the corresponding check() function directly to
#              avoid subprocess overhead and to produce structured failure
#              messages in the pytest report.
# =============================================================================

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make scripts/checks/ importable as a package when running via pytest from
# ay_platform_core/ (the sub-project root).
_CHECKS_DIR = Path(__file__).parent.parent.parent / "scripts" / "checks"
if str(_CHECKS_DIR) not in sys.path:
    sys.path.insert(0, str(_CHECKS_DIR))

from tests.fixtures.contract_registry import get_registry  # noqa: E402, I001


# ---------------------------------------------------------------------------
# Registry-level structural checks (in-process, no scripts needed)
# ---------------------------------------------------------------------------


@pytest.mark.coherence
def test_registry_is_well_formed() -> None:
    """Every registered contract SHALL have a non-empty producer and name.

    Precondition: the registry MUST be non-empty. C2/C3 contracts are
    registered at import time via `tests/fixtures/contract_registry.py`; an
    empty registry here indicates a regression (missing `register_contract`
    call or failed import).
    """
    registry = get_registry()
    assert registry, (
        "contract registry is empty — expected C2/C3 contracts from "
        "tests.fixtures.contract_registry"
    )

    for contract in registry:
        assert contract.producer, f"Empty producer in contract {contract.name}"
        assert contract.name, f"Empty name in contract produced by {contract.producer}"
        assert contract.transport in {"rest", "nats", "python-import"}, (
            f"Invalid transport {contract.transport!r} for {contract.producer}.{contract.name}"
        )


@pytest.mark.coherence
def test_no_duplicate_contract_names_per_producer() -> None:
    """A given (producer, name) pair SHALL be unique in the registry."""
    registry = get_registry()
    assert registry, "contract registry is empty — see test_registry_is_well_formed"

    seen: set[tuple[str, str]] = set()
    for contract in registry:
        key = (contract.producer, contract.name)
        assert key not in seen, f"Duplicate contract: {key}"
        seen.add(key)


@pytest.mark.coherence
def test_consumers_are_non_empty() -> None:
    """Every registered contract SHALL have at least one declared consumer."""
    registry = get_registry()
    assert registry, "contract registry is empty — see test_registry_is_well_formed"

    orphans = [c for c in registry if not c.consumers]
    assert not orphans, (
        f"Contracts with no consumers: {[(c.producer, c.name) for c in orphans]}"
    )


# ---------------------------------------------------------------------------
# Schema-level checks (delegates to scripts/checks/ functions)
# ---------------------------------------------------------------------------


@pytest.mark.coherence
def test_pydantic_schemas_valid() -> None:
    """All registered schemas SHALL be valid Pydantic BaseModels with no bare Any."""
    import check_pydantic_schemas_valid as m  # noqa: PLC0415

    issues = m.check()
    assert not issues, "Pydantic schema validation issues:\n" + "\n".join(issues)


@pytest.mark.coherence
def test_schema_isolation() -> None:
    """Public schemas SHALL NOT expose private fields from their internal counterparts."""
    import check_schema_isolation as m  # noqa: PLC0415

    issues = m.check()
    assert not issues, "Schema isolation violations:\n" + "\n".join(issues)


# ---------------------------------------------------------------------------
# Source-level AST checks (delegates to scripts/checks/ functions)
# ---------------------------------------------------------------------------


@pytest.mark.coherence
def test_router_typing() -> None:
    """All FastAPI route response_models SHALL be typed Pydantic BaseModels."""
    import check_router_typing as m  # noqa: PLC0415

    issues = m.check()
    assert not issues, "Router typing violations:\n" + "\n".join(issues)


@pytest.mark.coherence
def test_no_parallel_definitions() -> None:
    """No class in src/ SHALL shadow a registered contract name or share >= 3 fields."""
    import check_no_parallel_definitions as m  # noqa: PLC0415

    issues = m.check()
    assert not issues, "Parallel contract definitions:\n" + "\n".join(issues)


@pytest.mark.coherence
def test_canonical_imports() -> None:
    """All absolute imports of registered contract types SHALL use the canonical module."""
    import check_canonical_imports as m  # noqa: PLC0415

    issues = m.check()
    assert not issues, "Non-canonical contract imports:\n" + "\n".join(issues)
