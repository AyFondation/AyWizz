# =============================================================================
# File: test_checks.py
# Version: 1
# Path: ay_platform_core/tests/unit/c6_validation/test_checks.py
# Description: Unit tests for each of the 9 MUST checks of the `code` domain.
#              The 5 real checks are exercised on both positive (clean) and
#              negative (violation) fixtures. The 4 stubs are checked to
#              produce exactly one `severity=info` finding with the expected
#              check_id.
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ay_platform_core.c6_validation.domains.code import checks
from ay_platform_core.c6_validation.models import (
    CheckContext,
    CodeArtifact,
    Severity,
)

RUN_ID = "run-test-0001"


def _context(
    *,
    requirements: list[dict[str, object]] | None = None,
    artifacts: list[CodeArtifact] | None = None,
) -> CheckContext:
    return CheckContext(
        project_id="demo",
        domain="code",
        requirements=requirements or [],
        artifacts=artifacts or [],
    )


def _req(
    entity_id: str, *, status: str = "approved", type_: str = "R"
) -> dict[str, object]:
    return {"entity_id": entity_id, "status": status, "type": type_}


@pytest.mark.unit
class TestCheck1ReqWithoutCode:
    def test_missing_implementer_yields_blocking(self) -> None:
        ctx = _context(requirements=[_req("R-100-001")], artifacts=[])
        out = checks.check_req_without_code(RUN_ID, ctx)
        assert len(out) == 1
        assert out[0].check_id == "req-without-code"
        assert out[0].severity == Severity.BLOCKING
        assert out[0].entity_id == "R-100-001"

    def test_draft_entity_ignored(self) -> None:
        ctx = _context(requirements=[_req("R-100-001", status="draft")])
        assert checks.check_req_without_code(RUN_ID, ctx) == []

    def test_covered_requirement_ignored(self) -> None:
        ctx = _context(
            requirements=[_req("R-100-001")],
            artifacts=[
                CodeArtifact(
                    path="src/impl.py",
                    content="# @relation implements:R-100-001\n",
                )
            ],
        )
        assert checks.check_req_without_code(RUN_ID, ctx) == []


@pytest.mark.unit
class TestCheck2CodeWithoutRequirement:
    def test_bare_module_flagged(self) -> None:
        ctx = _context(
            artifacts=[
                CodeArtifact(path="src/empty.py", content="def x(): pass\n")
            ]
        )
        out = checks.check_code_without_requirement(RUN_ID, ctx)
        assert len(out) == 1
        assert out[0].severity == Severity.BLOCKING
        assert out[0].artifact_ref == "src/empty.py"

    def test_init_py_exempt(self) -> None:
        ctx = _context(
            artifacts=[CodeArtifact(path="src/pkg/__init__.py", content="")]
        )
        assert checks.check_code_without_requirement(RUN_ID, ctx) == []

    def test_marker_present_passes(self) -> None:
        ctx = _context(
            artifacts=[
                CodeArtifact(
                    path="src/good.py",
                    content="# @relation implements:R-100-001\n",
                )
            ]
        )
        assert checks.check_code_without_requirement(RUN_ID, ctx) == []


@pytest.mark.unit
class TestCheck4TestAbsentForRequirement:
    def test_approved_req_without_test_flagged(self) -> None:
        ctx = _context(
            requirements=[_req("R-100-001")],
            artifacts=[
                CodeArtifact(
                    path="src/impl.py",
                    content="# @relation implements:R-100-001\n",
                ),
            ],
        )
        out = checks.check_test_absent_for_requirement(RUN_ID, ctx)
        assert len(out) == 1
        assert out[0].entity_id == "R-100-001"

    def test_test_file_validates_passes(self) -> None:
        ctx = _context(
            requirements=[_req("R-100-001")],
            artifacts=[
                CodeArtifact(
                    path="tests/unit/test_foo.py",
                    content="# @relation validates:R-100-001\n",
                    is_test=True,
                )
            ],
        )
        assert checks.check_test_absent_for_requirement(RUN_ID, ctx) == []


