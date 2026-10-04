# =============================================================================
# File: test_baseline_models.py
# Version: 1
# Path: ay_platform_core/tests/unit/c5_requirements/test_baseline_models.py
# Description: The baseline manifest — R-310-200, R-310-201, R-310-204.
#
#              THE TEST THAT CARRIES THE REQUIREMENT is the one asserting
#              that a manifest entry has no `content` field. "SHALL NOT
#              duplicate object content" is a structural property, and the
#              thing a future renderer will want is "just inline the text so
#              the export needs one read" — so the absence is pinned by name
#              rather than left to be noticed in review.
#
# @relation validates:R-310-200
# @relation validates:R-310-201
# @relation validates:R-310-204
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ay_platform_core.c5_requirements.baseline.models import (
    BaselineBlocker,
    BaselineManifest,
    BaselineReadiness,
    BaselineRefusal,
    ManifestLink,
    ManifestObject,
    content_hash,
)

_NOW = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)


def _entry(
    object_id: str = "AD-100",
    container: str = "030-ARCH",
    ordinal: int = 1,
    version: int = 2,
    state: str = "accepted",
    text: str = "The controller ramps torque.",
) -> ManifestObject:
    return ManifestObject(
        object_id=object_id,
        container=container,
        version=version,
        ordinal=ordinal,
        review_state=state,
        content_hash=content_hash("body", text),
    )


def _manifest(**overrides: object) -> BaselineManifest:
    base: dict[str, object] = {
        "tag": "B-2026-10",
        "project_id": "adas",
        "cycle_id": "C-AUTO",
        "cycle_version": 2,
        "created_by": "o.mathieu",
        "created_at": _NOW,
        "objects": (_entry(),),
    }
    base.update(overrides)
    return BaselineManifest.model_validate(base)


# ---------------------------------------------------------------------------
# R-310-200 — a manifest NAMES content, it never copies it
# ---------------------------------------------------------------------------


def test_a_manifest_entry_cannot_carry_content() -> None:
    """The structural form of "SHALL NOT duplicate object content".

    A second copy of a paragraph creates a second answer to "what did this
    baseline contain?", and the two diverge the first time one is migrated.
    """
    assert "content" not in ManifestObject.model_fields
    assert "body" not in ManifestObject.model_fields
    assert "notation" not in ManifestObject.model_fields
    assert "text" not in ManifestObject.model_fields


def test_an_entry_carrying_content_is_refused_outright() -> None:
    with pytest.raises(ValidationError):
        ManifestObject.model_validate(
            {
                "object_id": "AD-100",
                "container": "030-ARCH",
                "version": 2,
                "ordinal": 1,
                "review_state": "accepted",
                "content_hash": content_hash("body", "x"),
                "content": "The controller ramps torque.",
            }
        )


def test_an_entry_names_a_version_and_a_hash() -> None:
    entry = _entry()
    assert entry.version == 2
    assert entry.content_hash.startswith("sha256:")


def test_a_malformed_hash_is_refused() -> None:
    with pytest.raises(ValidationError):
        ManifestObject(
            object_id="AD-100",
            container="030-ARCH",
            version=1,
            ordinal=1,
            review_state="accepted",
            content_hash="deadbeef",
        )


# ---------------------------------------------------------------------------
# The content hash
# ---------------------------------------------------------------------------


def test_the_hash_is_prefixed_with_its_algorithm() -> None:
    """An unprefixed digest is indistinguishable from another algorithm's."""
    assert content_hash("body", "x").startswith("sha256:")


def test_the_hash_is_stable_for_the_same_input() -> None:
    assert content_hash("body", "same") == content_hash("body", "same")


def test_the_hash_distinguishes_prose_from_a_figure() -> None:
    """R-310-008 makes them exclusive; the same string in each must differ.

    An earlier draft hashed the text alone, which made every figure hash
    the empty string and therefore collide with every other figure.
    """
    assert content_hash("body", "A") != content_hash("notation", "A")


def test_two_empty_figures_do_not_collide_with_two_empty_paragraphs() -> None:
    assert content_hash("notation", "") != content_hash("body", "")


def test_different_text_hashes_differently() -> None:
    assert content_hash("body", "a") != content_hash("body", "b")


# ---------------------------------------------------------------------------
# Manifest coherence
# ---------------------------------------------------------------------------


def test_a_tag_that_is_not_a_path_segment_is_refused() -> None:
    for bad in ("with space", "with/slash", "../escape", ""):
        with pytest.raises(ValidationError):
            _manifest(tag=bad)


