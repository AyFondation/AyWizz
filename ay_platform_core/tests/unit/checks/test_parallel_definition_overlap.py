# =============================================================================
# File: test_parallel_definition_overlap.py
# Version: 1
# Path: ay_platform_core/tests/unit/checks/test_parallel_definition_overlap.py
# Description: Unit tests for the overlap rule of
#              `scripts/checks/check_no_parallel_definitions.py`.
#
#              The check exists to catch a class that DUPLICATES a registered
#              contract. Its signal is shared field names; its noise is the
#              scoping and audit columns every persisted entity in this
#              platform carries by convention. v3 subtracts that noise.
#
#              These tests exist for two reasons:
#                - to prove the noise reduction does not cost detection —
#                  a genuine duplicate is still flagged;
#                - to PIN the exclusion list. A list of "fields that don't
#                  count" is exactly what gets quietly extended to silence a
#                  real finding later (§11.2 anti-pattern #3), so extending
#                  it must break a test and become a deliberate act.
#
#              No `@relation validates:` marker: this validates a coherence
#              TOOL, not a requirement. The §8.4 no-parallel-definition rule
#              is a CLAUDE.md discipline, not an `R-` entity, and inventing
#              a marker to satisfy the marker check would be the fabrication
#              those checks exist to prevent.
# =============================================================================

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_CHECKS = Path(__file__).resolve().parents[3] / "scripts" / "checks"
if str(_CHECKS) not in sys.path:
    sys.path.insert(0, str(_CHECKS))

from check_no_parallel_definitions import (  # noqa: E402
    CONVENTIONAL_FIELDS,
    OVERLAP_THRESHOLD,
    significant_overlap,
)

#: A registered contract's fields, domain and conventional mixed — the shape
#: `EntityPublic` actually has.
_CONTRACT = {
    "project_id", "entity_id", "type", "version", "status", "category",
    "title", "body", "created_at", "created_by", "updated_at", "updated_by",
}


@pytest.mark.unit
class TestConventionalFields:
    def test_the_exclusion_list_is_exactly_this(self) -> None:
        """Pinned on purpose: extending it must be a deliberate, visible act.

        Every entry is a field the platform attaches structurally — tenant
        and project scoping, the audit quartet, the version counter. None is
        a domain concept. A future entry that IS a domain concept would blind
        the check, which is why this assertion is exhaustive rather than a
        containment check.
        """
        expected = {
            "project_id",
            "tenant_id",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
            "version",
        }
        assert set(CONVENTIONAL_FIELDS) == expected

    def test_no_domain_field_is_excluded(self) -> None:
        # The fields a real duplicate would share must all still count.
        domain = {"entity_id", "status", "category", "title", "body", "type", "slug"}
        assert not (domain & CONVENTIONAL_FIELDS)


@pytest.mark.unit
class TestSignificantOverlap:
    def test_audit_only_overlap_is_not_significant(self) -> None:
        """The case that made every new model module fail the build.

        `DocObject` shares six fields with `EntityPublic` — all of them
        conventional. It is not a parallel definition: one is a unit of
        document content, the other a requirement entity.
        """
        doc_object = {
            "object_id", "project_id", "container", "type", "body", "parent",
            "ordinal", "version", "review_state", "created_at", "created_by",
            "updated_at", "updated_by",
        }
        overlap = significant_overlap(doc_object, _CONTRACT)
        assert overlap == {"type", "body"}
        assert len(overlap) < OVERLAP_THRESHOLD

    def test_a_genuine_duplicate_is_still_flagged(self) -> None:
        """Detection power is what the change must not cost."""
        copied = {
            "project_id", "entity_id", "type", "status", "category", "title",
            "body", "created_at",
        }
        overlap = significant_overlap(copied, _CONTRACT)
        assert overlap == {"entity_id", "type", "status", "category", "title", "body"}
        assert len(overlap) >= OVERLAP_THRESHOLD

    def test_three_domain_fields_still_trip_the_threshold(self) -> None:
        # The threshold is unchanged; only the noise was removed.
        borderline = {"entity_id", "status", "category", "project_id", "version"}
        assert len(significant_overlap(borderline, _CONTRACT)) == OVERLAP_THRESHOLD

    def test_two_domain_fields_do_not(self) -> None:
        borderline = {"entity_id", "status", "project_id", "tenant_id", "version"}
        assert len(significant_overlap(borderline, _CONTRACT)) < OVERLAP_THRESHOLD

    def test_a_class_of_only_conventional_fields_is_never_flagged(self) -> None:
        # An audit mixin duplicated across modules is convention, not drift.
        audit_only = set(CONVENTIONAL_FIELDS)
        assert significant_overlap(audit_only, _CONTRACT) == set()

    def test_overlap_is_symmetric(self) -> None:
        a, b = {"entity_id", "status", "project_id"}, {"entity_id", "status", "tenant_id"}
        assert significant_overlap(a, b) == significant_overlap(b, a)

    def test_disjoint_classes_overlap_not_at_all(self) -> None:
        assert significant_overlap({"holder", "expires_at"}, _CONTRACT) == set()