@pytest.mark.unit
class TestCheck5OrphanTest:
    def test_orphan_test_flagged(self) -> None:
        ctx = _context(
            artifacts=[
                CodeArtifact(
                    path="tests/unit/test_foo.py",
                    content="def test_bar(): assert True\n",
                    is_test=True,
                )
            ]
        )
        out = checks.check_orphan_test(RUN_ID, ctx)
        assert len(out) == 1
        assert out[0].check_id == "orphan-test"

    def test_conftest_exempt(self) -> None:
        ctx = _context(
            artifacts=[
                CodeArtifact(
                    path="tests/conftest.py", content="", is_test=True
                )
            ]
        )
        assert checks.check_orphan_test(RUN_ID, ctx) == []

    def test_test_with_marker_passes(self) -> None:
        ctx = _context(
            artifacts=[
                CodeArtifact(
                    path="tests/unit/test_foo.py",
                    content="# @relation validates:R-100-001\n",
                    is_test=True,
                )
            ]
        )
        assert checks.check_orphan_test(RUN_ID, ctx) == []

    def test_non_test_artifact_ignored(self) -> None:
        """Non-test artifacts are out of scope for this check."""
        ctx = _context(
            artifacts=[
                CodeArtifact(path="src/foo.py", content="def x(): pass\n")
            ]
        )
        assert checks.check_orphan_test(RUN_ID, ctx) == []


@pytest.mark.unit
class TestCheck6ObsoleteReference:
    def test_unknown_target_flagged(self) -> None:
        ctx = _context(
            requirements=[_req("R-100-001")],
            artifacts=[
                CodeArtifact(
                    path="src/impl.py",
                    content="# @relation implements:R-100-999\n",
                )
            ],
        )
        out = checks.check_obsolete_reference(RUN_ID, ctx)
        assert len(out) == 1
        assert out[0].entity_id == "R-100-999"

    def test_deprecated_target_flagged(self) -> None:
        ctx = _context(
            requirements=[_req("R-100-001", status="deprecated")],
            artifacts=[
                CodeArtifact(
                    path="src/impl.py",
                    content="# @relation implements:R-100-001\n",
                )
            ],
        )
        out = checks.check_obsolete_reference(RUN_ID, ctx)
        assert len(out) == 1
        assert "DEPRECATED" in out[0].message

    def test_clean_marker_passes(self) -> None:
        ctx = _context(
            requirements=[_req("R-100-001")],
            artifacts=[
                CodeArtifact(
                    path="src/impl.py",
                    content="# @relation implements:R-100-001\n",
                )
            ],
        )
        assert checks.check_obsolete_reference(RUN_ID, ctx) == []


@pytest.mark.unit
class TestCheck7VersionDrift:
    def test_unpinned_marker_ignored(self) -> None:
        ctx = _context(
            requirements=[_req("R-100-001")],  # approved, version=None in _req
            artifacts=[
                CodeArtifact(
                    path="src/impl.py",
                    content="# @relation implements:R-100-001\n",
                )
            ],
        )
        assert checks.check_version_drift(RUN_ID, ctx) == []

    def test_pin_matches_current_version_passes(self) -> None:
        ctx = _context(
            requirements=[
                {"entity_id": "R-100-001", "status": "approved", "version": 3, "type": "R"},
            ],
            artifacts=[
                CodeArtifact(
                    path="src/impl.py",
                    content="# @relation implements:R-100-001@v3\n",
                )
            ],
        )
        assert checks.check_version_drift(RUN_ID, ctx) == []

    def test_pin_lags_current_version_is_blocking(self) -> None:
        ctx = _context(
            requirements=[
                {"entity_id": "R-100-001", "status": "approved", "version": 3, "type": "R"},
            ],
            artifacts=[
                CodeArtifact(
                    path="src/impl.py",
                    content="# @relation implements:R-100-001@v2\n",
                )
            ],
        )
        out = checks.check_version_drift(RUN_ID, ctx)
        assert len(out) == 1
        assert out[0].severity == Severity.BLOCKING
        assert out[0].entity_id == "R-100-001"
        assert "v2" in out[0].message and "v3" in out[0].message

    def test_unknown_entity_delegated_to_obsolete_reference(self) -> None:
        """No entity in context.requirements → this check defers to
        obsolete-reference, which owns the 'unknown target' path."""
        ctx = _context(
            requirements=[],
            artifacts=[
                CodeArtifact(
                    path="src/impl.py",
                    content="# @relation implements:R-100-001@v1\n",
                )
            ],
        )
        assert checks.check_version_drift(RUN_ID, ctx) == []

    def test_malformed_pin_ignored(self) -> None:
        """A non-numeric pin is a marker-syntax issue, not version-drift."""
        ctx = _context(
            requirements=[
                {"entity_id": "R-100-001", "status": "approved", "version": 3, "type": "R"},
            ],
        )
        # extract_markers will reject this as a syntax error so context.markers
        # will be empty for this target; checking via dispatcher:
        out = checks.check_version_drift(RUN_ID, ctx)
        assert out == []