def test_two_versions_of_one_object_cannot_be_in_one_baseline() -> None:
    """A photograph holds one state per object."""
    with pytest.raises(ValidationError, match="one state per object"):
        _manifest(objects=(_entry(version=2), _entry(version=3)))


def test_a_link_naming_an_excluded_object_is_refused() -> None:
    """The pin would resolve to nothing."""
    with pytest.raises(ValidationError, match="resolve to nothing"):
        _manifest(
            links=(
                ManifestLink(
                    object_id="AD-999",
                    container="030-ARCH",
                    target_id="CUST-001",
                    pinned_version=4,
                    strength="covered",
                    state="accepted",
                ),
            )
        )


def test_a_link_pins_the_version_it_answered() -> None:
    manifest = _manifest(
        links=(
            ManifestLink(
                object_id="AD-100",
                container="030-ARCH",
                target_id="CUST-001",
                pinned_version=4,
                strength="covered",
                state="accepted",
            ),
        )
    )
    assert manifest.links_of("AD-100")[0].pinned_version == 4


def test_a_manifest_counts_what_it_names() -> None:
    manifest = _manifest(
        objects=(_entry(), _entry("AD-101", ordinal=2)),
        links=(
            ManifestLink(
                object_id="AD-100",
                container="030-ARCH",
                target_id="CUST-001",
                pinned_version=1,
                strength="covered",
                state="accepted",
            ),
        ),
    )
    assert manifest.object_count == 2
    assert manifest.link_count == 1


def test_containers_are_ordered_by_their_first_entry_ordinal() -> None:
    """The cycle's cascade is what makes a rendered document readable."""
    manifest = _manifest(
        objects=(
            _entry("TD-900", container="050-TECH", ordinal=50),
            _entry("FD-010", container="020-FUNC", ordinal=10),
            _entry("AD-100", container="030-ARCH", ordinal=30),
        )
    )
    assert manifest.containers == ("020-FUNC", "030-ARCH", "050-TECH")


def test_entries_of_a_container_come_back_in_document_order() -> None:
    manifest = _manifest(
        objects=(
            _entry("AD-102", ordinal=3),
            _entry("AD-100", ordinal=1),
            _entry("AD-101", ordinal=2),
        )
    )
    assert [e.object_id for e in manifest.entries_of("030-ARCH")] == [
        "AD-100",
        "AD-101",
        "AD-102",
    ]


def test_asking_for_an_absent_entry_is_an_error_not_a_default() -> None:
    with pytest.raises(KeyError, match="AD-999"):
        _manifest().entry("AD-999")


def test_a_manifest_round_trips_through_its_serialised_form() -> None:
    manifest = _manifest()
    assert BaselineManifest.model_validate(manifest.model_dump()) == manifest


def test_an_empty_baseline_is_representable() -> None:
    """A project with no accepted content yet still has a readable record."""
    manifest = _manifest(objects=())
    assert manifest.object_count == 0
    assert manifest.containers == ()


# ---------------------------------------------------------------------------
# R-310-201 — the gate
# ---------------------------------------------------------------------------


def test_nothing_outstanding_means_ready() -> None:
    verdict = BaselineReadiness(project_id="adas")
    assert verdict.is_ready is True
    assert "ready to baseline" in verdict.explain()


def test_every_blocker_is_named_with_its_subject() -> None:
    verdict = BaselineReadiness(
        project_id="adas",
        refusals=(
            BaselineRefusal(
                blocker=BaselineBlocker.OPEN_CHANGE_TICKET,
                subject="adas:W14:CUST-001",
                detail="not absorbed end to end",
            ),
            BaselineRefusal(
                blocker=BaselineBlocker.STALE_COVERAGE_LINK,
                subject="AD-100->CUST-001",
                detail="pins v2, now at v5",
            ),
        ),
    )
    assert verdict.is_ready is False
    assert verdict.blockers == {
        BaselineBlocker.OPEN_CHANGE_TICKET,
        BaselineBlocker.STALE_COVERAGE_LINK,
    }
    explained = verdict.explain()
    assert "open-change-ticket [adas:W14:CUST-001]" in explained
    assert "stale-coverage-link [AD-100->CUST-001]" in explained


def test_the_three_blockers_are_the_whole_set() -> None:
    """A fourth would be a spec amendment, not an implementation choice."""
    assert {b.value for b in BaselineBlocker} == {
        "open-change-ticket",
        "critical-coverage-gap",
        "stale-coverage-link",
    }
