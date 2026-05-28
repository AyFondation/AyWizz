# =============================================================================
# File: test_data_model_drift.py
# Version: 1
# Path: ay_platform_core/tests/unit/c6_validation/test_data_model_drift.py
# Description: Unit tests for C6 check #8 `data-model-drift` (R-700-027,
#              de-stubbed under D-017). Compares a Pydantic model's declared
#              fields against an `E-*` `fields:`/`model_name:` declaration ;
#              opt-in (no declaration → no finding).
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c6_validation.domains.code import checks
from ay_platform_core.c6_validation.models import (
    CheckContext,
    CodeArtifact,
    Severity,
)

pytestmark = pytest.mark.unit

_RUN = "run-dmd-1"
_FOO_SRC = (
    "from pydantic import BaseModel\n\n\n"
    "class Foo(BaseModel):\n"
    "    a: int\n"
    "    b: str\n"
)


def _ctx(*, fields: list[str] | None, model_name: str | None,
         artifacts: list[CodeArtifact]) -> CheckContext:
    row: dict[str, object] = {"entity_id": "E-900-001", "status": "approved"}
    if fields is not None:
        row["fields"] = fields
    if model_name is not None:
        row["model_name"] = model_name
    return CheckContext(
        project_id="demo", domain="code", requirements=[row], artifacts=artifacts,
    )


_ART = [CodeArtifact(path="src/foo.py", content=_FOO_SRC)]


def test_exact_match_no_finding() -> None:
    out = checks.check_data_model_drift(
        _RUN, _ctx(fields=["a", "b"], model_name="Foo", artifacts=_ART)
    )
    assert out == []


def test_missing_field_is_blocking() -> None:
    out = checks.check_data_model_drift(
        _RUN, _ctx(fields=["a", "b", "c"], model_name="Foo", artifacts=_ART)
    )
    assert len(out) == 1
    assert out[0].severity is Severity.BLOCKING
    assert "missing" in out[0].message and "c" in out[0].message
    assert out[0].entity_id == "E-900-001"


def test_extra_field_is_blocking() -> None:
    out = checks.check_data_model_drift(
        _RUN, _ctx(fields=["a"], model_name="Foo", artifacts=_ART)
    )
    assert len(out) == 1
    assert "extra" in out[0].message and "b" in out[0].message


def test_model_not_found_is_blocking() -> None:
    out = checks.check_data_model_drift(
        _RUN, _ctx(fields=["a"], model_name="Missing", artifacts=_ART)
    )
    assert len(out) == 1
    assert "not found" in out[0].message


def test_no_declaration_is_skipped() -> None:
    # Opt-in : an entity without fields/model_name never false-positives.
    assert checks.check_data_model_drift(
        _RUN, _ctx(fields=None, model_name=None, artifacts=_ART)
    ) == []
    assert checks.check_data_model_drift(
        _RUN, _ctx(fields=["a"], model_name=None, artifacts=_ART)
    ) == []
