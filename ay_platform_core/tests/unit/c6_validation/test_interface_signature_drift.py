# =============================================================================
# File: test_interface_signature_drift.py
# Version: 1
# Path: ay_platform_core/tests/unit/c6_validation/test_interface_signature_drift.py
# Description: Unit tests for C6 check #3 `interface-signature-drift`
#              (R-700-022, de-stubbed at V1 close). Compares each artifact's
#              PUBLIC AST signatures against the same-path baseline (previous
#              version). Drift = a public symbol removed or re-signed. No
#              baseline → nothing to compare (pass).
#
# @relation validates:R-700-022
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

_RUN = "run-isd-1"


def _ctx(
    *, current: list[CodeArtifact], baseline: list[CodeArtifact]
) -> CheckContext:
    return CheckContext(
        project_id="demo",
        domain="code",
        artifacts=current,
        baseline_artifacts=baseline,
    )


def _art(content: str, path: str = "src/svc.py") -> CodeArtifact:
    return CodeArtifact(path=path, content=content)


def test_no_baseline_is_a_pass() -> None:
    """First generation (no previous version) → nothing to compare."""
    out = checks.check_interface_signature_drift(
        _RUN, _ctx(current=[_art("def run(a, b):\n    return a\n")], baseline=[])
    )
    assert out == []


def test_stable_signature_is_a_pass() -> None:
    src = "def run(a, b):\n    return a\n"
    out = checks.check_interface_signature_drift(
        _RUN, _ctx(current=[_art(src)], baseline=[_art(src)])
    )
    assert out == []


def test_changed_parameter_is_drift() -> None:
    base = _art("def run(a, b):\n    return a\n")
    cur = _art("def run(a, b, c):\n    return a\n")
    out = checks.check_interface_signature_drift(
        _RUN, _ctx(current=[cur], baseline=[base])
    )
    assert len(out) == 1
    assert out[0].check_id == "interface-signature-drift"
    assert out[0].severity == Severity.ADVISORY
    assert out[0].location == "run"
    assert "(a, b)" in out[0].message and "(a, b, c)" in out[0].message


def test_renamed_public_parameter_is_drift() -> None:
    base = _art("def run(a, b):\n    return a\n")
    cur = _art("def run(a, renamed):\n    return a\n")
    out = checks.check_interface_signature_drift(
        _RUN, _ctx(current=[cur], baseline=[base])
    )
    assert len(out) == 1
    assert out[0].location == "run"


def test_removed_public_symbol_is_drift() -> None:
    base = _art("def run(a):\n    return a\n\n\ndef helper(x):\n    return x\n")
    cur = _art("def run(a):\n    return a\n")
    out = checks.check_interface_signature_drift(
        _RUN, _ctx(current=[cur], baseline=[base])
    )
    assert len(out) == 1
    assert out[0].location == "helper"
    assert "removed" in out[0].message


def test_missing_current_artifact_flags_every_public_symbol() -> None:
    """The whole file disappeared → each of its public symbols drifted."""
    base = _art("def a():\n    return 1\n\n\ndef b():\n    return 2\n")
    out = checks.check_interface_signature_drift(
        _RUN, _ctx(current=[], baseline=[base])
    )
    assert {f.location for f in out} == {"a", "b"}


def test_private_symbols_are_ignored() -> None:
    base = _art("def _hidden(a, b):\n    return a\n")
    cur = _art("def _hidden(a):\n    return a\n")
    out = checks.check_interface_signature_drift(
        _RUN, _ctx(current=[cur], baseline=[base])
    )
    assert out == []


def test_public_method_of_public_class_is_tracked() -> None:
    base = _art("class Svc:\n    def call(self, a, b):\n        return a\n")
    cur = _art("class Svc:\n    def call(self, a):\n        return a\n")
    out = checks.check_interface_signature_drift(
        _RUN, _ctx(current=[cur], baseline=[base])
    )
    assert len(out) == 1
    assert out[0].location == "Svc.call"


def test_non_python_and_unparseable_are_ignored() -> None:
    base_md = _art("# a doc\n", path="docs/readme.md")
    base_broken = _art("def run(:\n", path="src/broken.py")
    out = checks.check_interface_signature_drift(
        _RUN,
        _ctx(
            current=[_art("changed\n", path="docs/readme.md")],
            baseline=[base_md, base_broken],
        ),
    )
    assert out == []
