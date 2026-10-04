# =============================================================================
# File: test_absorption_traversal.py
# Version: 1
# Path: ay_platform_core/tests/unit/c5_requirements/test_absorption_traversal.py
# Description: The impact DAG — R-310-141 … R-310-144.
#
#              These tests are the reason the walk is a pure module: the
#              properties that matter (a node once, all its causes, a
#              cycle refused, a denominator that counts nodes) are graph
#              facts, and asserting them against a real ArangoDB would
#              prove the driver works rather than that the arithmetic is
#              right.
#
# @relation validates:R-310-141
# @relation validates:R-310-142
# @relation validates:R-310-143
# @relation validates:R-310-144
# @relation validates:T-310-002
# =============================================================================

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

import pytest
from pydantic import ValidationError

from ay_platform_core.c5_requirements.absorption.traversal import (
    DEFAULT_MAX_PATHS,
    GraphDefectError,
    ImpactEdge,
    ImpactPath,
    ImpactSet,
    traverse,
)


class _Graph:
    """An in-memory impact graph with a batched layer lookup.

    Records the calls it served so a test can assert the walk costs one
    round trip per layer rather than one per node.
    """

    def __init__(self, *edges: ImpactEdge) -> None:
        self._edges = list(edges)
        self.calls: list[frozenset[str]] = []

    async def __call__(self, layer: frozenset[str]) -> Sequence[ImpactEdge]:
        self.calls.append(layer)
        return [edge for edge in self._edges if edge.source_id in layer]


def _edge(source: str, node: str, container: str = "arch", version: int = 1) -> ImpactEdge:
    return ImpactEdge(
        source_id=source, node_id=node, container=container, pinned_version=version
    )


# ---------------------------------------------------------------------------
# Reachability — R-310-141, R-310-142
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_requirement_nothing_answers_has_an_empty_impact_set() -> None:
    result = await traverse("CUST-001", _Graph())
    assert result.is_empty
    assert result.node_count == 0
    assert result.node_ids == frozenset()


@pytest.mark.asyncio
async def test_impact_cascades_through_every_layer_of_the_cycle() -> None:
    graph = _Graph(
        _edge("CUST-001", "FD-010", "functional"),
        _edge("FD-010", "AD-100", "architecture"),
        _edge("AD-100", "TD-900", "technical"),
    )
    result = await traverse("CUST-001", graph)

    assert result.node_ids == {"FD-010", "AD-100", "TD-900"}
    assert result.node("TD-900").depth == 3
    assert str(result.node("TD-900").paths[0]) == "CUST-001 -> FD-010 -> AD-100 -> TD-900"


@pytest.mark.asyncio
async def test_the_seed_is_not_a_member_of_its_own_impact_set() -> None:
    """The change is not impacted by itself; dispositioning it is meaningless."""
    graph = _Graph(_edge("CUST-001", "FD-010"))
    result = await traverse("CUST-001", graph)
    assert "CUST-001" not in result.node_ids


@pytest.mark.asyncio
async def test_the_walk_costs_one_round_trip_per_layer_not_per_node() -> None:
    graph = _Graph(
        _edge("CUST-001", "FD-010"),
        _edge("CUST-001", "FD-011"),
        _edge("CUST-001", "FD-012"),
        _edge("FD-010", "AD-100"),
        _edge("FD-011", "AD-101"),
        _edge("FD-012", "AD-102"),
    )
    result = await traverse("CUST-001", graph)

    assert result.node_count == 6
    # Three layers of lookup plus the terminating empty frontier.
    assert len(graph.calls) == 3
    assert graph.calls[1] == {"FD-010", "FD-011", "FD-012"}


@pytest.mark.asyncio
async def test_a_node_is_expanded_once_even_when_reached_repeatedly() -> None:
    graph = _Graph(
        _edge("CUST-001", "FD-010"),
        _edge("CUST-001", "FD-011"),
        _edge("FD-010", "AD-100"),
        _edge("FD-011", "AD-100"),
        _edge("AD-100", "TD-900"),
    )
    result = await traverse("CUST-001", graph)

    assert [call for call in graph.calls if "AD-100" in call] == [frozenset({"AD-100"})]
    # Four impacted nodes; the seed is not one of them.
    assert result.node_count == 4


@pytest.mark.asyncio
async def test_a_lookup_that_over_returns_cannot_grow_the_impact_set() -> None:
    """R-310-141 is a guarantee only if the walk owns its own shape.

    A lookup returning an edge from outside the current layer — a buggy
    query, or an agent-supplied one — must not be able to inject a node.
    """

    async def leaky(layer: frozenset[str]) -> Sequence[ImpactEdge]:
        return [_edge("CUST-001", "FD-010"), _edge("SOMETHING-ELSE", "ROGUE-999")]

    result = await traverse("CUST-001", leaky)
    assert result.node_ids == {"FD-010"}