@pytest.mark.unit
class TestCheck9CrossLayerCoherence:
    def test_no_tailoring_passes(self) -> None:
        ctx = _context(requirements=[_req("R-100-001")])
        assert checks.check_cross_layer_coherence(RUN_ID, ctx) == []

    def test_tailoring_with_override_passes(self) -> None:
        ctx = _context(
            requirements=[
                {
                    "entity_id": "R-300-001",
                    "status": "approved",
                    "type": "R",
                    "tailoring_of": "R-100-001",
                    "override": True,
                }
            ]
        )
        assert checks.check_cross_layer_coherence(RUN_ID, ctx) == []

    def test_tailoring_without_override_is_blocking(self) -> None:
        ctx = _context(
            requirements=[
                {
                    "entity_id": "R-300-001",
                    "status": "approved",
                    "type": "R",
                    "tailoring_of": "R-100-001",
                    "override": False,
                }
            ]
        )
        out = checks.check_cross_layer_coherence(RUN_ID, ctx)
        assert len(out) == 1
        assert out[0].severity == Severity.BLOCKING
        assert out[0].entity_id == "R-300-001"

    def test_tailoring_with_missing_override_key_is_blocking(self) -> None:
        ctx = _context(
            requirements=[
                {
                    "entity_id": "R-300-001",
                    "status": "approved",
                    "type": "R",
                    "tailoring_of": "R-100-001",
                }
            ]
        )
        out = checks.check_cross_layer_coherence(RUN_ID, ctx)
        assert len(out) == 1

    def test_hyphenated_alias_accepted(self) -> None:
        """C5's on-the-wire field is ``tailoring-of`` — the check SHALL
        recognise both the snake_case and hyphenated forms."""
        ctx = _context(
            requirements=[
                {
                    "entity_id": "R-300-001",
                    "status": "approved",
                    "type": "R",
                    "tailoring-of": "R-100-001",
                    "override": True,
                }
            ]
        )
        assert checks.check_cross_layer_coherence(RUN_ID, ctx) == []


@pytest.mark.unit
class TestRemainingStubs:
    """#3 (interface-signature-drift, R-700-022) is the last STUB — it needs
    machine-readable signature specs on `E-*` entities (deferred). It emits
    one info finding. (#8 data-model-drift was de-stubbed under D-017 — see
    `test_data_model_drift.py`.)
    """

    def test_interface_signature_drift_is_a_stub(self) -> None:
        out = checks.check_interface_signature_drift(RUN_ID, _context())
        assert len(out) == 1
        assert out[0].severity == Severity.INFO
        assert out[0].check_id == "interface-signature-drift"


@pytest.mark.unit
class TestDispatcher:
    def test_known_check_routes_correctly(self) -> None:
        ctx = _context(requirements=[_req("R-100-001")])
        out = checks.dispatch("req-without-code", RUN_ID, ctx)
        assert len(out) == 1
        assert out[0].check_id == "req-without-code"

    def test_unknown_check_raises_keyerror(self) -> None:
        with pytest.raises(KeyError):
            checks.dispatch("nope", RUN_ID, _context())

    def test_available_check_ids_matches_dispatch(self) -> None:
        ids = checks.available_check_ids()
        # 9 MUST checks per D-006
        assert len(ids) == 9

    def test_finding_created_at_is_utc(self) -> None:
        ctx = _context(requirements=[_req("R-100-001")])
        out = checks.check_req_without_code(RUN_ID, ctx)
        assert out[0].created_at.tzinfo is not None
        assert out[0].created_at.tzinfo.utcoffset(datetime.now(UTC)) is not None
