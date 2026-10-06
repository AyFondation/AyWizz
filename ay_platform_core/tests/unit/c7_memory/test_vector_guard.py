# =============================================================================
# File: test_vector_guard.py
# Version: 1
# Path: ay_platform_core/tests/unit/c7_memory/test_vector_guard.py
# Description: Unit tests for `_vector_is_comparable`, the guard that keeps a
#              single unusable embedding from aborting a whole retrieval.
#
#              WHY IT EXISTS. `retrieve()` scored EVERY scanned row with
#              `cosine(query_vector, row["vector"])`, and `cosine` raises
#              ValueError on a length mismatch — deliberately, because
#              R-400-002 forbids comparing two models' embeddings. A chunk
#              stored with an empty vector has length 0, so it hit that
#              raise. The blast radius was not the offending source: the
#              exception aborted the retrieval request, i.e. search broke for
#              the WHOLE project, and the chat RAG path with it.
#
#              The upstream cause is now refused at the boundary (C7 answers
#              422 and marks the source FAILED when C13's artifacts carry no
#              vectors), so a fresh ingestion cannot create such a row. This
#              guard covers the rows that predate that check and anything a
#              future ingestion path gets wrong — the cost of being wrong
#              here is a project-wide outage, which is worth two lines of
#              defence.
#
# @relation validates:R-400-002
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c7_memory.retrieval.similarity import cosine
from ay_platform_core.c7_memory.service import _vector_is_comparable

pytestmark = pytest.mark.unit

_DIM = 4


def test_a_matching_dimension_is_comparable() -> None:
    assert _vector_is_comparable({"vector": [0.1, 0.2, 0.3, 0.4]}, _DIM)


def test_an_empty_vector_is_not_comparable() -> None:
    """The exact shape a vector-less C13 chunk used to land with."""
    assert not _vector_is_comparable({"vector": []}, _DIM)


def test_a_null_vector_is_not_comparable() -> None:
    assert not _vector_is_comparable({"vector": None}, _DIM)


def test_an_absent_vector_field_is_not_comparable() -> None:
    assert not _vector_is_comparable({"chunk_id": "c1"}, _DIM)


def test_a_shorter_vector_is_not_comparable() -> None:
    assert not _vector_is_comparable({"vector": [0.1, 0.2]}, _DIM)


def test_a_longer_vector_is_not_comparable() -> None:
    """A model swap without re-embedding produces exactly this."""
    assert not _vector_is_comparable({"vector": [0.1] * 8}, _DIM)


def test_an_all_zero_vector_of_the_right_dimension_stays_comparable() -> None:
    """Zero magnitude is NOT the same defect and SHALL NOT be filtered.

    `cosine` already returns 0.0 for a zero-magnitude vector instead of
    NaN, so such a row scores last and ranks itself out. Dropping it here
    would silently discard a chunk that is merely uninformative, and this
    guard is about rows that would CRASH the request.
    """
    assert _vector_is_comparable({"vector": [0.0] * _DIM}, _DIM)
    assert cosine([1.0, 0.0, 0.0, 0.0], [0.0] * _DIM) == 0.0


# ---------------------------------------------------------------------------
# The defect being guarded, reproduced — so the guard cannot be "proven" by
# a test that would pass with no guard at all.
# ---------------------------------------------------------------------------


def test_cosine_raises_on_the_rows_the_guard_rejects() -> None:
    """Establishes that these rows really are request-killing, not merely
    low-scoring. Without this, every assertion above could hold while the
    filter protected against nothing."""
    query = [1.0, 0.0, 0.0, 0.0]
    with pytest.raises(ValueError, match="different lengths"):
        cosine(query, [])
    with pytest.raises(ValueError, match="different lengths"):
        cosine(query, [0.1, 0.2])


def test_the_guard_admits_exactly_what_cosine_can_score() -> None:
    """The guard's contract: anything it admits, `cosine` can score without
    raising. Property-style over a spread of widths, so a future edit to
    either side that breaks the pairing fails here."""
    query = [1.0, 0.0, 0.0, 0.0]
    for width in range(0, 9):
        row = {"vector": [0.5] * width}
        admitted = _vector_is_comparable(row, len(query))
        if admitted:
            assert isinstance(cosine(query, list(row["vector"])), float)
        else:
            with pytest.raises(ValueError):
                cosine(query, list(row["vector"]))