@pytest.mark.asyncio
async def test_a_duplicate_edge_is_recorded_once() -> None:
    graph = _Graph(_edge("CUST-001", "FD-010"), _edge("CUST-001", "FD-010"))
    result = await traverse("CUST-001", graph)
    assert len(result.node("FD-010").causes) == 1


# ---------------------------------------------------------------------------
# Cycles are defects — R-310-142
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_cycle_is_refused_and_the_cycle_itself_is_named() -> None:
    """The reported cycle must be a real closed walk in the graph.

    Which rotation the DFS reports depends on where it starts, so the
    assertion is on the property that makes the report actionable — every
    consecutive pair is an actual edge, and the walk closes — rather than
    on one rotation, which would pin a traversal-order detail.
    """
    edges = (
        _edge("CUST-001", "FD-010"),
        _edge("FD-010", "AD-100"),
        _edge("AD-100", "FD-010"),
    )
    with pytest.raises(GraphDefectError) as raised:
        await traverse("CUST-001", _Graph(*edges))

    cycle = raised.value.cycle
    real = {(edge.source_id, edge.node_id) for edge in edges}
    assert cycle[0] == cycle[-1]
    assert set(cycle) == {"FD-010", "AD-100"}
    assert all(pair in real for pair in pairwise(cycle))
    assert " -> ".join(cycle) in str(raised.value)
    assert "R-310-142" in str(raised.value)


@pytest.mark.asyncio
async def test_a_cycle_back_onto_the_seed_is_also_refused() -> None:
    graph = _Graph(_edge("CUST-001", "FD-010"), _edge("FD-010", "CUST-001"))
    with pytest.raises(GraphDefectError):
        await traverse("CUST-001", graph)


@pytest.mark.asyncio
async def test_a_cycle_is_refused_even_when_an_acyclic_branch_exists() -> None:
    """The traversal refuses the graph, not just the branch.

    A partial answer here would be the exact failure R-310-141 forbids:
    a plausible, incomplete impact set whose omission is invisible.
    """
    graph = _Graph(
        _edge("CUST-001", "FD-010"),
        _edge("CUST-001", "FD-020"),
        _edge("FD-020", "AD-200"),
        _edge("FD-010", "AD-100"),
        _edge("AD-100", "FD-010"),
    )
    with pytest.raises(GraphDefectError):
        await traverse("CUST-001", graph)


@pytest.mark.asyncio
async def test_a_diamond_is_not_a_cycle() -> None:
    """Convergence is the normal consequence of multi-allocation."""
    graph = _Graph(
        _edge("CUST-001", "FD-010"),
        _edge("CUST-001", "FD-011"),
        _edge("FD-010", "AD-100"),
        _edge("FD-011", "AD-100"),
    )
    result = await traverse("CUST-001", graph)
    assert result.node_count == 3


# ---------------------------------------------------------------------------
# Multi-path nodes — R-310-143, R-310-144 (T-310-002)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_node_reached_twice_appears_once_and_shows_both_paths() -> None:
    """T-310-002, the whole of it.

    One object reachable by two distinct paths: in the set exactly once,
    enumerating both paths, with the closure denominator equal to the
    distinct node count.
    """
    graph = _Graph(
        _edge("CUST-001", "FD-010", "functional"),
        _edge("CUST-001", "FD-011", "functional"),
        _edge("FD-010", "AD-100", "architecture"),
        _edge("FD-011", "AD-100", "architecture"),
    )
    result = await traverse("CUST-001", graph)

    assert sorted(result.node_ids) == ["AD-100", "FD-010", "FD-011"]
    node = result.node("AD-100")
    assert node.path_count == 2
    assert {str(path) for path in node.paths} == {
        "CUST-001 -> FD-010 -> AD-100",
        "CUST-001 -> FD-011 -> AD-100",
    }
    assert result.node_count == 3  # nodes, not the 4 paths


@pytest.mark.asyncio
async def test_every_cause_is_retained_so_half_a_reason_is_never_shown() -> None:
    graph = _Graph(
        _edge("CUST-001", "FD-010", "functional"),
        _edge("CUST-001", "FD-011", "functional"),
        _edge("FD-010", "AD-100", "architecture", version=3),
        _edge("FD-011", "AD-100", "architecture", version=7),
    )
    result = await traverse("CUST-001", graph)

    causes = result.node("AD-100").causes
    assert {cause.source_id for cause in causes} == {"FD-010", "FD-011"}
    assert {cause.pinned_version for cause in causes} == {3, 7}


@pytest.mark.asyncio
async def test_the_denominator_counts_nodes_even_when_paths_outnumber_them() -> None:
    """R-310-144: a denominator the numerator cannot reach is uncloseable."""
    graph = _Graph(
        _edge("CUST-001", "FD-010"),
        _edge("CUST-001", "FD-011"),
        _edge("FD-010", "AD-100"),
        _edge("FD-011", "AD-100"),
        _edge("FD-010", "AD-101"),
        _edge("FD-011", "AD-101"),
        _edge("AD-100", "TD-900"),
        _edge("AD-101", "TD-900"),
    )
    result = await traverse("CUST-001", graph)

    assert result.node_count == 5
    assert result.node("TD-900").path_count == 4
    assert sum(node.path_count for node in result.nodes) > result.node_count


@pytest.mark.asyncio
async def test_shortest_depth_is_reported_when_paths_differ_in_length() -> None:
    graph = _Graph(
        _edge("CUST-001", "AD-100"),
        _edge("CUST-001", "FD-010"),
        _edge("FD-010", "AD-100"),
    )
    result = await traverse("CUST-001", graph)
    assert result.node("AD-100").depth == 1


# ---------------------------------------------------------------------------
# The path cap is visible, and cannot affect membership
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_truncating_paths_is_recorded_and_never_silent() -> None:
    graph = _Graph(
        *[_edge("CUST-001", f"FD-{index:03d}", "functional") for index in range(10)],
        *[_edge(f"FD-{index:03d}", "AD-100", "architecture") for index in range(10)],
    )
    result = await traverse("CUST-001", graph, max_paths=4)

    node = result.node("AD-100")
    assert node.paths_truncated is True
    assert node.path_count == 4
    # Membership is unaffected: 10 functional nodes plus the convergence.
    assert result.node_count == 11


@pytest.mark.asyncio
async def test_truncation_propagates_so_no_descendant_claims_completeness() -> None:
    graph = _Graph(
        *[_edge("CUST-001", f"FD-{index:03d}") for index in range(10)],
        *[_edge(f"FD-{index:03d}", "AD-100") for index in range(10)],
        _edge("AD-100", "TD-900"),
    )
    result = await traverse("CUST-001", graph, max_paths=4)
    assert result.node("TD-900").paths_truncated is True


@pytest.mark.asyncio
async def test_an_untruncated_node_says_so() -> None:
    graph = _Graph(_edge("CUST-001", "FD-010"))
    result = await traverse("CUST-001", graph)
    assert result.node("FD-010").paths_truncated is False


@pytest.mark.asyncio
async def test_a_non_positive_path_cap_is_refused() -> None:
    with pytest.raises(ValueError, match="max_paths must be positive"):
        await traverse("CUST-001", _Graph(), max_paths=0)


def test_the_default_cap_is_generous_enough_for_a_realistic_cycle() -> None:
    assert DEFAULT_MAX_PATHS >= 32


# ---------------------------------------------------------------------------
# Accessors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_asking_for_a_node_outside_the_set_is_an_error_not_a_default() -> None:
    result = await traverse("CUST-001", _Graph(_edge("CUST-001", "FD-010")))
    with pytest.raises(KeyError, match="AD-100"):
        result.node("AD-100")


def test_an_empty_set_reports_zero_depth_rather_than_failing() -> None:
    assert ImpactSet(seed_id="CUST-001", nodes=()).node_count == 0


def test_a_path_of_one_node_has_no_depth() -> None:
    assert ImpactPath(nodes=("CUST-001",)).depth == 0


# ---------------------------------------------------------------------------
# The stored shape — these types are persisted and exposed, so the
# constraints that keep a stored impact set readable are part of the model
# ---------------------------------------------------------------------------


def test_an_edge_with_a_blank_identifier_cannot_exist() -> None:
    with pytest.raises(ValidationError):
        ImpactEdge(source_id="CUST-001", node_id="", container="arch")


def test_an_edge_cannot_pin_a_version_below_one() -> None:
    with pytest.raises(ValidationError):
        ImpactEdge(
            source_id="CUST-001", node_id="FD-010", container="arch", pinned_version=0
        )


def test_an_unknown_field_on_a_stored_edge_is_refused() -> None:
    """A row that grew a field is a schema drift, not a tolerable extra."""
    with pytest.raises(ValidationError):
        ImpactEdge.model_validate(
            {
                "source_id": "CUST-001",
                "node_id": "FD-010",
                "container": "arch",
                "qualification": "no-effect",
            }
        )


def test_an_edge_is_frozen_and_hashable_so_the_walk_can_dedupe_on_it() -> None:
    edge = _edge("CUST-001", "FD-010")
    assert len({edge, _edge("CUST-001", "FD-010")}) == 1
    with pytest.raises(ValidationError):
        edge.node_id = "AD-100"


@pytest.mark.asyncio
async def test_an_impact_set_round_trips_through_its_serialised_form() -> None:
    """The ticket persists this, so the round trip is part of the contract."""
    graph = _Graph(
        _edge("CUST-001", "FD-010", "functional"),
        _edge("FD-010", "AD-100", "architecture", version=4),
    )
    result = await traverse("CUST-001", graph)
    assert ImpactSet.model_validate(result.model_dump()) == result
